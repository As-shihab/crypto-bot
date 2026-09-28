"""
Trading settings editable from the dashboard's Settings page, stored in MySQL
(`state` table) instead of .env / config.py.

config.py holds the defaults; saved values override them at startup
(`apply_saved()`) and immediately when changed (`update()`), by setting the
attribute on the config module — every trading path reads `config.X` at call
time, so a change takes effect on the next scan / order without a restart.

The Auto trade on/off switch lives here too (key "auto_trade", default OFF).
"""

from __future__ import annotations

import config
import db

# key -> (config attribute, type, min, max, label, help)
FIELDS = {
    "risk_per_trade_pct":      ("RISK_PER_TRADE_PCT", float, 0.1, 10, "Risk per trade (% of equity)", "How much of the account a stop-out loses. Sets position size."),
    "max_position_usd":        ("MAX_POSITION_USD", float, 5, 1_000_000, "Max position (USDT)", "Hard cap per trade, whatever the risk sizing says."),
    "min_order_usd":           ("MIN_ORDER_USD", float, 5, 10_000, "Min order (USDT)", "Skip trades smaller than this (Binance minimum is ~5–10)."),
    "max_open_trades":         ("MAX_OPEN_TRADES", int, 1, 20, "Max open auto trades", "Across all coins, per account."),
    "daily_loss_limit_pct":    ("DAILY_LOSS_LIMIT_PCT", float, 0.5, 50, "Daily loss limit (%)", "Stops new entries until 00:00 UTC after this realized loss."),
    "symbol_cooldown_minutes": ("SYMBOL_COOLDOWN_MINUTES", int, 0, 10_080, "Cooldown per coin (min)", "Wait after closing a coin before re-entering it."),
    "max_hold_bars":           ("MAX_HOLD_BARS", int, 1, 10_000, "Time stop (candles)", "Close an auto trade after this many candles of its timeframe."),
    "tp1_close_fraction":      ("TP1_CLOSE_FRACTION", float, 0, 1, "Sell at TP1 (fraction)", "Share sold at 1R before moving the stop to breakeven (0–1)."),
    "atr_stop_mult":           ("ATR_STOP_MULT", float, 0.3, 10, "Stop distance (× ATR)", "Volatility-scaled stop."),
    "atr_target_mult":         ("ATR_TARGET_MULT", float, 0.3, 20, "Final target (× ATR)", "Volatility-scaled final target (TP2)."),
    "auto_min_quality":        ("AUTO_MIN_QUALITY", int, 0, 100, "Min setup quality", "Share of checklist items that must pass (0–100)."),
    "auto_require_confirmed":  ("AUTO_REQUIRE_CONFIRMED", bool, None, None, "Require closed-candle confirmation", "Don't enter on a still-forming candle."),
    "auto_require_backtest":   ("AUTO_REQUIRE_BACKTEST", bool, None, None, "Require positive backtest", "Only trade coin+timeframes whose backtest made money over ≥20 trades."),
    "auto_timeframes":         ("AUTO_TIMEFRAMES", list, None, None, "Scanned timeframes", "Which candle sizes the auto trader scans."),
}
ALLOWED_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
_DEFAULTS = {k: getattr(config, attr) for k, (attr, *_rest) in FIELDS.items()}


def _coerce(key: str, value):
    attr, typ, lo, hi, label, _help = FIELDS[key]
    if typ is bool:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ is list:
        tfs = [t for t in (value or []) if t in ALLOWED_TIMEFRAMES]
        if not tfs:
            raise ValueError(f"{label}: pick at least one of {', '.join(ALLOWED_TIMEFRAMES)}")
        return [t for t in ALLOWED_TIMEFRAMES if t in tfs]
    try:
        v = typ(float(value)) if typ is int else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: not a number")
    if not lo <= v <= hi:
        raise ValueError(f"{label}: must be between {lo} and {hi}")
    return v


def current() -> dict:
    return {k: getattr(config, attr) for k, (attr, *_rest) in FIELDS.items()}


def schema() -> list[dict]:
    return [{"key": k, "type": typ.__name__, "min": lo, "max": hi, "label": label, "help": help_,
             "default": _DEFAULTS[k], "options": ALLOWED_TIMEFRAMES if typ is list else None}
            for k, (attr, typ, lo, hi, label, help_) in FIELDS.items()]


def apply_saved():
    """Load saved overrides onto config (call once at startup)."""
    for k, v in (db.get_state("settings") or {}).items():
        if k in FIELDS:
            try:
                setattr(config, FIELDS[k][0], _coerce(k, v))
            except ValueError:
                pass   # a value that no longer validates keeps the default


def update(changes: dict) -> tuple[dict, list[str]]:
    """Validate + save + apply. Returns (applied, errors). Nothing is saved if any value is invalid."""
    applied, errors = {}, []
    for k, v in (changes or {}).items():
        if k not in FIELDS:
            errors.append(f"Unknown setting {k}")
            continue
        try:
            applied[k] = _coerce(k, v)
        except ValueError as exc:
            errors.append(str(exc))
    if not errors and ("atr_stop_mult" in applied or "atr_target_mult" in applied):
        stop = applied.get("atr_stop_mult", config.ATR_STOP_MULT)
        target = applied.get("atr_target_mult", config.ATR_TARGET_MULT)
        if target <= stop:
            errors.append("Final target must be further than the stop (target × ATR > stop × ATR)")
    if errors:
        return {}, errors
    saved = {**(db.get_state("settings") or {}), **applied}
    db.set_state("settings", saved)
    for k, v in applied.items():
        setattr(config, FIELDS[k][0], v)
    return applied, []


def reset_defaults():
    db.set_state("settings", {})
    for k, (attr, *_rest) in FIELDS.items():
        setattr(config, attr, _DEFAULTS[k])


# --- auto trade switch ------------------------------------------------------------

def auto_trade_enabled() -> bool:
    return bool(db.get_state("auto_trade", False))


def set_auto_trade(on: bool):
    db.set_state("auto_trade", bool(on))
