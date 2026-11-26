"""Phone access: who counts as this computer, the access code, the session cookie, the login limits, the gate in front
of every page and WebSocket, the home-screen tags, and the Tailscale helpers. Plain ASGI, so no Streamlit needed."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys

import pytest

from riskaware import phone

PHONE_IP = "100.101.102.103"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Each test starts with its own settings file, no failed logins and the dashboard listening on 127.0.0.1."""
    monkeypatch.setattr(phone, "_path", lambda: tmp_path / "phone.json")
    monkeypatch.setattr(phone, "_state", None)
    monkeypatch.setattr(phone, "_mtime", None)
    monkeypatch.setattr(phone, "sync_keep_awake", lambda: False)
    monkeypatch.setattr(phone, "_maybe_sync_keep_awake", lambda: None)
    monkeypatch.setenv("RISKAWARE_DASHBOARD_HOST", "127.0.0.1")
    monkeypatch.delenv("CODESPACES", raising=False)
    monkeypatch.delenv("RISKAWARE_CONTAINER", raising=False)
    phone._fails.clear()
    phone._all_fails.clear()
    yield


# --------------------------------------------------------------------------- a tiny ASGI client
async def _inner(scope, receive, send):
    """Stands in for Streamlit: answers pages with HTML and says what the gate let through."""
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        return
    hs = dict(scope["headers"])
    body = json.dumps({"path": scope["path"], "local": hs.get(phone.LOCAL_MARK.encode(), b"").decode(),
                       "gzip": b"accept-encoding" in hs}).encode()
    if "text/html" in hs.get(b"accept", b"").decode():
        body = b"<html><head><title>x</title></head><body>" + body + b"</body></html>"
        ctype = b"text/html"
    else:
        ctype = b"application/json"
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def call(path="/", method="GET", client=PHONE_IP, headers=None, body=b"", scheme="http", page=False):
    hs = {"host": "localhost:8501" if phone._loopback(client) else "mac.tail.ts.net"}
    if page:
        hs["accept"] = "text/html"
        hs["accept-encoding"] = "gzip"
    hs.update(headers or {})
    path_only, _, qs = path.partition("?")
    scope = {"type": "http", "method": method, "path": path_only, "query_string": qs.encode(), "scheme": scheme,
             "client": (client, 50000), "headers": [(k.encode(), v.encode()) for k, v in hs.items()]}
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m):
        sent.append(m)
    asyncio.run(phone.PhoneGate(_inner)(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    out = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, out


def ws(client=PHONE_IP, headers=None):
    hs = {"host": "localhost:8501" if phone._loopback(client) else "mac.tail.ts.net"}
    hs.update(headers or {})
    scope = {"type": "websocket", "path": "/_stcore/stream", "client": (client, 50000),
             "headers": [(k.encode(), v.encode()) for k, v in hs.items()]}
    sent = []
    msgs = [{"type": "websocket.connect"}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "websocket.disconnect"}

    async def send(m):
        sent.append(m)
    asyncio.run(phone.PhoneGate(_inner)(scope, receive, send))
    return sent[0]["type"]


def login(code=None, client=PHONE_IP, headers=None, nxt="/"):
    data = json.dumps({"code": phone.access_code() if code is None else code, "next": nxt}).encode()
    return call("/login", "POST", client, {"content-type": "application/json", **(headers or {})}, data)


def cookie_of(headers: dict) -> str:
    return headers["set-cookie"].split(";")[0]


# --------------------------------------------------------------------------- settings
def test_settings_file_is_private_and_off_by_default(tmp_path):
    assert phone.enabled() is False and phone.keep_awake_wanted() is True
    code = phone.access_code()
    assert len(phone._norm(code)) == 8 and code[4] == "-" and not set(phone._norm(code)) & set("01OI")
    data = json.loads((tmp_path / "phone.json").read_text())
    assert data["code"] == code and len(bytes.fromhex(data["secret"])) == 32
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "phone.json").stat().st_mode) == 0o600


def test_switch_written_elsewhere_reaches_a_running_dashboard(tmp_path):
    assert phone.enabled() is False
    data = json.loads((tmp_path / "phone.json").read_text())
    data["enabled"] = True
    (tmp_path / "phone.json").write_text(json.dumps(data))
    os.utime(tmp_path / "phone.json", ns=(1, 10**18))   # a different mtime, as another process would leave
    assert phone.enabled() is True


def test_codes_compare_loosely_but_exactly():
    code = phone.access_code()
    assert phone.check_code(code) and phone.check_code(code.lower().replace("-", " "))
    assert not phone.check_code("") and not phone.check_code(code[:-1]) and not phone.check_code(None)


def test_new_code_and_sign_out_replace_the_signing_key():
    s = phone.make_session()
    assert phone.session_age(s) is not None
    phone.sign_out_all()
    assert phone.session_age(s) is None
    old = phone.access_code()
    s = phone.make_session()
    assert phone.new_access_code() != old and phone.session_age(s) is None


def test_sessions_expire_and_cannot_be_forged():
    now = 1_800_000_000
    s = phone.make_session(now)
    assert phone.session_age(s, now + 10) == 10
    assert phone.session_age(s, now + phone.SESSION_DAYS * 86400 + 1) is None
    body, sig = s.split(".")
    forged = phone._b64(json.dumps({"iat": now, "exp": now + 10**9}).encode())
    assert phone.session_age(forged + "." + sig, now) is None
    assert phone.session_age("junk", now) is None and phone.session_age(None, now) is None


# --------------------------------------------------------------------------- who is asking
@pytest.mark.parametrize("client,headers,local", [
    ("127.0.0.1", {"host": "localhost:8501"}, True),
    ("::1", {"host": "[::1]:8501"}, True),
    ("::ffff:127.0.0.1", {"host": "127.0.0.1:8501"}, True),
    ("127.0.0.1", {}, True),
    ("127.0.0.1", {"host": "mac.tail.ts.net", "x-forwarded-for": PHONE_IP}, False),      # Tailscale Serve
    ("127.0.0.1", {"host": "localhost:8501", "x-forwarded-for": "1.2.3.4"}, False),      # any local proxy
    ("127.0.0.1", {"host": "localhost:8501", "tailscale-user-login": "me@x"}, False),
    ("127.0.0.1", {"host": "evil.example:8501"}, False),                                 # DNS rebinding
    ("192.168.1.20", {"host": "localhost:8501"}, False),
])
def test_is_local(client, headers, local):
    assert phone.is_local(client, headers) is local


def test_the_hosts_browser_counts_as_local_in_docker(monkeypatch):
    monkeypatch.setattr(phone, "in_container", lambda: True)
    monkeypatch.setattr(phone, "_gateway", lambda: "172.17.0.1")
    assert phone.is_local("172.17.0.1", {"host": "localhost:8501"})
    assert not phone.is_local("172.17.0.5", {"host": "localhost:8501"})
    assert not phone.is_local("172.17.0.1", {"host": "localhost:8501", "x-forwarded-for": PHONE_IP})


def test_container_gateway_reads_the_default_route(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway \tFlags\n"
                     "eth0\t00000000\t010011AC\t0003\n"
                     "eth0\t000011AC\t00000000\t0001\n")
    assert phone.container_gateway(str(route)) == "172.17.0.1"
    assert phone.container_gateway(str(tmp_path / "missing")) == ""


def test_default_host_and_network_opening(monkeypatch):
    assert phone.default_host() == "127.0.0.1" and not phone.opened_to_network()
    monkeypatch.setenv("RISKAWARE_DASHBOARD_HOST", "0.0.0.0")
    assert phone.opened_to_network()
    monkeypatch.setenv("RISKAWARE_DASHBOARD_HOST", "127.0.0.1")
    monkeypatch.setenv("CODESPACES", "true")
    assert phone.opened_to_network()


def test_client_key_trusts_only_the_last_forwarded_hop():
    assert phone._client_key(PHONE_IP, {"x-forwarded-for": "9.9.9.9"}) == PHONE_IP
    assert phone._client_key("127.0.0.1", {"x-forwarded-for": "6.6.6.6, " + PHONE_IP}) == PHONE_IP
    assert phone._client_key("127.0.0.1", {}) == "127.0.0.1"


@pytest.mark.parametrize("value,expected", [
    ("/agents?x=1", "/agents?x=1"), (None, "/"), ("https://evil.example/", "/"), ("//evil.example", "/"),
    ("/\tevil", "/"), ("/\\evil.example", "/"), ("/login", "/"), ("agents", "/"),
])
def test_safe_next(value, expected):
    assert phone._safe_next(value) == expected


# --------------------------------------------------------------------------- the gate, phone access off
def test_this_computer_passes_and_is_marked_local():
    status, _, body = call(client="127.0.0.1")
    assert status == 200 and json.loads(body)["local"] == "1"


def test_a_forged_local_mark_is_dropped(monkeypatch):
    monkeypatch.setenv("RISKAWARE_DASHBOARD_HOST", "0.0.0.0")
    status, _, body = call(headers={phone.LOCAL_MARK: "1"})
    assert status == 200 and json.loads(body)["local"] == "0"


def test_off_refuses_tunnels_when_listening_on_loopback():
    status, _, body = call(client="127.0.0.1", headers={"x-forwarded-for": PHONE_IP, "host": "mac.tail.ts.net"},
                           page=True)
    assert status == 403 and "riskaware phone on" in body.decode()
    assert call(client="127.0.0.1", headers={"x-forwarded-for": PHONE_IP})[0] == 403
    assert ws(client="127.0.0.1", headers={"x-forwarded-for": PHONE_IP}) == "websocket.close"
    assert ws(client="127.0.0.1") == "websocket.accept"


def test_off_keeps_the_old_behaviour_where_the_network_was_opened_on_purpose(monkeypatch):
    monkeypatch.setenv("RISKAWARE_DASHBOARD_HOST", "0.0.0.0")
    assert call()[0] == 200 and ws() == "websocket.accept"
    status, headers, _ = call("/login", page=True)
    assert status == 303 and headers["location"] == "/"


# --------------------------------------------------------------------------- the gate, phone access on
def test_on_sends_a_phone_to_the_login_page_and_lets_it_in_with_the_code():
    phone.set_enabled(True)
    status, headers, _ = call("/agents?x=1", page=True)
    assert status == 303 and headers["location"] == "/login?next=%2Fagents%3Fx%3D1"
    assert call("/_stcore/host-config")[0] == 401
    assert ws() == "websocket.close"
    status, headers, body = call("/login", page=True)
    assert status == 200 and b"XXXX-XXXX" in body and headers["cache-control"] == "no-store"

    status, headers, body = login(nxt="/agents?x=1")
    assert status == 200 and json.loads(body) == {"ok": True, "next": "/agents?x=1"}
    set_cookie = headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie and "Secure" not in set_cookie
    jar = {"cookie": cookie_of(headers)}
    status, _, body = call(headers=jar)
    assert status == 200 and json.loads(body)["local"] == "0"
    assert ws(headers=jar) == "websocket.accept"


def test_the_cookie_is_secure_over_https():
    phone.set_enabled(True)
    _, headers, _ = login(client="127.0.0.1", headers={"x-forwarded-for": PHONE_IP, "x-forwarded-proto": "https"})
    assert "Secure" in headers["set-cookie"]


def test_this_computer_never_logs_in():
    phone.set_enabled(True)
    assert call(client="127.0.0.1")[0] == 200
    status, headers, _ = call("/login", client="127.0.0.1", page=True)
    assert status == 303 and headers["location"] == "/"


def test_wrong_codes_wait_per_device_then_for_everyone():
    phone.set_enabled(True)
    for _ in range(phone.PER_DEVICE_LIMIT[0]):
        assert login("AAAA-AAAA")[0] == 401
    status, _, body = login()
    assert status == 429 and json.loads(body)["retry_after"] > 0
    # another device is still welcome until the overall limit
    assert login(client="100.64.0.9")[0] == 200
    for i in range(phone.GLOBAL_LIMIT[0]):
        login("AAAA-AAAA", client=f"100.64.1.{i}")
    assert login(client="100.64.2.1")[0] == 429


def test_a_new_code_signs_every_phone_out():
    phone.set_enabled(True)
    jar = {"cookie": cookie_of(login()[1])}
    assert call(headers=jar)[0] == 200
    phone.new_access_code()
    assert call(headers=jar)[0] == 401


def test_logout_clears_the_cookie():
    phone.set_enabled(True)
    status, headers, _ = call("/logout")
    assert status == 303 and headers["location"] == "/login" and "Max-Age=0" in headers["set-cookie"]


def test_login_refuses_when_off_and_oversized_bodies():
    assert login()[0] == 403
    phone.set_enabled(True)
    big = json.dumps({"code": phone.access_code(), "pad": "x" * 5000}).encode()
    assert call("/login", "POST", body=big)[0] == 401


def test_an_old_cookie_is_renewed():
    phone.set_enabled(True)
    old = phone.make_session(__import__("time").time() - 8 * 86400)
    status, headers, _ = call(headers={"cookie": f"{phone.COOKIE}={old}"})
    assert status == 200 and headers.get("set-cookie", "").startswith(phone.COOKIE + "=")
    fresh = phone.make_session()
    assert "set-cookie" not in call(headers={"cookie": f"{phone.COOKIE}={fresh}"})[1]


# --------------------------------------------------------------------------- home screen
def test_pages_get_the_home_screen_tags_uncompressed():
    status, headers, body = call(client="127.0.0.1", page=True)
    assert status == 200 and b'rel="manifest"' in body and b"apple-mobile-web-app-capable" in body
    assert headers["content-length"] == str(len(body))
    assert b'"gzip": false' in body
    # files and Streamlit's own routes pass untouched
    _, _, body = call("/_stcore/health", client="127.0.0.1", page=True)
    assert b'rel="manifest"' not in body and b'"gzip": true' in body


def test_manifest_and_icons_are_public():
    phone.set_enabled(True)
    status, headers, body = call(phone.MANIFEST)
    assert status == 200 and json.loads(body)["display"] == "standalone"
    for path in phone.ICONS:
        status, headers, body = call(path)
        assert status == 200 and headers["content-type"] == "image/png" and body[:4] == b"\x89PNG"


def test_inject_head_once():
    html = b"<html><head></head><body></body></html>"
    once = phone.inject_head(html)
    assert once.count(b'rel="manifest"') == 1 and phone.inject_head(once) == once
    assert phone.inject_head(b"no head") == b"no head"


# --------------------------------------------------------------------------- Tailscale, QR, keep awake
def test_parse_serve_finds_this_dashboard():
    cfg = {"Web": {"mac.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8501"}}}},
           "AllowFunnel": {"mac.tail.ts.net:443": True}}
    assert phone.parse_serve(cfg, 8501) == {"serve": True, "url": "https://mac.tail.ts.net", "funnel": True, "busy": ""}
    other = phone.parse_serve(cfg, 8600)
    assert other["serve"] is False and other["busy"] == "http://127.0.0.1:8501"
    assert phone.parse_serve({}, 8501)["serve"] is False
    cfg = {"Web": {"mac.tail.ts.net:8443": {"Handlers": {"/": {"Proxy": "localhost:8501"}}}}}
    assert phone.parse_serve(cfg, 8501)["url"] == "https://mac.tail.ts.net:8443"


def test_serve_command_and_login_link():
    assert phone.serve_command(8501, exe="tailscale") == ["tailscale", "serve", "--bg", "--yes", "--https=443",
                                                          "http://127.0.0.1:8501"]
    assert phone.serve_command(8501, False, exe="tailscale") == ["tailscale", "serve", "--https=443", "off"]
    text = "To enable, visit:\n\n         https://login.tailscale.com/f/serve?node=abc123.\n"
    assert phone._login_link(text) == "https://login.tailscale.com/f/serve?node=abc123"


def test_qr_link_keeps_the_code_out_of_the_request():
    link = phone.login_link("https://mac.tail.ts.net/")
    assert link == "https://mac.tail.ts.net/login#code=" + phone.access_code()
    assert phone.login_link("") == ""


def test_keep_awake_command():
    cmd = phone.keep_awake_command(1234)
    if sys.platform == "darwin":
        assert cmd is not None and cmd[0] == "caffeinate" and cmd[-1] == "1234"
    elif sys.platform == "win32":
        assert cmd is None
