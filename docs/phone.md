# Use the dashboard on your phone

[中文](zh/phone.md)

The dashboard keeps running on your computer, and your phone opens it at home or away. The phone reaches the computer through [Tailscale](https://tailscale.com/download), a free private network for your own devices: the address opens only on devices logged in to your Tailscale account, the connection is HTTPS, and the dashboard still listens only on 127.0.0.1, so nothing is opened to the internet.

## Set it up once

1. On the computer that runs the dashboard, install Tailscale and log in.
2. Start the dashboard (`riskaware dashboard`), open the **Phone** page and turn on **Let my phone in with an access code**. Or run `riskaware phone on` in a terminal.
3. Press **Turn on the https address**. It runs `tailscale serve --bg --yes --https=443 http://127.0.0.1:8501`, which forwards `https://your-computer.your-tailnet.ts.net` to the dashboard; you can also run that command yourself, or `riskaware phone tailscale`. The first time, Tailscale may ask you to turn on HTTPS for your tailnet: the page shows the link; open it, press Enable, and press the button again.
4. On the phone, install Tailscale from the App Store or Google Play and log in to the same account.
5. Scan the QR code on the Phone page with the phone's camera (or the one `riskaware phone` prints). The dashboard opens and logs in by itself.
6. Add it to the home screen: on iPhone, Safari → Share → Add to Home Screen; on Android, Chrome → ⋮ → Add to Home screen. It then opens full screen with its own icon, like an app. An iPhone keeps the logins of home-screen apps apart from Safari's, so you may type the code once more.

## How it stays safe

- With phone access off (the default) the dashboard answers only this computer. `riskaware dashboard` now listens on 127.0.0.1; before 0.9.0 it listened on every network interface, so anyone on the same Wi-Fi could open it.
- A phone logs in once with the 8-character access code and stays logged in for 180 days, renewed while you use it. The login is a signed, HttpOnly cookie. Five wrong codes from one device make it wait 10 minutes; twenty from anywhere pause every login for 15 minutes.
- **New access code** replaces the code and logs every phone out; **Sign out every phone** logs them out and keeps the code. Both are on the Phone page and in the terminal (`riskaware phone new-code`, `riskaware phone sign-out`).
- The Phone page, which shows the code, opens only on this computer. Everything else works on the phone as on the computer, and a phone can do no more than the dashboard already allows: paper trading stays the default, and a live order still needs the three switches in [Safety](../README.md#-safety).
- The QR code carries the code after `#` in its link, so the code never reaches a server log.
- If Tailscale Funnel is on for the address, it opens from the internet too. The access code still protects it, and the Phone page warns you.

## Keep the computer reachable

- The computer has to be on, awake and running the dashboard. **Keep this computer awake** (on by default) stops it from sleeping when idle while phone access is on, on macOS and Linux. Closing a laptop's lid still puts it to sleep, so keep it open or use an external display.
- `riskaware agents schedule --install` keeps the paper-trading agents trading every day whether or not the dashboard runs; the phone is for looking at them and starting things by hand.

## Only on the same Wi-Fi, without Tailscale

Start the dashboard with `riskaware dashboard --host 0.0.0.0`. The Phone page then shows this computer's address and a QR code for a phone on the same Wi-Fi, which still logs in with the access code. This is plain http: others on the Wi-Fi can see the traffic, and it stops working once you leave home.

## With Docker

The image listens on 0.0.0.0 inside the container, as before, and a browser on the computer counts as this computer. Keep `-p 127.0.0.1:8501:8501` so only the computer reaches the port, install Tailscale on the computer itself (not in the container), and run `tailscale serve --bg --https=443 http://127.0.0.1:8501` there; then turn phone access on (`docker exec <container> riskaware phone on`, or the Phone page) and follow steps 4 to 6.

## In the terminal

| Command | What it does |
|---|---|
| `riskaware phone` | on or off, the access code, the address and a QR code to scan |
| `riskaware phone on` / `off` | let phones in with the code / only this computer |
| `riskaware phone tailscale [--off]` | give the dashboard an https address in your tailnet |
| `riskaware phone new-code` | a new code; every phone logs in again |
| `riskaware phone sign-out` | log every phone out; the code stays |

Add `--port` when the dashboard is not on 8501. The switch, the code and the key that signs the cookies live in `~/.riskaware/phone.json`, readable only by you; a running dashboard picks up a change made in the terminal.

## Requirements

Streamlit 1.59 or newer (the `dashboard` extra installs it): the gate sits in front of Streamlit's `st.App`, which serves every page, file and WebSocket through it. With an older Streamlit the dashboard still runs, without phone access, and says how to upgrade.
