# Crypto Chart-Analysis Assistant

A rules-based bot that monitors live cryptocurrency markets via standard exchange public APIs (Binance, Kraken, Bybit, etc.), computes standard technical indicators, and delivers BUY / SELL / HOLD signals with confidence scores and analytical reasoning. 

> **Notice:** This assistant does **not** execute trades automatically by default and does **not** use binary options platforms or synthetic pricing (e.g., Quotex).

---

## Technical Overview

* **Market Analysis Engine:** Uses standard pandas indicators (EMA, RSI, MACD, Bollinger Bands, ATR, Swing Levels).
* **Execution Options:** Supports Paper (simulated), Binance Testnet, and Binance Live spot execution (via `ccxt`).
* **Frontend/Backend:** Flask & Socket.IO backend serving a React 18 + Vite interface with real-time charts and position calculators.

---

## Installation & Setup

### Prerequisites
* Python 3.10+
* Node.js 18+ (for building the Web UI)

### 1. Environment Setup
```bash
# Clone or place files in your working directory
python3 -m venv venv

# Activate Virtual Environment
# On Linux/macOS:
source venv/bin/activate
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Windows (CMD):
.\venv\Scripts\activate.bat

# Install required dependencies
pip install -r requirements.txt
cp .env.example .env          # then fill in your MySQL login
```

Storage is **MySQL**. `.env` holds only the MySQL login (`MYSQL_HOST`,
`MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`); the
database must already exist; the app creates its tables on first start. Everything else — Binance
API key/secret/URL, Telegram, SMTP — is stored in the MySQL `credentials`
table and edited on the dashboard's **Settings → Credentials** card.

Build the web frontend once (React + Vite, needs Node 18+):

```bash
cd frontend && npm install && npm run build && cd ..
```

Then run whichever you want:

```bash
python dashboard.py           # backend + built React app at http://localhost:3100 (recommended)
python backtester.py          # console backtest — run this first, see Step 1 below
python bot.py                 # console live-signal loop, no browser
```

`dashboard.py` is the full experience: real-time chart, live signal, entry
suggestion, backtest, and Prophet forecast, all in a browser. Needs normal
outbound internet access to reach the exchange's API (see the note under
Setup) — won't work in a network-sandboxed environment. Everything below
explains what each piece does and how to read the output.

## What's in here

| File | Purpose |
|---|---|
| `config.py` | All settings: exchange, symbol, timeframe, indicator periods, fees. |
| `data_fetcher.py` | Pulls live/historical candles from the exchange (public data, no API key needed). |
| `indicators.py` | EMA, RSI, MACD, Bollinger Bands, ATR, swing support/resistance — plain pandas, fully auditable. |
| `signal_engine.py` | Combines indicators into one BUY/SELL/HOLD call with a 0-100 confidence score and reasons. |
| `backtester.py` | Walks the signal engine over history with real fees/slippage and reports honest win rate, expectancy, drawdown. |
| `bot.py` | Live loop: polls for new candles, prints/alerts a signal. No auto-trading. |
| `alerts.py` | Optional Telegram alerts (falls back to console-only). |
| `forecast.py` | Prophet-based trend forecast (day/month/year) on daily closes — separate from the rules-based signal. |
| `live_trade.py` | Real-time trade recommendation for one coin + timeframe: live signal, checklist, entry/stop/targets. |
| `auto_trader.py` | Auto trader: scans every coin x timeframe, opens the best confirmed long, manages stop/TP1/TP2/time exits. |
| `broker.py` | Order execution: Demo (simulated) or Real (Binance via ccxt, at the configured API URL). |
| `db.py` | MySQL storage: trades/holdings, orders, fills, ledger, equity snapshots, events, state, credentials. |
| `credentials.py` | Binance key/secret/API URL, Telegram and SMTP settings in the MySQL `credentials` table (secrets never sent to the browser). |
| `order_desk.py` | Spot order desk for the Trade page: market/limit buy & sell, holdings, reserved balances. |
| `notifier.py` | SMTP email on every trade open/close, daily-loss halt and errors. |
| `frontend/` | React web app (Vite): Dashboard, Trade, Auto trader, History, Settings pages. |
| `dashboard.py` | Backend for the web app (Flask + Socket.IO, port 3100) — real-time chart per coin, live signal, on-demand backtest and Prophet forecast, in a browser. |

## Setup

Edit `config.py` (or set environment variables) to pick your exchange,
symbol, and timeframe. Defaults to Binance BTC/USDT on 15-minute candles.

If Binance isn't accessible from where you are, `EXCHANGE_ID` also works
with `"kraken"`, `"coinbase"`, `"bybit"`, etc. — anything [ccxt](https://github.com/ccxt/ccxt)
supports, since market-data calls are public on all of them.

**Note on where to run this:** this needs normal outbound internet access
to reach the exchange's API. It won't run inside a network-sandboxed
environment (which is why I built and unit-tested the logic here using
synthetic data, but couldn't run a live fetch from this session — see
"How this was tested" below). Run it on your own computer or a VPS.

## Step 1 — always run the backtest first

Before you look at a single live signal, find out whether this logic has
any real edge on the pair/timeframe you care about:

```bash
python backtester.py
```

This fetches `config.BACKTEST_CANDLES` (default 2000) historical candles
and walks forward through them bar by bar, generating a signal at each
point using *only* data available up to that point (no lookahead), then
simulates entering/exiting a position on each signal with real fees and
slippage. It prints:

- **Win rate** — % of trades that were profitable
- **Expectancy per trade** — average P&L% per trade, net of costs (the
  single most important number: if this is negative, the strategy loses
  money even if the win rate looks fine)
- **Profit factor, total return, max drawdown**
- **Raw directional hit rate** — the % of BUY/SELL calls where the very
  next candle closed in the predicted direction. This is the closest
  analog to the "accuracy" percentage people quote for binary-option
  bots. Expect this number to land somewhere in the 45-60% range on
  liquid pairs, not 80-90% — and note it is *not* the same as the
  profitability numbers above.

Try different symbols, timeframes, and `MIN_CONFIDENCE` thresholds in
`config.py` and re-run. If you can't get positive expectancy after
reasonable tuning, that's real information — don't run it live.

## Step 2 — run it live as an assistant

```bash
python bot.py
```

This polls for newly closed candles and prints (and optionally Telegram-
alerts) each non-HOLD signal with its confidence score and the specific
indicators behind it, e.g.:

```
[2026-09-21 10:15:00+00:00] BUY  (confidence 72/100)  price=63241.5000
  - EMA stack bullish (fast > slow > trend) — uptrend alignment
  - MACD histogram just crossed positive — bullish momentum shift
  - RSI 58.3 above midline — mild bullish bias
  - Volume well above average, confirming the bullish move
```

### Web app (React frontend + Python backend)

- **Backend** — `dashboard.py` (Flask + Socket.IO, port 3100): REST for one-off
  loads (`/api/config`, `/api/history`, `/api/indicators`, `/api/trades`,
  `/api/trades.csv`) and a websocket for everything live (prices, live trade
  call, forecast/backtest results, trader status and commands). It serves the
  built React app from `frontend/dist`. All times are shown in UTC+6
  (Asia/Dhaka) on a 12-hour clock. Pages fit the window (SAP Fiori /
  UI5-style layout) — only panels scroll, except on small screens.
- **Frontend** — `frontend/` (React 18, Vite, react-router, lightweight-charts,
  Chart.js). The header has the **Demo | Real** switch (starts on Demo every
  time the app starts): it picks where new trades go and which account the
  Trade, Account, Auto trader and Settings pages show. Demo simulates fills at
  real Binance prices with fees and needs no Binance account or keys; Real
  places real orders with real money on Binance and unlocks once the Binance
  API key and secret are saved in Settings → Credentials. Open trades in the
  other mode keep their stops/targets managed. Sidebar pages:
  - **Dashboard** — chart (candles, volume, RSI, MACD, live price, forecast,
    entry/stop/TP lines) with Trade now / Signal / Position / Forecast / Best
    time / Backtest / Markets tabs.
  - **Trade** — Binance-style spot terminal: pair header with 24h stats and
    the bot's live call, live **order book** and **market trades** (click a
    price to use it), chart with forecast, and a **Buy | Sell order form**
    (Limit or Market, amount or total, 25/50/75/100% of available, optional
    TP/SL with "use bot levels"). Bottom tabs: open orders (cancel), order
    history, holdings (sell all), trade history (every fill, whoever caused
    it) and the bot's signal. Demo limit orders fill when the price
    crosses; Real limits rest on the exchange. Funds held by open orders aren't available for new ones.
  - **Account** — the account picked in the header. Demo: your play-money
    account — deposit / withdraw / reset, equity, P&L and return vs what you
    put in, an equity curve, and tabs for open trades, all trades, the cash
    **ledger** (every deposit, buy, sell) and logs. Real: your Binance USDT
    balance, equity curve, P&L of the bot's trades, open trades, every fill and
    logs (deposit / withdraw on Binance itself). Demo includes the **forecast bot**: pick coins, a horizon (15m … 1 year),
    % of cash per trade, minimum signal/noise and minimum move; each new candle
    it re-fits Prophet per coin and buys when the projected move beats costs and
    its own uncertainty band, then exits at the forecast target, the lower band
    (stop), or when the horizon is reached. Its decisions (and why it skipped)
    are listed live.
  - **Auto trader** — mode, equity, pause/resume, close all, open trades, last
    scan (why each coin/timeframe wasn't traded), event log.
  - **History** — every trade in MySQL, filter by mode/coin, stats, CSV export.
  - **Settings** (pinned to the bottom of the sidebar) — mode, limits, entry
    rules, email status + test email, security checklist, and **Credentials**:
    Binance API key / secret / API URL, Telegram API URL / bot token / chat ID,
    SMTP host / port / SSL / user / password / from / to. Saved secrets show
    masked; leave a secret blank to keep it.
- **Develop the frontend**: run `python dashboard.py`, then `cd frontend && npm run dev`
  and open http://localhost:5173 (Vite proxies `/api` and `/socket.io` to :3100).

The trader always runs inside the dashboard so manual trades get managed;
it only opens trades by itself when **Auto trade** is ON in Settings (saved in
MySQL, default OFF). A DB lock refuses
to start a second trader (e.g. `python auto_trader.py`) on the same database.

### What the analysis tabs show

```bash
python dashboard.py
```

Serves a browser dashboard at `http://localhost:3100`:

- **Coin picker** — pick from `config.COINS` (BTC, BNB, LTC, ETH, SOL, XRP by
  default; add any pair your exchange lists).
- **One time control** — a single row of tabs (15m/30m/1h/4h/1D/1M/1Y, from
  `config.TIME_OPTIONS`) drives the chart candle size, RSI/MACD panels, and
  the forecast horizon together, instead of separate controls per feature.
- **Real-time chart** — candlestick history, volume, RSI and MACD panels,
  plus a live price line pushed over a websocket (Socket.IO), updated every
  `config.REALTIME_POLL_SECONDS`.
- **Trade now** (live signal, `live_trade.py`) — for the selected coin *and*
  timeframe, scores the still-forming candle with the latest price every
  `config.LIVE_SIGNAL_SECONDS` and pushes it over the websocket. Shows
  LONG/SHORT/WAIT/AVOID, entry at live price, stop, TP1 (1R) and TP2, reward:
  risk, a countdown to candle close (the call is provisional until then), the
  matching-horizon forecast, and a checklist: closed candle agrees, higher
  timeframe trend, forecast direction, backtest edge on this timeframe, room
  to the next swing level, volatility vs fees. Setup quality = share of checks
  that pass — not a win probability. Also: risk-% position sizing, a signal
  log with % move since each call, entry/stop/target lines on the chart, and
  optional browser alert + beep on a new entry.
- **Signal panel** — live (forming candle) and last-closed-candle signals for
  the selected timeframe, with reasons.
- **Position calculator** — one investment-amount field (top toolbar) sizes
  the current entry: quantity, live unrealized P&L, and estimated $
  profit/loss at target/stop.
- **Backtest** — runs `backtester.py` for the selected coin and timeframe, with
  a win/loss donut chart.
- **Forecast** — runs `forecast.py` (Prophet) for whichever horizon the time
  tabs are set to; plots the projection and uncertainty band on the chart,
  projects your investment amount forward at that horizon, and includes a
  "transparent overview" of exactly what fed the number (history span,
  candle count, which seasonality components were actually enabled).
- **Best time to invest** — scans every horizon (15m through 1Y) with
  Prophet and ranks them by projected move relative to that horizon's own
  uncertainty band. The top-ranked horizon is a "this trend is large
  relative to its own noise" signal, not a promise — click any ranked
  horizon to jump the whole dashboard to it. Runs one Prophet fit per
  horizon sequentially, so it can take a few minutes.

This is a purely statistical trend extrapolation — it does not feed into
the BUY/SELL/HOLD signal above, and per `forecast.py`'s docstring, the
widening uncertainty band further out is the honest part of the output.

For Telegram alerts, fill in the Telegram bot token and chat ID in
Settings → Credentials — get a token from
[@BotFather](https://t.me/BotFather) and your chat ID from
[@userinfobot](https://t.me/userinfobot).

## Step 3 — auto trading (Demo → Real)

`auto_trader.py` turns the live analysis into trades. **Spot, long only**
(Binance spot can't short — a SELL signal only closes an open long).

Every `AUTO_SCAN_SECONDS` it runs the `live_trade.py` recommendation for
every coin in `COINS` on every timeframe in `AUTO_TIMEFRAMES`, keeps only
ENTRY_LONG calls that pass stricter filters (quality ≥ `AUTO_MIN_QUALITY`,
confirmed by the last *closed* candle, and a backtest on that exact
coin+timeframe with positive expectancy over ≥20 trades), ranks them by
quality and opens the best ones. Every `AUTO_MANAGE_SECONDS` it manages
open trades:

- **Stop** — in Real mode a `STOP_LOSS_LIMIT` order sits on the exchange,
  so the position is protected even if the bot crashes.
- **TP1 (1R)** — sells `TP1_CLOSE_FRACTION` (50%) and moves the stop to
  breakeven (+fees).
- **TP2**, **SELL signal** on the trade's timeframe, or **time stop**
  (`MAX_HOLD_BARS`) — closes the rest.

Risk controls in `config.py`: `RISK_PER_TRADE_PCT` sizing, `MAX_POSITION_USD`
cap, `MAX_OPEN_TRADES`, one trade per coin, `SYMBOL_COOLDOWN_MINUTES`,
`DAILY_LOSS_LIMIT_PCT` (halts new entries until 00:00 UTC), and a pause
switch in the dashboard. Every trade and event lands in MySQL, and you get
an email for each open and close.

Setup (`.env` = MySQL login only; the rest in Settings → Credentials):

1. **Email** — SMTP host / port / user / password, From, To. For Gmail use
   an App Password, not your normal password.
2. **Demo first** (the default) — simulated fills at live prices with fees
   and slippage, starting from `PAPER_START_BALANCE`. No API key. Run it for
   weeks.
3. **Real** — save the Binance API key and secret, then switch the header
   to Real (it asks for confirmation). Create the key with **Spot
   trading only — never enable withdrawals** — and restrict it to your
   server's IP. To rehearse with fake funds first, set the Binance API URL
   to `https://testnet.binance.vision` with testnet keys; set it back to
   `https://api.binance.com` for real money.

Switch it on in the dashboard's **Settings** page (Auto trade: On), which is saved
in MySQL and applies to the active mode. Run it either inside the dashboard (`python dashboard.py`,
"Auto trader" page) or headless (`python auto_trader.py`) — not both at once
(a DB lock enforces this). The dashboard now listens on `127.0.0.1` only (it can
close positions); don't expose it to the internet without auth.

**Before going live:** the backtest on this logic has so far shown small or
negative expectancy on most pairs/timeframes — the auto trader will simply
never trade those (that's the backtest gate working). Automating a strategy
doesn't give it an edge; it only executes the one it has, faster. Start
with an amount you can afford to lose entirely.

## How this was tested

The sandbox this bot was built in has outbound network restricted to an
allowlist that doesn't include exchange APIs (Binance/Kraken/Coinbase were
all blocked at the proxy level when I tried). So:

- Every module was unit-tested with **synthetic OHLCV data** (random walks
  and mixed-regime series I generated locally) to confirm the indicators,
  signal engine, and backtester run correctly, handle edge cases, and
  produce sane, auditable output — a backtest on synthetic noise
  correctly showed poor results, which is the expected, honest outcome.
- The `data_fetcher.py` / `bot.py` / `backtester.py` live-data paths use
  standard, well-tested `ccxt` calls, but I have not been able to run
  them end-to-end against a live exchange from here. **Run
  `python backtester.py` yourself first** — if anything's wrong with the
  live data path it'll surface immediately as an error, before you ever
  get to `bot.py`.

## Honest expectations

Read `signal_engine.py`'s module docstring — it explains why the
confidence score is a measure of indicator agreement, not a probability
of being right. On liquid crypto pairs, a genuinely decent rules-based
strategy typically nets out to a small, positive expectancy per trade
after costs — often a win rate not far above 50%, made profitable by
letting winners run longer than losers (asymmetric exits), not by being
"right" 80-90% of the time. If backtester.py shows something close to
80-90% win rate on real market data, the far more likely explanation is
a bug or overfitting, not a breakthrough — go back and check for
lookahead bias or an unrealistically favorable exit rule before trusting
it.

## Why not Quotex / binary options

This was built as the legitimate alternative after I explained why I
wouldn't build the Quotex/binary-options version: those platforms'
"OTC" pairs are synthetic prices generated by the broker itself (not a
real market), the broker is typically your counterparty rather than a
neutral exchange, and short up/down windows don't support the accuracy
needed to beat their payout structure. Everything in this project instead
points at a regulated exchange with real market data and no house edge
baked into the payout.

## One more thing, since you're trading from Bangladesh

I don't have live web access from this session to check current specifics,
but Bangladesh Bank has historically treated cryptocurrency transactions
and unauthorized foreign-currency trading as falling under foreign
exchange control law, and has issued public warnings on this. Please
verify the current rules with Bangladesh Bank or a local financial/legal
advisor before depositing real money anywhere — this applies regardless
of which platform or bot you use.

This project and its outputs are for educational/informational purposes,
not financial advice.
# crypto-bot
