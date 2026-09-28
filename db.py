"""
SQLite storage for the auto trader: every trade, every event, and small
key/value state (paper balance, pause flag, backtest cache).

One connection per call (sqlite3 connections aren't shared across threads
by default); WAL mode so the dashboard can read while the trader writes.
"""

from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    mode             TEXT NOT NULL,             -- paper / testnet / live
    symbol           TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    side             TEXT NOT NULL,             -- LONG (spot is long-only)
    status           TEXT NOT NULL,             -- OPEN / CLOSED
    quality          REAL,
    reasons          TEXT,                      -- JSON list: why it was opened
    qty              REAL NOT NULL,             -- base qty bought
    qty_open         REAL NOT NULL,             -- base qty still held
    entry_price      REAL NOT NULL,             -- average fill
    cost_usd         REAL NOT NULL,             -- quote spent incl. fee
    stop_price       REAL NOT NULL,             -- current stop (moves to breakeven after TP1)
    initial_stop     REAL NOT NULL,
    tp1_price        REAL,
    target_price     REAL,
    tp1_done         INTEGER NOT NULL DEFAULT 0,
    entry_time       REAL NOT NULL,
    entry_order_id   TEXT,
    stop_order_id    TEXT,                      -- exchange-side protective stop (testnet/live)
    exit_price       REAL,                      -- average of all sells
    exit_time        REAL,
    exit_reason      TEXT,
    realized_usd     REAL NOT NULL DEFAULT 0,   -- quote received from sells, net of fees
    sold_qty         REAL NOT NULL DEFAULT 0,   -- base qty sold so far (partials + final)
    sold_value       REAL NOT NULL DEFAULT 0,   -- gross quote value of those sells (for avg exit price)
    fees_usd         REAL NOT NULL DEFAULT 0,
    pnl_usd          REAL,
    pnl_pct          REAL
);
CREATE INDEX IF NOT EXISTS trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,     -- INFO / TRADE / WARN / ERROR
    message TEXT NOT NULL,
    trade_id INTEGER
);

CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    mode          TEXT NOT NULL,
    type          TEXT NOT NULL,     -- DEPOSIT / WITHDRAW / RESET / BUY / SELL
    amount        REAL NOT NULL,     -- signed change to cash (quote)
    balance_after REAL NOT NULL,
    note          TEXT,
    trade_id      INTEGER
);
CREATE INDEX IF NOT EXISTS ledger_mode ON ledger(mode, id);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts     REAL NOT NULL,
    mode   TEXT NOT NULL,
    cash   REAL NOT NULL,
    equity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS equity_mode ON equity_snapshots(mode, ts);

-- Orders placed from the Trade page's order form (Binance-style spot orders).
CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL NOT NULL,
    mode              TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,          -- BUY / SELL
    type              TEXT NOT NULL,          -- MARKET / LIMIT
    price             REAL,                   -- limit price
    qty               REAL,                   -- base amount requested
    quote             REAL,                   -- or: USDT to spend (market buy by total)
    tp_price          REAL,
    sl_price          REAL,
    status            TEXT NOT NULL,          -- NEW / FILLED / CANCELED / REJECTED
    filled_qty        REAL NOT NULL DEFAULT 0,
    avg_price         REAL,
    fee_usd           REAL NOT NULL DEFAULT 0,
    exchange_order_id TEXT,
    trade_id          INTEGER,
    note              TEXT,
    updated_at        REAL
);
CREATE INDEX IF NOT EXISTS orders_open ON orders(mode, status);

-- Every execution, whoever caused it (your orders, stops, targets, bots): Binance-style trade history.
CREATE TABLE IF NOT EXISTS fills (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    mode      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    side      TEXT NOT NULL,
    qty       REAL NOT NULL,
    price     REAL NOT NULL,
    quote     REAL NOT NULL,      -- USDT spent (buy, incl. fee) / received (sell, net of fee)
    fee_usd   REAL NOT NULL,
    source    TEXT,               -- "order #12", "stop hit", "forecast bot", ...
    trade_id  INTEGER,
    order_id  INTEGER
);
CREATE INDEX IF NOT EXISTS fills_mode ON fills(mode, id);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release — added in place so old DBs keep working.
_MIGRATIONS = {
    "trades": {"strategy": "TEXT NOT NULL DEFAULT 'rules'",   # rules / manual / forecast
               "expires_at": "REAL"},                          # forecast trades: close at horizon
    "events": {"mode": "TEXT"},
}


def init_db():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# --- state -------------------------------------------------------------------

def get_state(key: str, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(key: str, value):
    with connect() as c:
        c.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


# --- events ------------------------------------------------------------------

def log_event(level: str, message: str, trade_id: int | None = None, mode: str | None = None):
    print(f"[trader] {level}: {message}")
    with connect() as c:
        if mode is None and trade_id is not None:
            row = c.execute("SELECT mode FROM trades WHERE id=?", (trade_id,)).fetchone()
            mode = row["mode"] if row else None
        c.execute("INSERT INTO events(ts, level, message, trade_id, mode) VALUES(?,?,?,?,?)",
                  (time.time(), level, message, trade_id, mode))


def recent_events(limit: int = 50, mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM events", []
    if mode:
        q += " WHERE mode=?"
        args.append(mode)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]


# --- orders / fills ------------------------------------------------------------

def insert_order(**fields) -> int:
    fields.setdefault("ts", time.time())
    fields["updated_at"] = fields["ts"]
    cols = ", ".join(fields)
    with connect() as c:
        cur = c.execute(f"INSERT INTO orders({cols}) VALUES({', '.join('?' for _ in fields)})", tuple(fields.values()))
        return int(cur.lastrowid)


def update_order(order_id: int, **fields):
    fields["updated_at"] = time.time()
    with connect() as c:
        c.execute(f"UPDATE orders SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), order_id))


def get_order(order_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def open_orders(mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM orders WHERE status='NEW'", []
    if mode:
        q += " AND mode=?"
        args.append(mode)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC", args)]


def order_history(mode: str, limit: int = 200) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM orders WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, limit))]


def add_fill(mode: str, symbol: str, side: str, qty: float, price: float, quote: float, fee_usd: float,
             source: str, trade_id: int | None = None, order_id: int | None = None):
    with connect() as c:
        c.execute("INSERT INTO fills(ts, mode, symbol, side, qty, price, quote, fee_usd, source, trade_id, order_id) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), mode, symbol, side, qty, price, quote, fee_usd, source, trade_id, order_id))


def fills(mode: str, limit: int = 200) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM fills WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, limit))]


# --- ledger / equity ---------------------------------------------------------

def add_ledger(mode: str, type_: str, amount: float, balance_after: float,
               note: str = "", trade_id: int | None = None):
    with connect() as c:
        c.execute("INSERT INTO ledger(ts, mode, type, amount, balance_after, note, trade_id) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), mode, type_, amount, balance_after, note, trade_id))


def ledger(mode: str, limit: int = 500) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ledger WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, limit))]


def net_deposits(mode: str) -> float:
    """Money the user put in minus money taken out (RESET counts as a fresh deposit)."""
    with connect() as c:
        row = c.execute("SELECT id FROM ledger WHERE mode=? AND type='RESET' ORDER BY id DESC LIMIT 1", (mode,)).fetchone()
        since = row["id"] if row else 0
        r = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger WHERE mode=? AND id>=? AND type IN ('DEPOSIT','WITHDRAW','RESET')",
            (mode, since),
        ).fetchone()
    return float(r["s"])


def last_reset_ts(mode: str) -> float:
    with connect() as c:
        row = c.execute("SELECT ts FROM ledger WHERE mode=? AND type='RESET' ORDER BY id DESC LIMIT 1", (mode,)).fetchone()
    return float(row["ts"]) if row else 0.0


def add_equity_snapshot(mode: str, cash: float, equity: float):
    with connect() as c:
        c.execute("INSERT INTO equity_snapshots(ts, mode, cash, equity) VALUES(?,?,?,?)", (time.time(), mode, cash, equity))


def equity_curve(mode: str, since: float = 0.0, limit: int = 2000) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT ts, cash, equity FROM equity_snapshots WHERE mode=? AND ts>=? ORDER BY ts DESC LIMIT ?",
            (mode, since, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- trades ------------------------------------------------------------------

def insert_trade(**fields) -> int:
    if isinstance(fields.get("reasons"), list):
        fields["reasons"] = json.dumps(fields["reasons"])
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with connect() as c:
        cur = c.execute(f"INSERT INTO trades({cols}) VALUES({marks})", tuple(fields.values()))
        return int(cur.lastrowid)


def update_trade(trade_id: int, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE trades SET {sets} WHERE id=?", (*fields.values(), trade_id))


def get_trade(trade_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    return dict(row) if row else None


def open_trades(mode: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM trades WHERE status='OPEN'", []
    if mode:
        q += " AND mode=?"
        args.append(mode)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id", args)]


def closed_trades(mode: str | None = None, limit: int = 100) -> list[dict]:
    q, args = "SELECT * FROM trades WHERE status='CLOSED'", []
    if mode:
        q += " AND mode=?"
        args.append(mode)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY exit_time DESC LIMIT ?", (*args, limit))]


def all_trades(mode: str | None = None, limit: int = 1000) -> list[dict]:
    q, args = "SELECT * FROM trades", []
    if mode:
        q += " WHERE mode=?"
        args.append(mode)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def realized_pnl_since(ts: float, mode: str) -> float:
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) AS s FROM trades WHERE status='CLOSED' AND mode=? AND exit_time>=?",
            (mode, ts),
        ).fetchone()
    return float(row["s"])


def last_close_time(symbol: str, mode: str) -> float | None:
    with connect() as c:
        row = c.execute(
            "SELECT MAX(exit_time) AS t FROM trades WHERE status='CLOSED' AND mode=? AND symbol=?",
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
               FROM trades WHERE status='CLOSED' AND mode=?""",
            (mode,),
        ).fetchone()
    n = row["n"]
    return {
        "closed": n,
        "wins": row["wins"],
        "win_rate": 100.0 * row["wins"] / n if n else 0.0,
        "pnl_usd": row["pnl"],
        "fees_usd": row["fees"],
    }
