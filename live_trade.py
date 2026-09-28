"""
Real-time trade recommendation for one symbol on one timeframe.

Where signal_engine answers "what did the last *closed* candle say?", this
module answers "if I took a trade right now, on the timeframe I'm looking
at, what would it be?" It:

  - scores the still-forming candle using the latest live price
    (provisional — it can flip before the candle closes),
  - checks it against the last closed candle, the next higher timeframe's
    trend, the Prophet forecast for the matching horizon, and the backtest
    expectancy on this same timeframe,
  - and turns the result into a concrete plan: entry, stop, two targets,
    reward:risk and how long the setup is valid (until the candle closes).

Same honesty rules as the rest of the bot: the quality score is the share
of independent checks that agree, not a probability of winning. Read
backtester.py output for the real historical hit rate on this timeframe.
"""

from __future__ import annotations
import ccxt
import pandas as pd

import config
from indicators import add_all_indicators
from signal_engine import signal_at

# Timeframe -> the next larger one used as a trend filter.
HIGHER_TIMEFRAME = {
    "1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h",
    "1h": "4h", "4h": "1d", "1d": "1w",
}

# Minimum share of available checks (0-100) that must agree before a
# BUY/SELL becomes an ENTRY call instead of WAIT.
MIN_QUALITY = 60

# Fewer backtest trades than this is too small a sample to call an edge either way.
MIN_BACKTEST_TRADES = 20


def timeframe_seconds(timeframe: str) -> int:
    return int(ccxt.Exchange.parse_timeframe(timeframe))


def _signal_dict(sig) -> dict:
    return {
        "timestamp": str(sig.timestamp),
        "direction": sig.direction,
        "confidence": sig.confidence,
        "net_score": sig.net_score,
        "price": sig.price,
        "reasons": sig.reasons,
    }


def _with_live_price(df: pd.DataFrame, live_price: float | None) -> pd.DataFrame:
    """Overwrite the forming candle's close (and stretch high/low) with the latest tick."""
    if live_price is None or df.empty:
        return df
    df = df.copy()
    last = df.index[-1]
    df.loc[last, "close"] = live_price
    df.loc[last, "high"] = max(df.loc[last, "high"], live_price)
    df.loc[last, "low"] = min(df.loc[last, "low"], live_price)
    return df


def _htf_trend(htf_df: pd.DataFrame | None) -> str | None:
    """'up' / 'down' / 'flat' from the higher timeframe's last closed candle EMA stack."""
    if htf_df is None or len(htf_df) < config.EMA_TREND + 2:
        return None
    row = add_all_indicators(htf_df).iloc[-2]
    if row.ema_fast > row.ema_slow and row.close > row.ema_trend:
        return "up"
    if row.ema_fast < row.ema_slow and row.close < row.ema_trend:
        return "down"
    return "flat"


def build_live_trade(
    df: pd.DataFrame,
    timeframe: str,
    live_price: float | None = None,
    htf_df: pd.DataFrame | None = None,
    forecast: dict | None = None,
    backtest: dict | None = None,
) -> dict:
    """
    df: raw OHLCV for `timeframe`, last row = the candle still forming.
    forecast: forecast.run_forecast() result for the matching horizon, or None.
    backtest: dashboard backtest result dict for this symbol+timeframe, or None.
    """
    df = _with_live_price(df, live_price)
    enriched = add_all_indicators(df)
    n = len(enriched)
    if n < max(config.EMA_TREND, config.SR_LOOKBACK) + 3:
        raise ValueError("Not enough candles to compute stable indicators (need ~60+).")

    live = signal_at(enriched, n - 1)
    closed = signal_at(enriched, n - 2)
    price = float(enriched["close"].iloc[-1])
    atr_val = float(enriched["atr"].iloc[-2])
    window = enriched.iloc[max(0, n - 1 - config.SR_LOOKBACK):n - 1]
    support, resistance = float(window["low"].min()), float(window["high"].max())

    direction = live.direction
    side = 1 if direction == "BUY" else (-1 if direction == "SELL" else 0)
    checks: list[dict] = []

    def check(label: str, ok: bool | None, detail: str):
        checks.append({"label": label, "ok": ok, "detail": detail})

    # 1. Closed candle already agrees (not just the provisional one)?
    check(
        "Last closed candle agrees",
        None if side == 0 else closed.direction == direction,
        f"Closed candle: {closed.direction} ({closed.confidence:.0f}/100)",
    )

    # 2. Higher timeframe trend.
    htf = HIGHER_TIMEFRAME.get(timeframe)
    trend = _htf_trend(htf_df)
    if trend is None or side == 0:
        check(f"Higher timeframe ({htf or '—'}) trend", None, "Not available" if trend is None else f"{htf} trend {trend}")
    else:
        check(
            f"Higher timeframe ({htf}) trend",
            (trend == "up") if side > 0 else (trend == "down"),
            f"{htf} trend {trend}",
        )

    # 3. Forecast for the matching horizon.
    fc_out = None
    if forecast and "error" not in forecast:
        fc_change = forecast["projected_change_pct"]
        band = forecast.get("fit_details", {}).get("uncertainty_band_pct_of_price", 0.0) / 2.0
        horizon_point = next((p for p in forecast["forecast"] if p["time"] == forecast.get("horizon_time")), None)
        fc_out = {
            "horizon": forecast["horizon"],
            "change_pct": fc_change,
            "band_pct": band,
            "price": horizon_point["yhat"] if horizon_point else None,
            "time": forecast.get("horizon_time"),
        }
        check(
            f"Forecast ({forecast['horizon']}) agrees",
            None if side == 0 else (fc_change > 0) == (side > 0),
            f"{fc_change:+.2f}% projected (±{band:.2f}% band)",
        )
    else:
        check("Forecast agrees", None, "Forecast not run yet for this horizon")

    # 4. Backtest edge on this exact timeframe.
    if backtest and "error" not in backtest:
        detail = f"{backtest['expectancy_pct']:+.3f}%/trade, win rate {backtest['win_rate']:.1f}% over {backtest['n_trades']} trades"
        if backtest["n_trades"] < MIN_BACKTEST_TRADES:
            check(f"Backtest edge on {timeframe}", None, detail + f" — too few trades (<{MIN_BACKTEST_TRADES}) to judge")
        else:
            check(f"Backtest edge on {timeframe}", backtest["expectancy_pct"] > 0, detail)
    else:
        check(f"Backtest edge on {timeframe}", None, "Backtest not run yet for this timeframe")

    # 5. Room to run: distance to the opposing swing level vs the stop distance.
    stop_dist = config.ATR_STOP_MULT * atr_val
    if side != 0 and stop_dist > 0:
        room = (resistance - price) if side > 0 else (price - support)
        level = "resistance" if side > 0 else "support"
        check(
            "Room to first target",
            room >= stop_dist,
            f"{room / stop_dist:.1f}R to swing {level} ({resistance if side > 0 else support:.4f})",
        )
    else:
        check("Room to first target", None, "No direction")

    # 6. Volatility sanity: the final target must clear round-trip costs with room
    # to spare (too quiet = fees eat it), and ATR not so wild the stop is noise.
    atr_pct = atr_val / price * 100 if price else 0.0
    cost_pct = (config.TAKER_FEE + config.SLIPPAGE) * 2 * 100
    target_pct = config.ATR_TARGET_MULT * atr_pct
    check(
        "Volatility tradeable",
        target_pct >= 2 * cost_pct and atr_pct <= 8.0,
        f"TP2 move {target_pct:.2f}% vs {cost_pct:.2f}% round-trip fees+slippage (ATR {atr_pct:.2f}%)",
    )

    rated = [c for c in checks if c["ok"] is not None]
    passed = sum(1 for c in rated if c["ok"])
    quality = round(100 * passed / len(rated)) if rated and side != 0 else 0

    reasons = [f"Live {timeframe} signal: {direction} ({live.confidence:.0f}/100, provisional until candle close)"]
    by_label = {c["label"].split(" (")[0]: c for c in checks}
    if side == 0:
        verdict = "WAIT"
        lean = "bullish" if live.net_score > 0 else ("bearish" if live.net_score < 0 else "neutral")
        reasons.append(
            f"Leaning {lean} ({abs(live.net_score):.0f}/100) but below the {config.MIN_CONFIDENCE} threshold — nothing to take right now."
        )
    elif by_label.get("Backtest edge on " + timeframe, {}).get("ok") is False:
        verdict = "AVOID"
        reasons.append(f"Backtest shows no positive expectancy on {timeframe} — this signal hasn't paid historically.")
    elif by_label.get("Higher timeframe", {}).get("ok") is False or by_label.get("Forecast", {}).get("ok") is False:
        verdict = "WAIT"
        reasons.append("Signal conflicts with the higher-timeframe trend or the forecast — wait for alignment.")
    elif quality < MIN_QUALITY:
        verdict = "WAIT"
        reasons.append(f"Only {passed}/{len(rated)} checks agree (quality {quality} < {MIN_QUALITY}) — weak setup.")
    else:
        verdict = "ENTRY_LONG" if side > 0 else "ENTRY_SHORT"
        reasons.append(f"{passed}/{len(rated)} checks agree (quality {quality}).")

    tf_sec = timeframe_seconds(timeframe)
    candle_open = int(enriched.index[-1].timestamp())
    last_row = df.iloc[-1]
    result = {
        "timeframe": timeframe,
        "verdict": verdict,
        "direction": direction,
        "lean": "BUY" if live.net_score > 0 else ("SELL" if live.net_score < 0 else "HOLD"),
        "lean_score": live.net_score,
        "quality": quality,
        "provisional": closed.direction != direction,
        "entry_price": price,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "support": support,
        "resistance": resistance,
        "candle_close_at": candle_open + tf_sec,
        "candle": {
            "time": candle_open,
            "open": float(last_row.open), "high": float(last_row.high),
            "low": float(last_row.low), "close": float(last_row.close),
        },
        "live_signal": _signal_dict(live),
        "closed_signal": _signal_dict(closed),
        "higher_timeframe": {"timeframe": htf, "trend": trend},
        "forecast": fc_out,
        "checks": checks,
        "reasons": reasons,
    }

    # Levels whenever there's a direction, so "if I took it now" is always answerable.
    if side != 0 and atr_val > 0:
        result["stop_price"] = price - side * stop_dist
        result["tp1_price"] = price + side * stop_dist                        # 1R: take partial, move stop to entry
        result["target_price"] = price + side * config.ATR_TARGET_MULT * atr_val
        result["risk_reward"] = config.ATR_TARGET_MULT / config.ATR_STOP_MULT
        result["risk_pct"] = stop_dist / price * 100
    return result
