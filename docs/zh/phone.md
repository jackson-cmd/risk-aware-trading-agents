# 在手机上用看板

[English](../phone.md)

看板照常在你的电脑上运行，手机在家、在外用流量都能打开它。手机通过 [Tailscale](https://tailscale.com/download) 连回电脑：这是给你自己设备用的免费私有网络，地址只有登录了你 Tailscale 账号的设备能打开，连接走 HTTPS；看板本身仍然只监听 127.0.0.1，不向公网开放任何端口。

## 一次性设置

1. 在运行看板的电脑上装好 Tailscale 并登录。
2. 启动看板（`riskaware dashboard`），打开 **手机** 页，打开 **允许手机用访问码登录**。也可以在终端里运行 `riskaware phone on`。
3. 点 **开启 HTTPS 地址**。它会运行 `tailscale serve --bg --yes --https=443 http://127.0.0.1:8501`，把 `https://你的电脑.你的tailnet.ts.net` 转到看板；也可以自己在终端里运行这条命令，或者运行 `riskaware phone tailscale`。第一次用时 Tailscale 可能要求先给你的 tailnet 开 HTTPS：页面会显示链接，打开、点 Enable，再回来点一次按钮。
4. 在手机上从 App Store 或 Google Play 装 Tailscale，用同一个账号登录。
5. 用手机相机扫 **手机** 页上的二维码（或 `riskaware phone` 在终端里打印的二维码），看板会打开并自动登录。
6. 添加到主屏幕：iPhone 上 Safari → 分享 → 添加到主屏幕；Android 上 Chrome → ⋮ → 添加到主屏幕。之后它带着自己的图标全屏打开，和 App 一样。iPhone 的主屏幕 App 和 Safari 分开记登录，所以可能要再输一次访问码。

## 怎么保证安全

- 手机访问关闭时（默认），看板只回应这台电脑。`riskaware dashboard` 现在只监听 127.0.0.1；0.9.0 之前它监听所有网卡，同一个 Wi-Fi 里谁都能打开。
- 手机用 8 位访问码登录一次，之后 180 天内保持登录，经常用就会自动续期。登录凭证是签名过的 HttpOnly cookie。同一台设备输错 5 次要等 10 分钟；所有设备合计输错 20 次，所有登录暂停 15 分钟。
- **换一个访问码** 会换掉访问码并让所有手机退出；**让所有手机退出登录** 只让它们退出，访问码不变。两个按钮都在 **手机** 页，终端里也有（`riskaware phone new-code`、`riskaware phone sign-out`）。
- 显示访问码的 **手机** 页只能在这台电脑上打开。其余功能在手机上和电脑上一样，手机也做不了看板本来不允许的事：默认仍是模拟盘，下真实订单仍然要过 [安全](../../README_zh.md#-安全) 里的三道开关。
- 二维码链接把访问码放在 `#` 后面，所以访问码不会出现在任何服务器日志里。
- 如果这个地址开了 Tailscale Funnel，它在公网上也能打开。访问码仍然保护着它，**手机** 页也会提醒你。

## 让电脑一直连得上

- 电脑要开着、醒着，并且看板在运行。**电脑不睡眠**（默认开）会在手机访问打开时阻止电脑因闲置而睡眠，支持 macOS 和 Linux。合上笔记本盖子仍然会睡眠，所以请开着盖子，或者接上显示器用。
- `riskaware agents schedule --install` 让模拟盘智能体每天照常交易，不管看板开没开；手机是用来看它们、手动启动任务的。

## 只在同一个 Wi-Fi 里用（不装 Tailscale）

用 `riskaware dashboard --host 0.0.0.0` 启动看板。之后 **手机** 页会显示这台电脑的地址和二维码，同一个 Wi-Fi 下的手机扫码即可，仍然要用访问码登录。这种方式走 http：同一 Wi-Fi 里的人能看到流量，出了家门就用不了。

## 用 Docker 时

镜像在容器里照旧监听 0.0.0.0，电脑上的浏览器算作「这台电脑」。保持 `-p 127.0.0.1:8501:8501`，让只有电脑能连到这个端口；在电脑本身（不是容器里）装好 Tailscale，并在电脑上运行 `tailscale serve --bg --https=443 http://127.0.0.1:8501`；然后打开手机访问（`docker exec <容器> riskaware phone on`，或在 **手机** 页打开），再按上面第 4 到 6 步操作。

## 终端命令

| 命令 | 作用 |
|---|---|
| `riskaware phone` | 开关状态、访问码、地址和可以扫的二维码 |
| `riskaware phone on` / `off` | 允许手机用访问码登录 / 只允许这台电脑 |
| `riskaware phone tailscale [--off]` | 用 Tailscale 给看板一个 https 地址 |
| `riskaware phone new-code` | 换一个访问码，所有手机重新登录 |
| `riskaware phone sign-out` | 让所有手机退出登录，访问码不变 |

看板不在 8501 端口时加 `--port`。开关、访问码和给 cookie 签名的密钥存在 `~/.riskaware/phone.json`，只有你能读；在终端里改了，正在运行的看板会读到。

## 需要什么

Streamlit 1.59 或更新版本（`dashboard` 这个 extra 会装上）：登录门挡在 Streamlit 的 `st.App` 前面，每个页面、文件和 WebSocket 都经过它。Streamlit 太旧时看板照常运行，但没有手机访问，并提示怎么升级。
