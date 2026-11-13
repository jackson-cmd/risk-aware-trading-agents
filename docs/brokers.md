# Broker setup

[中文](zh/brokers.md)

Every broker sits behind the same interface (`account`, `positions`, `quotes`, `place_order`, `cancel_order`, `open_orders`). Pick one with `broker.name` in `riskaware.yaml`, pass adapter options in `broker.options`, and keep keys in `.env` (see [examples/.env.example](../examples/.env.example)). Check the connection with `riskaware doctor`, then preview with `riskaware plan` before the first `riskaware run`.

Paper or sandbox accounts are treated as paper. Any other account is LIVE and needs `execution.mode: live`, the `--live` flag and a confirmation.

## Built-in paper account (`paper`)

No account needed. Positions and orders live in `~/.riskaware/paper/default.json`; prices come from Yahoo Finance.

```yaml
broker:
  name: paper
  options: {starting_cash: 100000, slippage_bps: 5}
```

Reset it with `riskaware paper reset --cash 50000`.

## Alpaca (`alpaca`)

Create keys at alpaca.markets (paper keys start with `PK`).

```bash
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
```

```yaml
broker:
  name: alpaca
  options: {paper: true}          # false for live, data_feed: sip if you pay for SIP data
```

The adapter also reads `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` and `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`; `key_env` and `secret_env` pick any other variable names (handy for several paper accounts). With `paper: true` a live key (`AK...`) is refused.

## Public.com (`public`)

Public has no paper environment: every order is real. Generate a secret key on Public's API settings page.

```bash
PUBLIC_API_SECRET=...
PUBLIC_ACCOUNT_ID=...            # optional; the first brokerage account is used otherwise
```

Orders are asynchronous: the agent records the order id and reads the status on the next run. Public's terms allow personal, non-commercial use of the Individual API.

## Tradier (`tradier`)

```bash
TRADIER_SANDBOX_TOKEN=...        # or TRADIER_TOKEN for live
TRADIER_ACCOUNT_ID=VA...
```

```yaml
broker:
  name: tradier
  options: {sandbox: true}
```

## Interactive Brokers (`ibkr`)

`pip install "risk-aware-trading-agents[ibkr]"`, start TWS or IB Gateway, enable "ActiveX and Socket Clients" in the API settings.

```yaml
broker:
  name: ibkr
  options: {port: 7497, client_id: 17, market_data_type: 3}   # 7497/4002 paper, 7496/4001 live; 3 = delayed data
```

## Charles Schwab (`schwab`)

`pip install "risk-aware-trading-agents[schwab]"`. Create an app at developer.schwab.com with the callback URL `https://127.0.0.1:8182`.

```bash
SCHWAB_API_KEY=...
SCHWAB_APP_SECRET=...
```

The first connection opens a browser for the Schwab login and saves a token to `~/.riskaware/schwab_token.json`. Schwab has no paper API.

## tastytrade (`tastytrade`)

`pip install "risk-aware-trading-agents[tastytrade]"`. Create an OAuth application in tastytrade's API settings.

```bash
TASTYTRADE_CLIENT_SECRET=...
TASTYTRADE_REFRESH_TOKEN=...
```

```yaml
broker:
  name: tastytrade
  options: {sandbox: true}        # the cert environment
```

## Futu / moomoo (`futu`)

`pip install "risk-aware-trading-agents[futu]"`. Download and log in to OpenD (Futu or moomoo).

```yaml
broker:
  name: futu
  options: {simulate: true, security_firm: FUTUINC, port: 11111}   # FUTUINC = moomoo US; FUTUSECURITIES = Futu HK
```

Real trading (`simulate: false`) needs `FUTU_TRADE_PASSWORD` in `.env`.

## Tiger Brokers (`tiger`)

`pip install "risk-aware-trading-agents[tiger]"`. Download `tiger_openapi_config.properties` from the Tiger developer page into `~/.riskaware/tiger/`.

```yaml
broker:
  name: tiger
  options: {paper: true, account: "your paper account id"}
```

## Longbridge (`longbridge`)

`pip install "risk-aware-trading-agents[longbridge]"`. Create an app on the Longbridge developer site; a paper account has its own access token.

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

## Adding a broker

Subclass `riskaware.brokers.base.Broker`, implement the seven methods and `is_paper`, register it in `riskaware/brokers/__init__.py`, and add a mocked test like the ones in `tests/test_brokers.py`. Pull requests welcome.
