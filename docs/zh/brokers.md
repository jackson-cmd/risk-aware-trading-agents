# 券商配置

[English](../brokers.md)

所有券商都用同一套接口（`account`、`positions`、`quotes`、`place_order`、`cancel_order`、`open_orders`）。在 `riskaware.yaml` 里用 `broker.name` 选券商，用 `broker.options` 传参数，key 放在 `.env` 里（参考 [.env.example](../../.env.example)）。先用 `riskaware doctor` 检查连接，第一次 `riskaware run` 之前先用 `riskaware plan` 预演。

模拟盘、沙盒账户按模拟盘处理。其他账户都是实盘，需要 `execution.mode: live`、命令加 `--live`，并手动确认。

## 内置模拟盘（`paper`）

不需要任何账户。持仓和订单保存在 `~/.riskaware/paper/default.json`，价格来自 Yahoo Finance。

```yaml
broker:
  name: paper
  options: {starting_cash: 100000, slippage_bps: 5}
```

用 `riskaware paper reset --cash 50000` 重置。

## Alpaca（`alpaca`）

在 alpaca.markets 创建 key（模拟盘 key 以 `PK` 开头）。

```bash
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
```

```yaml
broker:
  name: alpaca
  options: {paper: true}          # 实盘改成 false；付费 SIP 行情可设 data_feed: sip
```

也认 `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` 和 `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`；有多个模拟账户时，可以用 `key_env`、`secret_env` 指定变量名。`paper: true` 时如果填的是实盘 key（`AK...`），会直接拒绝。

## Public.com（`public`）

Public 没有模拟盘，每一笔都是真钱。在 Public 的 API 设置页生成 secret key。

```bash
PUBLIC_API_SECRET=...
PUBLIC_ACCOUNT_ID=...            # 可选，不填就用第一个经纪账户
```

Public 的下单是异步的：智能体先记下订单号，下次运行时再读状态。Public 的个人 API 只允许个人、非商业用途。

## Tradier（`tradier`）

```bash
TRADIER_SANDBOX_TOKEN=...        # 实盘用 TRADIER_TOKEN
TRADIER_ACCOUNT_ID=VA...
```

```yaml
broker:
  name: tradier
  options: {sandbox: true}
```

## 盈透 Interactive Brokers（`ibkr`）

先 `pip install -e ".[ibkr]"`，启动 TWS 或 IB Gateway，在 API 设置里勾选 "ActiveX and Socket Clients"。

```yaml
broker:
  name: ibkr
  options: {port: 7497, client_id: 17, market_data_type: 3}   # 7497/4002 模拟，7496/4001 实盘；3 = 延迟行情
```

## 嘉信 Charles Schwab（`schwab`）

先 `pip install -e ".[schwab]"`。在 developer.schwab.com 创建应用，回调地址填 `https://127.0.0.1:8182`。

```bash
SCHWAB_API_KEY=...
SCHWAB_APP_SECRET=...
```

第一次连接会打开浏览器登录嘉信，token 保存在 `~/.riskaware/schwab_token.json`。嘉信没有模拟盘 API。

## tastytrade（`tastytrade`）

先 `pip install -e ".[tastytrade]"`，在 tastytrade 的 API 设置里创建 OAuth 应用。

```bash
TASTYTRADE_CLIENT_SECRET=...
TASTYTRADE_REFRESH_TOKEN=...
```

```yaml
broker:
  name: tastytrade
  options: {sandbox: true}        # cert 测试环境
```

## 富途 / moomoo（`futu`）

先 `pip install -e ".[futu]"`，下载并登录 OpenD（富途牛牛或 moomoo）。

```yaml
broker:
  name: futu
  options: {simulate: true, security_firm: FUTUINC, port: 11111}   # FUTUINC = moomoo 美国；FUTUSECURITIES = 富途香港
```

实盘（`simulate: false`）需要在 `.env` 里填 `FUTU_TRADE_PASSWORD`（交易密码）。

## 老虎证券（`tiger`）

先 `pip install -e ".[tiger]"`。从老虎开放平台下载 `tiger_openapi_config.properties`，放到 `~/.riskaware/tiger/`。

```yaml
broker:
  name: tiger
  options: {paper: true, account: "你的模拟账户号"}
```

## 长桥（`longbridge`）

先 `pip install -e ".[longbridge]"`。在长桥开放平台创建应用；模拟账户有自己单独的 access token。

```bash
LONGPORT_APP_KEY=...
LONGPORT_APP_SECRET=...
LONGPORT_ACCESS_TOKEN=...
```

```yaml
broker:
  name: longbridge
  options: {paper: true}
```

## 接入新券商

继承 `riskaware.brokers.base.Broker`，实现七个方法和 `is_paper`，在 `riskaware/brokers/__init__.py` 里注册，再仿照 `tests/test_brokers.py` 写一个 mock 测试。欢迎提 PR。
