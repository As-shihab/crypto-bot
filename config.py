"""
Central configuration for the analysis bot.

Nothing in this file requires API keys to run the analysis/backtest side of
the bot — Binance's market-data endpoints are public. API keys are only
needed if you later choose to place real orders yourself (this bot does not
place trades automatically — see README.md).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Market ---------------------------------------------------------------
EXCHANGE_ID = "binance"        # ccxt exchange id; e.g. "binance", "kraken", "coinbase"
SYMBOL = "BTC/USDT"            # any pair the exchange lists, e.g. "ETH/USDT"
TIMEFRAME = "15m"              # candle size: "1m","5m","15m","1h","4h","1d", ...

# Coins selectable in the dashboard (symbol -> display label). Add any pair
# your exchange lists — market data is public, no API key needed.
COINS = {
    "BTC/USDT": "Bitcoin",
    "BNB/USDT": "BNB",
    "LTC/USDT": "Litecoin",
    "ETH/USDT": "Ethereum",
    "SOL/USDT": "Solana",
    "XRP/USDT": "XRP",
}

# --- Forecast (Prophet) -----------------------------------------------------
# Horizon key -> which candle timeframe to fit/forecast on, how many candles
# ahead ("periods"), the pandas frequency alias for that candle size, and how
# much history to pull for fitting. Short intraday horizons fit on intraday
# candles (Prophet has far less signal to work with there — treat these as
# even less reliable than the longer horizons); month/year fit on daily
# candles regardless of the chosen chart timeframe.
FORECAST_HORIZONS = {
# `periods` is the horizon the projected change is measured at; `plot_periods`
# is how far the curve drawn on the chart extends (>= periods), so 1-candle
# horizons still show a visible path instead of a single dot.
    "15m":   {"timeframe": "15m", "periods": 1,   "plot_periods": 16,  "freq": "15min", "history_candles": 2000},
    "30m":   {"timeframe": "30m", "periods": 1,   "plot_periods": 16,  "freq": "30min", "history_candles": 2000},
    "1h":    {"timeframe": "1h",  "periods": 1,   "plot_periods": 24,  "freq": "h",     "history_candles": 2000},
    "4h":    {"timeframe": "4h",  "periods": 1,   "plot_periods": 18,  "freq": "4h",    "history_candles": 1000},
    "day":   {"timeframe": "1d",  "periods": 1,   "plot_periods": 14,  "freq": "D",     "history_candles": 730},
    "month": {"timeframe": "1d",  "periods": 30,  "plot_periods": 30,  "freq": "D",     "history_candles": 730},
    "year":  {"timeframe": "1d",  "periods": 365, "plot_periods": 365, "freq": "D",     "history_candles": 730},
}

# --- Entry sizing (dashboard entry-suggestion card) --------------------------
# Stop-loss / take-profit distance from entry, in multiples of ATR (average
# true range) — a volatility-scaled distance, not a fixed price offset.
ATR_STOP_MULT = 1.5
ATR_TARGET_MULT = 3.0   # 2:1 reward:risk at these defaults

# --- Dashboard chart ---------------------------------------------------------
# One unified time control drives the chart timeframe, indicators, and the
# forecast horizon at once — key -> (label, candle timeframe, forecast
# horizon key). Binance has no native 45m candle, so the ladder below is the
# closest increasing sequence it actually supports.
CHART_TIMEFRAME = "15m"         # default candle size for the live dashboard chart
TIME_OPTIONS = [
    {"key": "15m", "label": "15m", "timeframe": "15m", "horizon": "15m"},
    {"key": "30m", "label": "30m", "timeframe": "30m", "horizon": "30m"},
    {"key": "1h",  "label": "1h",  "timeframe": "1h",  "horizon": "1h"},
    {"key": "4h",  "label": "4h",  "timeframe": "4h",  "horizon": "4h"},
    {"key": "1d",  "label": "1D",  "timeframe": "1d",  "horizon": "day"},
    {"key": "1mo", "label": "1M",  "timeframe": "1d",  "horizon": "month"},
    {"key": "1y",  "label": "1Y",  "timeframe": "1d",  "horizon": "year"},
]
CHART_HISTORY_LIMIT = 300       # candles to load for the initial chart draw
REALTIME_POLL_SECONDS = 5       # how often the dashboard pushes live price ticks
LIVE_SIGNAL_SECONDS = 10        # how often the live trade recommendation is recomputed and pushed

# --- Indicator settings -----------------------------------------------------
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2
ATR_PERIOD = 14
SR_LOOKBACK = 50               # bars used to detect swing support/resistance

# --- Signal engine ----------------------------------------------------------
# Minimum confidence (0-100) before the bot will surface a BUY/SELL call
# instead of HOLD. Raising this makes the bot pickier (fewer, higher-quality
# signals); lowering it produces more signals of lower average quality.
MIN_CONFIDENCE = 60

# --- Backtest ---------------------------------------------------------------
BACKTEST_CANDLES = 2000        # how many historical candles to pull for a backtest
TAKER_FEE = 0.001              # 0.1% per side, typical Binance spot taker fee
SLIPPAGE = 0.0005              # 0.05% assumed slippage per fill — tune to your pair's liquidity

# --- Alerts (optional) -------------------------------------------------------
# Telegram token/chat and API URL live in the MySQL `credentials` table (Settings page).
# Leave the token blank to just print signals to the console.
TELEGRAM_API_URL = "https://api.telegram.org"
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# --- Live loop ---------------------------------------------------------------
POLL_SECONDS = 60              # how often bot.py checks for a new closed candle

# --- Auto trading ------------------------------------------------------------
# Two modes, switched on the Trade page (the last choice is saved in MySQL):
#   "paper" = DEMO — simulated fills at live prices with fees/slippage, no API key, no real orders (default)
#   "live"  = REAL — REAL orders with REAL money on Binance, using the keys in the `credentials` table
# Spot only, long only: spot can't short, so a SELL signal only closes an open long.
TRADING_MODE = "paper"                  # startup default until a mode has been chosen
MODE_NAMES = {"paper": "DEMO", "live": "REAL"}
# Binance credentials + API URL come from the MySQL `credentials` table (credentials.py),
# edited on the Settings page. These are only the defaults before it is loaded.
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""
BINANCE_API_URL = "https://api.binance.com"   # spot REST base URL (market data + orders)

# Auto trade on/off is a dashboard switch (Settings page), stored in MySQL — not here.
AUTO_TIMEFRAMES = ["15m", "1h", "4h"]   # timeframes scanned for every coin in COINS
AUTO_SCAN_SECONDS = 60                  # how often to scan for new entries
AUTO_MANAGE_SECONDS = 10                # how often open trades are checked against stop/targets
AUTO_MIN_QUALITY = 80                   # stricter than the dashboard's 60 — every trade costs fees
AUTO_REQUIRE_CONFIRMED = True           # only enter when the last CLOSED candle agrees (no provisional entries)
AUTO_REQUIRE_BACKTEST = True            # only enter when this coin+timeframe backtest shows positive expectancy
AUTO_BACKTEST_MAX_AGE_HOURS = 12        # re-run each coin+timeframe backtest this often

RISK_PER_TRADE_PCT = 1.0                # % of equity lost if the stop is hit
MAX_POSITION_USD = 100.0                # hard cap per trade, whatever the risk sizing says
MIN_ORDER_USD = 10.0                    # skip trades smaller than this (Binance min notional is ~5-10 USDT)
MAX_OPEN_TRADES = 2
DAILY_LOSS_LIMIT_PCT = 3.0              # halt new entries for the rest of the UTC day past this loss
SYMBOL_COOLDOWN_MINUTES = 60            # wait after closing a coin before re-entering it
MAX_HOLD_BARS = 48                      # time stop: close after this many candles of the trade's timeframe
TP1_CLOSE_FRACTION = 0.5                # sell this share at TP1, then move the stop to breakeven
STOP_LIMIT_BUFFER_PCT = 0.3             # exchange stop-limit price sits this % below the trigger
PAPER_START_BALANCE = 1000.0            # USDT the paper account starts with
QUOTE_ASSET = "USDT"

# --- Database (the only thing read from .env) -----------------------------------
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT") or 3306)
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "crypto_bot")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB_LABEL = f"MySQL {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
DISPLAY_TZ = "Asia/Dhaka"   # UTC+6 — used for times written into logs/emails (the web app uses the same zone)

# The dashboard can pause trading and close positions, so it only listens on
# this machine by default. Set DASHBOARD_HOST=0.0.0.0 only behind a VPN / auth proxy.
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "3100"))

# --- Email (SMTP) --------------------------------------------------------------
# Stored in the MySQL `credentials` table (Settings page). Gmail: SMTP_HOST=smtp.gmail.com,
# SMTP_PORT=587, SMTP_USER=you@gmail.com and an App Password (not your normal password).
SMTP_HOST = ""
SMTP_PORT = 587
SMTP_USER = ""
SMTP_PASSWORD = ""
SMTP_USE_SSL = False    # True for port 465, else STARTTLS
EMAIL_FROM = ""
EMAIL_TO = ""
