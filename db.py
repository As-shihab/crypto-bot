"""
MySQL storage for the auto trader: every trade, every event, small key/value
state (paper balance, pause flag, backtest cache) and the credentials table
(Binance keys, API URLs, SMTP, Telegram — see credentials.py).

Connection details come from .env (MYSQL_HOST/PORT/DATABASE/USER/PASSWORD);
the database must already exist; the tables are created on first start. One connection per
thread (PyMySQL connections aren't thread-safe), pinged/reconnected on use,
committed at the end of every `with connect()` block.
"""

from __future__ import annotations
import json
import re
import threading
import time
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

import config

_TABLES = [
    """CREATE TABLE IF NOT EXISTS trades (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    mode             VARCHAR(16) NOT NULL,      -- paper (Demo) / live (Real)
    symbol           VARCHAR(32) NOT NULL,
    timeframe        VARCHAR(8)  NOT NULL,
    side             VARCHAR(8)  NOT NULL,      -- LONG (spot is long-only)
    status           VARCHAR(16) NOT NULL,      -- OPEN / CLOSED
    strategy         VARCHAR(16) NOT NULL DEFAULT 'rules',   -- rules / manual / forecast
    quality          DOUBLE,
    reasons          TEXT,                      -- JSON list: why it was opened
    qty              DOUBLE NOT NULL,           -- base qty bought
    qty_open         DOUBLE NOT NULL,           -- base qty still held
    entry_price      DOUBLE NOT NULL,           -- average fill
    cost_usd         DOUBLE NOT NULL,           -- quote spent incl. fee
    stop_price       DOUBLE NOT NULL,           -- current stop (moves to breakeven after TP1)
    initial_stop     DOUBLE NOT NULL,
    tp1_price        DOUBLE,
    target_price     DOUBLE,
    tp1_done         TINYINT NOT NULL DEFAULT 0,
    entry_time       DOUBLE NOT NULL,
    expires_at       DOUBLE,                    -- forecast trades: close at horizon
    entry_order_id   VARCHAR(64),
    stop_order_id    VARCHAR(64),               -- exchange-side protective stop (Real)
    exit_price       DOUBLE,                    -- average of all sells
    exit_time        DOUBLE,
    exit_reason      VARCHAR(255),
    realized_usd     DOUBLE NOT NULL DEFAULT 0, -- quote received from sells, net of fees
    sold_qty         DOUBLE NOT NULL DEFAULT 0, -- base qty sold so far (partials + final)
    sold_value       DOUBLE NOT NULL DEFAULT 0, -- gross quote value of those sells (for avg exit price)
    fees_usd         DOUBLE NOT NULL DEFAULT 0,
    pnl_usd          DOUBLE,
    pnl_pct          DOUBLE,
    INDEX trades_status (status, mode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS events (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    ts       DOUBLE NOT NULL,
    level    VARCHAR(16) NOT NULL,     -- INFO / TRADE / WARN / ERROR
    message  TEXT NOT NULL,
    trade_id INT,
    mode     VARCHAR(16),
    INDEX events_mode (mode, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS ledger (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    ts            DOUBLE NOT NULL,
    mode          VARCHAR(16) NOT NULL,
    type          VARCHAR(16) NOT NULL,     -- DEPOSIT / WITHDRAW / RESET / BUY / SELL
    amount        DOUBLE NOT NULL,          -- signed change to cash (quote)
    balance_after DOUBLE NOT NULL,
    note          TEXT,
    trade_id      INT,
    INDEX ledger_mode (mode, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS equity_snapshots (
    id     INT AUTO_INCREMENT PRIMARY KEY,
    ts     DOUBLE NOT NULL,
    mode   VARCHAR(16) NOT NULL,
    cash   DOUBLE NOT NULL,
    equity DOUBLE NOT NULL,
    INDEX equity_mode (mode, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    # Orders placed from the Trade page's order form (Binance-style spot orders).
    """CREATE TABLE IF NOT EXISTS orders (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    ts                DOUBLE NOT NULL,
    mode              VARCHAR(16) NOT NULL,
    symbol            VARCHAR(32) NOT NULL,
    side              VARCHAR(8)  NOT NULL,   -- BUY / SELL
    type              VARCHAR(16) NOT NULL,   -- MARKET / LIMIT
    price             DOUBLE,                 -- limit price
    qty               DOUBLE,                 -- base amount requested
    quote             DOUBLE,                 -- or: USDT to spend (market buy by total)
    tp_price          DOUBLE,
    sl_price          DOUBLE,
    status            VARCHAR(16) NOT NULL,   -- NEW / FILLED / CANCELED / REJECTED
    filled_qty        DOUBLE NOT NULL DEFAULT 0,
    avg_price         DOUBLE,
    fee_usd           DOUBLE NOT NULL DEFAULT 0,
    exchange_order_id VARCHAR(64),
    trade_id          INT,
    note              TEXT,
    updated_at        DOUBLE,
    INDEX orders_open (mode, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    # Every execution, whoever caused it (your orders, stops, targets, bots): Binance-style trade history.
    """CREATE TABLE IF NOT EXISTS fills (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    ts        DOUBLE NOT NULL,
    mode      VARCHAR(16) NOT NULL,
    symbol    VARCHAR(32) NOT NULL,
    side      VARCHAR(8)  NOT NULL,
    qty       DOUBLE NOT NULL,
    price     DOUBLE NOT NULL,
    quote     DOUBLE NOT NULL,      -- USDT spent (buy, incl. fee) / received (sell, net of fee)
    fee_usd   DOUBLE NOT NULL,
    source    VARCHAR(128),         -- "order #12", "stop hit", "forecast bot", ...
    trade_id  INT,
    order_id  INT,
    INDEX fills_mode (mode, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS state (
    `key`   VARCHAR(128) PRIMARY KEY,
    value   LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    # API keys, secrets and service URLs, edited on the Settings page (credentials.py).
    """CREATE TABLE IF NOT EXISTS credentials (
    name       VARCHAR(64) PRIMARY KEY,   -- e.g. BINANCE_API_SECRET
    provider   VARCHAR(32) NOT NULL,      -- binance / telegram / smtp
    value      TEXT NOT NULL,
    is_secret  TINYINT NOT NULL DEFAULT 0,
    updated_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _raw_connect():
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4", cursorclass=DictCursor, autocommit=False, connect_timeout=10,
    )


class _Conn:
    """Thin wrapper so callers can write c.execute(sql, args).fetchone() like sqlite3."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, args=()):
        cur = self.raw.cursor()
        cur.execute(sql, tuple(args) if args else None)
        return cur


@contextmanager
def connect():
    conn = getattr(_local, "conn", None)
    if conn is None or not conn.open:
        conn = _local.conn = _raw_connect()
    else:
        conn.ping(reconnect=True)
    try:
        yield _Conn(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _columns(ddl: str) -> tuple[str, list[tuple[str, str]]]:
    """(table, [(column, definition)]) parsed from one of the CREATE TABLE statements above."""
    lines = ddl.splitlines()
    table = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", lines[0]).group(1)
    cols = []
    for line in lines[1:]:
        line = line.split("--")[0].strip().rstrip(",")
        if not line or line.startswith((")", "INDEX", "PRIMARY", "UNIQUE")):
            continue
        name, definition = line.split(None, 1)
        cols.append((name.strip("`"), definition))
    return table, cols


def init_db():
    """Connect to the existing MYSQL_DATABASE (from .env), create the app's tables if missing and
    ALTER existing ones to add any missing columns. Never touches users or other tables. Cheap after the first call."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        with connect() as c:
            for ddl in _TABLES:
                c.execute(ddl)
                table, cols = _columns(ddl)
                have = {r["COLUMN_NAME"].lower() for r in c.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                    (table,))}
                for name, definition in cols:
                    if name.lower() not in have:
                        print(f"[db] ALTER TABLE {table} ADD COLUMN {name}")
                        c.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}")
        _initialized = True


# --- state -------------------------------------------------------------------

def get_state(key: str, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM state WHERE `key`=%s", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(key: str, value):
    with connect() as c:
        c.execute("INSERT INTO state(`key`, value) VALUES(%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)",
                  (key, json.dumps(value)))


# --- credentials ---------------------------------------------------------------

def get_credentials() -> dict[str, str]:
    with connect() as c:
        return {r["name"]: r["value"] for r in c.execute("SELECT name, value FROM credentials")}


def seed_credential(name: str, provider: str, value: str, is_secret: bool):
    """Insert a credential row with its default value if it doesn't exist yet."""
    with connect() as c:
        c.execute("INSERT IGNORE INTO credentials(name, provider, value, is_secret, updated_at) VALUES(%s,%s,%s,%s,%s)",
                  (name, provider, value, int(is_secret), time.time()))


def set_credential(name: str, provider: str, value: str, is_secret: bool):
    with connect() as c:
        c.execute("INSERT INTO credentials(name, provider, value, is_secret, updated_at) VALUES(%s,%s,%s,%s,%s) "
                  "ON DUPLICATE KEY UPDATE value=VALUES(value), updated_at=VALUES(updated_at)",
                  (name, provider, value, int(is_secret), time.time()))


# --- events ------------------------------------------------------------------

def log_event(level: str, message: str, trade_id: int | None = None, mode: str | None = None):
    print(f"[trader] {level}: {message}")
    with connect() as c:
        if mode is None and trade_id is not None:
            row = c.execute("SELECT mode FROM trades WHERE id=%s", (trade_id,)).fetchone()
            mode = row["mode"] if row else None
        c.execute("INSERT INTO events(ts, level, message, trade_id, mode) VALUES(%s,%s,%s,%s,%s)",
                  (time.time(), level, message, trade_id, mode))


def recent_events(limit: int = 50, mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM events", []
    if mode:
        q += " WHERE mode=%s"
        args.append(mode)
    with connect() as c:
        return list(c.execute(q + " ORDER BY id DESC LIMIT %s", (*args, int(limit))))


# --- orders / fills ------------------------------------------------------------

def insert_order(**fields) -> int:
    fields.setdefault("ts", time.time())
    fields["updated_at"] = fields["ts"]
    cols = ", ".join(fields)
    with connect() as c:
        cur = c.execute(f"INSERT INTO orders({cols}) VALUES({', '.join('%s' for _ in fields)})", tuple(fields.values()))
        return int(cur.lastrowid)


def update_order(order_id: int, **fields):
    fields["updated_at"] = time.time()
    with connect() as c:
        c.execute(f"UPDATE orders SET {', '.join(f'{k}=%s' for k in fields)} WHERE id=%s", (*fields.values(), order_id))


def get_order(order_id: int) -> dict | None:
    with connect() as c:
        return c.execute("SELECT * FROM orders WHERE id=%s", (order_id,)).fetchone()


def open_orders(mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM orders WHERE status='NEW'", []
    if mode:
        q += " AND mode=%s"
        args.append(mode)
    with connect() as c:
        return list(c.execute(q + " ORDER BY id DESC", args))


def order_history(mode: str, limit: int = 200) -> list[dict]:
    with connect() as c:
        return list(c.execute("SELECT * FROM orders WHERE mode=%s ORDER BY id DESC LIMIT %s", (mode, int(limit))))


def add_fill(mode: str, symbol: str, side: str, qty: float, price: float, quote: float, fee_usd: float,
             source: str, trade_id: int | None = None, order_id: int | None = None):
    with connect() as c:
        c.execute("INSERT INTO fills(ts, mode, symbol, side, qty, price, quote, fee_usd, source, trade_id, order_id) "
                  "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (time.time(), mode, symbol, side, qty, price, quote, fee_usd, source, trade_id, order_id))


def fills(mode: str, limit: int = 200) -> list[dict]:
    with connect() as c:
        return list(c.execute("SELECT * FROM fills WHERE mode=%s ORDER BY id DESC LIMIT %s", (mode, int(limit))))


# --- ledger / equity ---------------------------------------------------------

def add_ledger(mode: str, type_: str, amount: float, balance_after: float,
               note: str = "", trade_id: int | None = None):
    with connect() as c:
        c.execute("INSERT INTO ledger(ts, mode, type, amount, balance_after, note, trade_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                  (time.time(), mode, type_, amount, balance_after, note, trade_id))


def ledger(mode: str, limit: int = 500) -> list[dict]:
    with connect() as c:
        return list(c.execute("SELECT * FROM ledger WHERE mode=%s ORDER BY id DESC LIMIT %s", (mode, int(limit))))


def net_deposits(mode: str) -> float:
    """Money the user put in minus money taken out (RESET counts as a fresh deposit)."""
    with connect() as c:
        row = c.execute("SELECT id FROM ledger WHERE mode=%s AND type='RESET' ORDER BY id DESC LIMIT 1", (mode,)).fetchone()
        since = row["id"] if row else 0
        r = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger WHERE mode=%s AND id>=%s AND type IN ('DEPOSIT','WITHDRAW','RESET')",
            (mode, since),
        ).fetchone()
    return float(r["s"])


def last_reset_ts(mode: str) -> float:
    with connect() as c:
        row = c.execute("SELECT ts FROM ledger WHERE mode=%s AND type='RESET' ORDER BY id DESC LIMIT 1", (mode,)).fetchone()
    return float(row["ts"]) if row else 0.0


def add_equity_snapshot(mode: str, cash: float, equity: float):
    with connect() as c:
        c.execute("INSERT INTO equity_snapshots(ts, mode, cash, equity) VALUES(%s,%s,%s,%s)", (time.time(), mode, cash, equity))


def equity_curve(mode: str, since: float = 0.0, limit: int = 2000) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT ts, cash, equity FROM equity_snapshots WHERE mode=%s AND ts>=%s ORDER BY ts DESC LIMIT %s",
            (mode, since, int(limit)),
        ).fetchall()
    return list(reversed(rows))


# --- trades ------------------------------------------------------------------

def insert_trade(**fields) -> int:
    if isinstance(fields.get("reasons"), list):
        fields["reasons"] = json.dumps(fields["reasons"])
    cols = ", ".join(fields)
    marks = ", ".join("%s" for _ in fields)
    with connect() as c:
        cur = c.execute(f"INSERT INTO trades({cols}) VALUES({marks})", tuple(fields.values()))
        return int(cur.lastrowid)


def update_trade(trade_id: int, **fields):
    sets = ", ".join(f"{k}=%s" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE trades SET {sets} WHERE id=%s", (*fields.values(), trade_id))


def get_trade(trade_id: int) -> dict | None:
    with connect() as c:
        return c.execute("SELECT * FROM trades WHERE id=%s", (trade_id,)).fetchone()


def open_trades(mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM trades WHERE status='OPEN'", []
    if mode:
        q += " AND mode=%s"
        args.append(mode)
    with connect() as c:
        return list(c.execute(q + " ORDER BY id", args))


def closed_trades(mode: str | None = None, limit: int = 100) -> list[dict]:
    q, args = "SELECT * FROM trades WHERE status='CLOSED'", []
    if mode:
        q += " AND mode=%s"
        args.append(mode)
    with connect() as c:
        return list(c.execute(q + " ORDER BY exit_time DESC LIMIT %s", (*args, int(limit))))


def all_trades(mode: str | None = None, limit: int = 1000) -> list[dict]:
    q, args = "SELECT * FROM trades", []
    if mode:
        q += " WHERE mode=%s"
        args.append(mode)
    with connect() as c:
        return list(c.execute(q + " ORDER BY id DESC LIMIT %s", (*args, int(limit))))


def realized_pnl_since(ts: float, mode: str) -> float:
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) AS s FROM trades WHERE status='CLOSED' AND mode=%s AND exit_time>=%s",
            (mode, ts),
        ).fetchone()
    return float(row["s"])


def last_close_time(symbol: str, mode: str) -> float | None:
    with connect() as c:
        row = c.execute(
            "SELECT MAX(exit_time) AS t FROM trades WHERE status='CLOSED' AND mode=%s AND symbol=%s",
            (mode, symbol),
        ).fetchone()
    return row["t"]


def stats(mode: str) -> dict:
    with connect() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n,
                      COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0) AS wins,
                      COALESCE(SUM(pnl_usd), 0) AS pnl,
                      COALESCE(SUM(fees_usd), 0) AS fees
               FROM trades WHERE status='CLOSED' AND mode=%s""",
            (mode,),
        ).fetchone()
    n, wins = int(row["n"]), int(row["wins"])   # MySQL SUM() of ints comes back as Decimal
    return {
        "closed": n,
        "wins": wins,
        "win_rate": 100.0 * wins / n if n else 0.0,
        "pnl_usd": float(row["pnl"]),
        "fees_usd": float(row["fees"]),
    }
