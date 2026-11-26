"""Phone access (手机访问): open the dashboard on your phone, at home or away, while it runs on this computer.

How it fits together
--------------------
* ``riskaware dashboard`` listens on 127.0.0.1 (on 0.0.0.0 inside the Docker image). The phone reaches it through
  Tailscale: ``tailscale serve`` publishes https://<computer>.<tailnet>.ts.net inside your own tailnet and forwards it
  to 127.0.0.1:<port>, so nothing is opened to the internet. On the same Wi-Fi, ``--host 0.0.0.0`` works too (http).
* :class:`PhoneGate` stands in front of every page, file, API call and WebSocket of the dashboard: Streamlit's
  ``st.App`` takes it as middleware (``dashboard/riskaware_asgi.py``). A request is *local* (a browser on this
  computer) or *remote* (anything that came over the network or through a proxy or tunnel, even one running on this
  computer). Local requests never log in.
* Phone access off (the default): remote requests are answered only where the dashboard was opened to the network
  on purpose (``--host 0.0.0.0``, the Docker image, GitHub Codespaces), as before; anything else gets a page that
  says how to turn phone access on.
* Phone access on: a remote device logs in once with the access code (or by scanning the QR code on the dashboard's
  Phone page) and keeps a signed, HttpOnly cookie for 180 days, renewed while it is used. The Phone page, which
  shows the code, opens only on this computer.

The switch, the access code and the key that signs the cookies live in ``$RISKAWARE_HOME/phone.json`` (mode 600). A new
code, or "sign out every phone", replaces the key, so every cookie issued before stops working. Nothing here needs
Streamlit: the gate is plain ASGI, so the CLI and the tests use this module without the dashboard extra.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

COOKIE = "riskaware_phone"
SESSION_DAYS = 180
RENEW_AFTER_DAYS = 7          # a phone in use gets a fresh 180 days once a week
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Headers that reverse proxies and tunnels add (Tailscale Serve, Cloudflare Tunnel, ngrok, nginx, Caddy, Codespaces).
# A request carrying any of them came through a proxy, even one running on this computer.
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip",
                 "cf-connecting-ip", "true-client-ip", "tailscale-user-login", "tailscale-funnel-request")
#: Set by the gate on every request it lets through: "1" from this computer, "0" from a phone. A header of this name
#: sent by the browser is dropped first, so the dashboard can trust it (``is_local_session``).
LOCAL_MARK = "x-riskaware-local"
LOGIN, LOGOUT, MANIFEST = "/login", "/logout", "/manifest.webmanifest"
ICONS = {"/phone-icon-192.png": "icon-192.png", "/phone-icon-512.png": "icon-512.png",
         "/apple-touch-icon.png": "apple-touch-icon.png"}
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # no 0/O or 1/I to confuse
CODE_LEN = 8
PER_DEVICE_LIMIT = (5, 600)   # 5 wrong codes in 10 minutes: that device waits
GLOBAL_LIMIT = (20, 900)      # 20 wrong codes in 15 minutes from anywhere: every login waits
ASSETS = Path(__file__).resolve().parent / "dashboard" / "phone_assets"
TS_DOWNLOAD = "https://tailscale.com/download"

#: Whether the dashboard runs behind the gate (set by dashboard/riskaware_asgi.py). Without it, as with a Streamlit
#: older than 1.59, the dashboard cannot tell a phone from this computer, so the Phone page explains how to upgrade.
GATE = {"active": False}

_lock = threading.Lock()
_state: dict | None = None
_mtime: int | None = None


# --------------------------------------------------------------------------- settings
def _path() -> Path:
    from . import config

    return config.RISKAWARE_HOME / "phone.json"


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    return raw[:4] + "-" + raw[4:]


def _norm(code) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(code or "").upper())


def _stamp(p: Path) -> int | None:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return None


def _save(data: dict) -> None:
    """Write atomically, readable only by you (the file holds the code and the signing key)."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
    os.replace(tmp, p)


def _load() -> dict:
    """The settings, created on first use. Read again when the file changes, so `riskaware phone on` in a terminal
    reaches a dashboard that is already running."""
    global _state, _mtime
    p = _path()
    with _lock:
        stamp = _stamp(p)
        if _state is not None and stamp is not None and stamp == _mtime:
            return _state
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        changed = False
        if len(_norm(data.get("code"))) != CODE_LEN:
            data["code"], changed = _new_code(), True
        try:
            if len(bytes.fromhex(str(data.get("secret") or ""))) < 32:
                raise ValueError
        except ValueError:
            data["secret"], changed = secrets.token_hex(32), True
        for key, default in (("enabled", False), ("keep_awake", True)):
            if not isinstance(data.get(key), bool):
                data[key], changed = default, True
        if changed:
            _save(data)
        _state, _mtime = data, _stamp(p)
        return _state


def _update(**changes) -> dict:
    global _state, _mtime
    data = {**_load(), **changes}
    with _lock:
        _save(data)
        _state, _mtime = data, _stamp(_path())
    return data


def enabled() -> bool:
    return bool(_load()["enabled"])


def set_enabled(on: bool) -> None:
    _update(enabled=bool(on))
    sync_keep_awake()


def keep_awake_wanted() -> bool:
    return bool(_load()["keep_awake"])


def set_keep_awake(on: bool) -> None:
    _update(keep_awake=bool(on))
    sync_keep_awake()


def access_code() -> str:
    return _load()["code"]


def new_access_code() -> str:
    """A new code and a new signing key: every phone has to log in again with the new code."""
    with _lock:
        _fails.clear()
        _all_fails.clear()
    return _update(code=_new_code(), secret=secrets.token_hex(32))["code"]


def sign_out_all() -> None:
    """A new signing key with the same code: every phone has to log in again."""
    _update(secret=secrets.token_hex(32))


def check_code(code) -> bool:
    given = _norm(code)
    return len(given) == CODE_LEN and hmac.compare_digest(given, _norm(access_code()))


# --------------------------------------------------------------------------- sessions
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    key = bytes.fromhex(_load()["secret"])
    return _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


def make_session(now: float | None = None) -> str:
    now = int(now if now is not None else time.time())
    body = _b64(json.dumps({"iat": now, "exp": now + SESSION_DAYS * 86400}, separators=(",", ":")).encode())
    return body + "." + _sign(body)


def session_age(value, now: float | None = None) -> float | None:
    """Seconds since the session cookie was issued; None when it is missing, forged or expired."""
    try:
        body, sig = str(value or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        data = json.loads(_unb64(body))
        now = time.time() if now is None else now
        if not (float(data["iat"]) - 300 <= now < float(data["exp"])):
            return None
        return max(0.0, now - float(data["iat"]))
    except Exception:
        return None


def _cookie(headers: dict, name: str) -> str:
    for part in (headers.get("cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip().strip('"')
    return ""


def _https(scheme: str, headers: dict) -> bool:
    if scheme in ("https", "wss"):
        return True
    if (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() == "https":
        return True
    return "proto=https" in (headers.get("forwarded") or "").lower()


def session_cookie(value: str, secure: bool) -> str:
    return (f"{COOKIE}={value}; Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else ""))


# --------------------------------------------------------------------------- who is asking
def in_container() -> bool:
    """The Docker image sets RISKAWARE_CONTAINER=1; the marker file makes sure it really is a container."""
    return os.environ.get("RISKAWARE_CONTAINER") == "1" and any(
        os.path.exists(p) for p in ("/.dockerenv", "/run/.containerenv"))


def container_gateway(route_file: str = "/proc/net/route") -> str:
    """The container's default gateway, i.e. Docker's bridge address. A browser on the host that opens a port
    published with -p arrives from this address."""
    import struct

    try:
        rows = Path(route_file).read_text(encoding="ascii").splitlines()[1:]
    except (OSError, ValueError):
        return ""
    for row in rows:
        f = row.split()
        try:
            if len(f) > 3 and f[1] == "00000000" and int(f[3], 16) & 0x2:   # the default route, via a gateway
                return socket.inet_ntoa(struct.pack("=L", int(f[2], 16)))  # the kernel prints it in host order
        except (ValueError, OSError, struct.error):
            continue
    return ""


_gateway_cache: list[str] = []


def _gateway() -> str:
    if not _gateway_cache:
        _gateway_cache.append(container_gateway())
    return _gateway_cache[0]


def _host_only(value: str) -> str:
    v = (value or "").split(",")[0].strip().lower()
    if v.startswith("["):
        return v[1:v.find("]")] if "]" in v else v[1:]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def _loopback(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def is_local(client: str, headers: dict) -> bool:
    """A browser or program on this computer: a loopback address, addressed to localhost, through no proxy. The Host
    check also turns away DNS rebinding, where a web page renames itself to 127.0.0.1."""
    if any(h in headers for h in PROXY_HEADERS):
        return False
    host = _host_only(headers.get("host", ""))
    if host and host not in LOCAL_HOSTS:
        return False
    if _loopback(client):
        return True
    # In the Docker image, a browser on the host reaches the published port from the bridge gateway.
    return in_container() and bool(_gateway()) and client == _gateway()


def listen_host() -> str:
    """The address the dashboard listens on (passed down by `riskaware dashboard`)."""
    host = os.environ.get("RISKAWARE_DASHBOARD_HOST")
    if not host:
        try:
            from streamlit import config as st_config

            host = st_config.get_option("server.address")
        except Exception:
            host = None
    return str(host or "127.0.0.1")


def default_host() -> str:
    """127.0.0.1, so only this computer can open the dashboard; 0.0.0.0 in the Docker image, where the host's browser
    arrives from outside the container."""
    return "0.0.0.0" if in_container() else "127.0.0.1"


def opened_to_network() -> bool:
    """Listening beyond this computer on purpose, or in GitHub Codespaces, whose port forwarding is a proxy."""
    return listen_host() not in ("127.0.0.1", "localhost", "::1") or os.environ.get("CODESPACES") == "true"


def is_local_session(headers) -> bool:
    """For the dashboard: whether the browser of this session is on this computer, as the gate marked it."""
    try:
        return str(headers.get(LOCAL_MARK) or "") == "1"
    except Exception:
        return False


def _client_key(client: str, headers: dict) -> str:
    """Which device is trying codes. A device on the network is its own address, whatever headers it sends. Through a
    proxy on this computer, the address the proxy saw is the last X-Forwarded-For entry (Tailscale Serve replaces the
    header; anything earlier could come from the device itself)."""
    if not _loopback(client):
        return client or "?"
    fwd = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    return fwd[-1] if fwd else (client or "?")


def _wants_page(method: str, headers: dict) -> bool:
    if method not in ("GET", "HEAD"):
        return False
    if headers.get("sec-fetch-mode") == "navigate" or headers.get("sec-fetch-dest") in ("document", "iframe"):
        return True
    return "text/html" in (headers.get("accept") or "")


def _safe_next(value) -> str:
    """Where to go after logging in: a path on this site, never another site. Browsers drop tabs and line breaks from
    URLs and read a backslash as a slash, so "/<tab>/evil.example" would leave."""
    v = str(value or "/")
    if (not v.startswith("/") or v.startswith(LOGIN) or "\\" in v
            or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in v)):
        return "/"
    u = urlparse(v)
    return "/" if (u.scheme or u.netloc or v.startswith("//")) else v


# --------------------------------------------------------------------------- wrong codes
_fails: dict[str, list[float]] = {}
_all_fails: list[float] = []


def _retry_after(key: str, now: float) -> int:
    n, win = PER_DEVICE_LIMIT
    if len(_fails) > 1000:                # many devices: forget the ones whose wait is over
        for k in [k for k, ts in _fails.items() if not ts or now - ts[-1] >= win]:
            del _fails[k]
    mine = [t for t in _fails.get(key, []) if now - t < win]
    if mine:
        _fails[key] = mine
    else:
        _fails.pop(key, None)
    if len(mine) >= n:
        return int(mine[0] + win - now) + 1
    gn, gwin = GLOBAL_LIMIT
    _all_fails[:] = [t for t in _all_fails if now - t < gwin]
    if len(_all_fails) >= gn:
        return int(_all_fails[0] + gwin - now) + 1
    return 0


def try_login(key: str, code, now: float | None = None) -> tuple[int, dict]:
    """(status, body) for a login attempt from device ``key``. Nothing in here awaits, so attempts sent at the same
    moment are counted one after another and cannot all slip past the limit together."""
    now = time.time() if now is None else now
    wait = _retry_after(key, now)
    if wait:
        return 429, {"error": "too_many", "retry_after": wait}
    if not check_code(code):
        _fails.setdefault(key, []).append(now)
        _all_fails.append(now)
        return 401, {"error": "wrong_code"}
    _fails.pop(key, None)
    return 200, {"ok": True}


# --------------------------------------------------------------------------- the gate (plain ASGI)
def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def _marked(scope, local: bool) -> dict:
    mark = LOCAL_MARK.encode("latin-1")
    kept = [(k, v) for k, v in scope.get("headers") or [] if k.lower() != mark]
    return {**scope, "headers": kept + [(mark, b"1" if local else b"0")]}


async def _respond(send, status: int, body: bytes | str = b"", ctype: str = "application/json",
                   extra: list[tuple[str, str]] | None = None, head: bool = False) -> None:
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = [(b"content-type", ctype.encode("latin-1")), (b"content-length", str(len(data)).encode()),
               (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
    headers += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in extra or []]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else data})


async def _json(send, status: int, obj: dict, extra=None) -> None:
    await _respond(send, status, json.dumps(obj), "application/json", extra)


async def _redirect(send, location: str, extra=None) -> None:
    await _respond(send, 303, b"", "text/plain", [("location", location), *(extra or [])])


async def _refuse_ws(receive, send) -> None:
    msg = await receive()
    if msg.get("type") == "websocket.connect":
        await send({"type": "websocket.close", "code": 1008})


async def _read_body(receive, limit: int = 4096) -> bytes | None:
    body, more = b"", True
    while more:
        msg = await receive()
        if msg.get("type") == "http.disconnect":
            return None
        body += msg.get("body", b"")
        more = bool(msg.get("more_body"))
        if len(body) > limit:
            return None
    return body


def _asset(name: str) -> bytes:
    return (ASSETS / name).read_bytes()


def manifest() -> dict:
    return {"name": "Risk-Aware Trading Agents", "short_name": "RTA", "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": "#0a0e13", "theme_color": "#0a0e13",
            "icons": [{"src": "/phone-icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/phone-icon-512.png", "sizes": "512x512", "type": "image/png"}]}


#: Added to the dashboard's HTML so a phone can add it to the home screen as an app (full screen, its own icon).
HEAD_TAGS = ('<link rel="manifest" href="/manifest.webmanifest">'
             '<meta name="theme-color" content="#0a0e13">'
             '<meta name="apple-mobile-web-app-capable" content="yes">'
             '<meta name="mobile-web-app-capable" content="yes">'
             '<meta name="apple-mobile-web-app-title" content="RTA">'
             '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
             '<link rel="apple-touch-icon" href="/apple-touch-icon.png">')
_STREAMLIT_PREFIXES = ("/_stcore", "/static", "/media", "/component", "/app/static")


def inject_head(html: bytes) -> bytes:
    if b'rel="manifest"' in html or b"</head>" not in html:
        return html
    return html.replace(b"</head>", HEAD_TAGS.encode("utf-8") + b"</head>", 1)


def _page_html(title_en: str, title_zh: str, body_en: str, body_zh: str) -> str:
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=robots content=noindex><title>Risk-Aware Trading Agents</title>"
            "<body style='background:#0a0e13;color:#e6edf3;font:15px/1.6 -apple-system,BlinkMacSystemFont,"
            "\"PingFang SC\",sans-serif;padding:28px;max-width:560px;margin:auto'>"
            f"<h3>🔒 {title_zh}</h3><p>{body_zh}</p><h3 style='margin-top:28px'>{title_en}</h3><p>{body_en}</p>")


OFF_PAGE = _page_html(
    "Phone access is off", "手机访问已关闭",
    "Turn it on on the computer that runs the dashboard: open the dashboard there and go to <b>Phone</b>, or run "
    "<code>riskaware phone on</code>. Then scan the QR code it shows.",
    "请在运行看板的电脑上打开：在电脑的看板里进入 <b>手机</b> 页，或运行 <code>riskaware phone on</code>，"
    "然后用手机扫它显示的二维码。")


class PhoneGate:
    """Decides who may use the dashboard, for pages, files, API calls and WebSockets alike."""

    def __init__(self, app):
        self.app = app
        sync_keep_awake()

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = _headers(scope)
        client = (scope.get("client") or ("",))[0] or ""
        path = scope.get("path") or "/"
        method = scope.get("method", "GET")
        local = is_local(client, headers)
        scope = _marked(scope, local)
        _maybe_sync_keep_awake()
        if kind == "http" and path in (LOGIN, LOGOUT, MANIFEST, *ICONS):
            return await self._own(scope, receive, send, path, method, headers, client, local)
        if not local:
            if not enabled():
                if not opened_to_network():
                    if kind == "websocket":
                        return await _refuse_ws(receive, send)
                    if _wants_page(method, headers):
                        return await _respond(send, 403, OFF_PAGE, "text/html; charset=utf-8")
                    return await _json(send, 403, {"error": "Phone access is off"})
            else:
                age = session_age(_cookie(headers, COOKIE))
                if age is None:
                    if kind == "websocket":
                        return await _refuse_ws(receive, send)
                    if _wants_page(method, headers):
                        qs = scope.get("query_string", b"").decode("latin-1")
                        nxt = path + ("?" + qs if qs else "")
                        return await _redirect(send, LOGIN + ("" if nxt == "/" else "?next=" + quote(nxt, safe="")))
                    return await _json(send, 401, {"error": "Log in first: open /login on this device",
                                                   "login": LOGIN}, [("x-riskaware-login", LOGIN)])
                if kind == "http" and age > RENEW_AFTER_DAYS * 86400:
                    send = _with_header(send, "set-cookie",
                                        session_cookie(make_session(), _https(scope.get("scheme", ""), headers)))
        if kind == "http" and method == "GET" and _wants_page(method, headers) \
                and not path.startswith(_STREAMLIT_PREFIXES):
            return await self._page(scope, receive, send)
        return await self.app(scope, receive, send)

    async def _page(self, scope, receive, send):
        """Pass a page through with the home-screen tags added. Asked for uncompressed, so the HTML can be edited."""
        scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k.lower() != b"accept-encoding"]}
        start: dict = {}
        chunks: list[bytes] = []

        async def edit(message):
            if message["type"] == "http.response.start":
                hs = {k.lower(): v for k, v in message.get("headers") or []}
                if b"text/html" in hs.get(b"content-type", b"") and b"content-encoding" not in hs:
                    start.update(message)
                    return
                return await send(message)
            if message["type"] == "http.response.body" and start:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = inject_head(b"".join(chunks))
                hdrs = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
                await send({**start, "headers": hdrs + [(b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
            return await send(message)

        return await self.app(scope, receive, edit)

    async def _own(self, scope, receive, send, path, method, headers, client, local):
        if path == MANIFEST:
            return await _respond(send, 200, json.dumps(manifest()), "application/manifest+json")
        if path in ICONS:
            return await _respond(send, 200, _asset(ICONS[path]), "image/png", head=method == "HEAD")
        if path == LOGOUT:
            gone = f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"
            return await _redirect(send, "/" if local or not enabled() else LOGIN, [("set-cookie", gone)])
        # /login
        if method in ("GET", "HEAD"):
            if local or not enabled():
                return await _redirect(send, "/")
            return await _respond(send, 200, _asset("login.html"), "text/html; charset=utf-8", head=method == "HEAD")
        if method != "POST":
            return await _json(send, 405, {"error": "method"})
        if not enabled():
            return await _json(send, 403, {"error": "off"})
        raw = await _read_body(receive)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        status, out = try_login(_client_key(client, headers), body.get("code"))
        if status != 200:
            return await _json(send, status, out)
        cookie = session_cookie(make_session(), _https(scope.get("scheme", ""), headers))
        return await _json(send, 200, {"ok": True, "next": _safe_next(body.get("next"))}, [("set-cookie", cookie)])


def _with_header(send, name: str, value: str):
    extra = (name.encode("latin-1"), value.encode("latin-1"))

    async def wrapped(message):
        if message.get("type") == "http.response.start":
            message = {**message, "headers": list(message.get("headers") or []) + [extra]}
        await send(message)
    return wrapped


# --------------------------------------------------------------------------- Tailscale
def _ts_bin() -> str:
    for c in (shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if c and os.path.isfile(c):
            return c
    return ""


def _run(args: list[str], timeout: float = 8) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        def dec(x):
            return (x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or ""))
        return -1, dec(e.stdout), dec(e.stderr)
    except OSError as e:
        return -2, "", str(e)


def _targets(proxy: str, port: int) -> bool:
    u = urlparse(proxy if "://" in proxy else "http://" + proxy)
    try:
        return (u.hostname or "") in LOCAL_HOSTS and u.port == port
    except ValueError:
        return False


def parse_serve(cfg: dict, port: int) -> dict:
    """From `tailscale serve status --json`: the https address that forwards to this dashboard, and whether port 443
    already forwards somewhere else."""
    out = {"serve": False, "url": "", "funnel": False, "busy": ""}
    for hostport, web in ((cfg or {}).get("Web") or {}).items():
        host, _, p = str(hostport).rpartition(":")
        root = ((web or {}).get("Handlers") or {}).get("/") or {}
        proxy = str(root.get("Proxy") or "")
        if proxy and _targets(proxy, port):
            out.update(serve=True, url="https://" + host + ("" if p == "443" else ":" + p),
                       funnel=bool(((cfg or {}).get("AllowFunnel") or {}).get(hostport)))
            return out
        if p == "443" and root:
            out["busy"] = proxy or "another handler"
    return out


def tailscale_status(port: int) -> dict:
    out = {"installed": False, "running": False, "state": "", "dns": "", "https_certs": False,
           "serve": False, "url": "", "funnel": False, "busy": "", "download": TS_DOWNLOAD}
    exe = _ts_bin()
    if not exe or in_container():
        return out
    out["installed"] = True
    rc, so, _ = _run([exe, "status", "--json"])
    try:
        st = json.loads(so) if so.strip() else {}
    except ValueError:
        st = {}
    out["state"] = str(st.get("BackendState") or ("Stopped" if rc else ""))
    out["running"] = out["state"] == "Running"
    out["dns"] = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    out["https_certs"] = bool(st.get("CertDomains"))
    if out["running"]:
        rc, so, _ = _run([exe, "serve", "status", "--json"])
        try:
            out.update(parse_serve(json.loads(so) if so.strip() else {}, port))
        except ValueError:
            pass
    return out


def serve_command(port: int, on: bool = True, exe: str = "") -> list[str]:
    exe = exe or _ts_bin() or "tailscale"
    if on:
        return [exe, "serve", "--bg", "--yes", "--https=443", f"http://127.0.0.1:{port}"]
    return [exe, "serve", "--https=443", "off"]


def _login_link(text: str) -> str:
    m = re.search(r"https://login\.tailscale\.com/\S+", text or "")
    return m.group(0).rstrip(".,)") if m else ""


def tailscale_serve(port: int, on: bool) -> dict:
    """Start or stop forwarding https://<computer>.<tailnet>.ts.net to this dashboard."""
    if not _ts_bin():
        return {"ok": False, "error": "not_installed"}
    if on:
        cur = tailscale_status(port)
        if not cur["running"]:
            return {"ok": False, "error": "not_running", "status": cur}
        if cur["serve"]:
            return {"ok": True, "status": cur}
        if cur["busy"]:
            return {"ok": False, "error": "busy", "busy": cur["busy"], "status": cur}
    # Until HTTPS is switched on for the tailnet, the command prints a link to do that and waits.
    rc, so, se = _run(serve_command(port, on), timeout=20)
    text = (so + "\n" + se).strip()
    status = tailscale_status(port)
    ok = status["serve"] if on else not status["serve"]
    res = {"ok": ok, "status": status, "output": text[-1500:]}
    if not ok and _login_link(text):
        res.update(error="enable_https", link=_login_link(text))
    return res


# --------------------------------------------------------------------------- same Wi-Fi, QR code, keep awake
def _primary_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))     # no packet is sent: this only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def lan_url(port: int) -> str:
    """http://<this computer's address>:<port> when the dashboard listens on the network (not in Docker, whose
    addresses are the container's)."""
    if not opened_to_network() or os.environ.get("CODESPACES") == "true" or in_container():
        return ""
    ip = _primary_ip()
    return f"http://{ip}:{port}" if ip and not ip.startswith("127.") else ""


def phone_url(port: int, ts: dict | None = None) -> str:
    """The address a phone should open: the Tailscale https address, else the same-Wi-Fi one."""
    ts = ts if ts is not None else tailscale_status(port)
    return ts["url"] if ts.get("serve") else lan_url(port)


def login_link(url: str) -> str:
    """The QR code's link. The code goes after "#", so it never reaches a server log; the login page reads it."""
    return url.rstrip("/") + LOGIN + "#code=" + access_code() if url else ""


def qr_svg(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")


def qr_data_uri(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    import io

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000", light="#fff")
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def qr_terminal(text: str) -> bool:
    """Print the QR code in the terminal; False when segno is not installed."""
    try:
        import segno
    except ImportError:
        return False
    segno.make(text, error="m").terminal(compact=True)
    return True


_awake: subprocess.Popen | None = None
_awake_lock = threading.Lock()
_awake_checked = [0.0]


def keep_awake_command(pid: int) -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        return ["caffeinate", "-i", "-s", "-w", str(pid)]   # idle sleep; system sleep on power
    if sys.platform.startswith("linux") and shutil.which("systemd-inhibit") and shutil.which("tail"):
        return ["systemd-inhibit", "--what=idle:sleep", "--who=Risk-Aware Trading Agents",
                "--why=Phone access is on", "--mode=block", "tail", f"--pid={pid}", "-f", "/dev/null"]
    return None


def keep_awake_supported() -> bool:
    return keep_awake_command(os.getpid()) is not None and not in_container()


def sync_keep_awake() -> bool:
    """While phone access is on and the dashboard runs behind the gate, keep the computer from idle sleep (until the
    dashboard exits). Returns whether it is being kept awake."""
    global _awake
    want = GATE["active"] and enabled() and keep_awake_wanted() and not in_container()
    with _awake_lock:
        alive = _awake is not None and _awake.poll() is None
        if want and not alive:
            cmd = keep_awake_command(os.getpid())
            if cmd:
                try:
                    _awake = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
                except OSError:
                    _awake = None
        elif not want and alive:
            _awake.terminate()
            _awake = None
        return _awake is not None and _awake.poll() is None


def _maybe_sync_keep_awake() -> None:
    """A switch flipped in a terminal reaches a running dashboard within half a minute."""
    now = time.monotonic()
    if now - _awake_checked[0] >= 30:
        _awake_checked[0] = now
        try:
            sync_keep_awake()
        except Exception:
            pass
