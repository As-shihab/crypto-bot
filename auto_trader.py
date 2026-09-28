"""
Auto trader: scans every coin x timeframe, picks the strongest confirmed
long setup, opens it, and manages it to exit — with an email and a SQLite
record for every open and close.

Flow per cycle:
  scan   (every AUTO_SCAN_SECONDS)   — for each coin in config.COINS and each
         timeframe in config.AUTO_TIMEFRAMES, build the live_trade
         recommendation (with a cached backtest for that coin+timeframe),
         keep ENTRY_LONG calls that pass the stricter AUTO_* filters, rank by
         quality, and open the best ones up to MAX_OPEN_TRADES. Also exits
         open trades whose timeframe's last closed candle turned SELL.
  manage (every AUTO_MANAGE_SECONDS) — stop / TP1 (partial + breakeven) /
         TP2 / time stop for every open trade.

Risk controls (config.py): RISK_PER_TRADE_PCT sizing, MAX_POSITION_USD cap,
MAX_OPEN_TRADES, one trade per coin, SYMBOL_COOLDOWN_MINUTES,
DAILY_LOSS_LIMIT_PCT halt, and a pause switch (dashboard or DB state).

Spot, long only. Default TRADING_MODE is "paper" — no real orders. Read the
README "Auto trading" section before switching to testnet or live.

Run standalone: python auto_trader.py   (don't also run it inside dashboard.py)
"""

from __future__ import annotations
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
import db
import notifier
import settings
from backtester import run_backtest
from forecast import run_forecast
from broker import BrokerError, make_broker
from data_fetcher import fetch_ohlcv, fetch_ohlcv_history
from live_trade import HIGHER_TIMEFRAME, build_live_trade, timeframe_seconds
from order_desk import OrderDesk

_CANDLES = max(200, config.EMA_TREND + config.SR_LOOKBACK + 10)


def binance_status() -> dict:
    """What Binance mode would do and whether it's ready (no secrets exposed)."""
    target = config.BINANCE_MODE
    missing = []
    if not config.BINANCE_API_KEY:
        missing.append("BINANCE_API_KEY")
    if not config.BINANCE_API_SECRET:
        missing.append("BINANCE_API_SECRET")
    if target == "live" and config.LIVE_TRADING_CONFIRM != "YES_REAL_MONEY":
        missing.append("LIVE_TRADING_CONFIRM=YES_REAL_MONEY")
    return {"target_mode": target, "ready": not missing, "missing": missing}


def _fmt_local(ts: float) -> str:
    """Display time used in logs/emails: UTC+6 (Asia/Dhaka), 12-hour clock."""
    d = datetime.fromtimestamp(ts, ZoneInfo(config.DISPLAY_TZ))
    return d.strftime("%b %d, %I:%M %p").replace(" 0", " ") + " UTC+6"


def _utc_day_start() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()


class AutoTrader(OrderDesk):
    def __init__(self, broker=None, on_update=None):
        db.init_db()
        settings.apply_saved()          # dashboard-edited limits (SQLite) override config.py defaults
        self._claim_lock()
        # One broker per mode. New trades go to the active mode; open trades of
        # every mode keep being managed with their own broker, so switching
        # Demo <-> Binance never leaves a position without its stop/targets.
        first = broker or make_broker()
        self.brokers = {first.mode: first}
        self.mode = first.mode
        self.on_update = on_update
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._last_alert: dict[str, float] = {}
        self.last_scan: list[dict] = []
        self.last_scan_at: float | None = None
        self.fbot_evals: dict[str, dict] = {}     # symbol -> last forecast-bot evaluation
        self.fbot_run: dict | None = None         # progress of a "Run forecast now" request
        self._last_snapshot: dict[str, float] = {}
        db.log_event("INFO", f"Trader started in {self.mode.upper()} mode — auto trade "
                             f"{'ON' if self.auto_entries else 'OFF (manual trades only)'} "
                             f"(coins={list(config.COINS)}, timeframes={config.AUTO_TIMEFRAMES})")

    # --- single instance -------------------------------------------------------
    # Two traders managing the same open trades would double-sell, so only one
    # process may run a trader against a DB at a time (heartbeat in `state`).

    @staticmethod
    def _pid_alive(pid) -> bool:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True     # exists, owned by someone else
        except (TypeError, ValueError, OSError):
            return False
        return True

    def _claim_lock(self):
        # A lock only counts if its heartbeat is fresh AND that process still exists —
        # otherwise a quick restart after stopping the old one would be refused.
        lock = db.get_state("trader_lock")
        if (lock and lock.get("pid") != os.getpid() and time.time() - lock.get("ts", 0) < 60
                and self._pid_alive(lock.get("pid"))):
            raise RuntimeError(f"Another trader (pid {lock['pid']}) is already running on {config.DB_PATH} — "
                               "stop it first (dashboard and auto_trader.py must not both run).")
        self._heartbeat()

    def _heartbeat(self):
        db.set_state("trader_lock", {"pid": os.getpid(), "ts": time.time()})

    # --- brokers / mode ---------------------------------------------------------

    @property
    def broker(self):
        return self.brokers[self.mode]

    def _broker_for(self, mode: str):
        if mode not in self.brokers:
            self.brokers[mode] = make_broker(mode)
        return self.brokers[mode]

    def set_mode(self, mode: str) -> str | None:
        """Switch where NEW trades go ('paper', 'testnet' or 'live'). Returns None or why it was refused."""
        if mode == self.mode:
            return None
        try:
            with self._lock:
                self._broker_for(mode)          # validates keys / LIVE_TRADING_CONFIRM
                self.mode = mode
        except Exception as exc:
            db.log_event("WARN", f"Switch to {mode.upper()} refused: {exc}")
            return str(exc)
        db.log_event("WARN", f"Trading mode switched to {mode.upper()} — new trades now go to "
                             f"{'Binance' if mode != 'paper' else 'the demo account'}")
        notifier.alert(f"Trading mode switched to {mode.upper()}",
                       f"New trades now use {mode}. Open trades in other modes keep being managed.", mode=mode)
        self._notify_update()
        return None

    # --- auto trade switch (Settings page, stored in SQLite) ----------------------

    @property
    def auto_entries(self) -> bool:
        return settings.auto_trade_enabled()

    def set_auto_trade(self, on: bool):
        settings.set_auto_trade(on)
        msg = (f"Auto trade {'ON' if on else 'OFF'} — the bot will "
               + (f"open and close trades by itself on {self.mode.upper()}" if on
                  else "not open new trades; open ones keep their stops/targets"))
        db.log_event("WARN", msg, mode=self.mode)
        notifier.alert(f"Auto trade switched {'ON' if on else 'OFF'}", msg, mode=self.mode)
        self._notify_update()

    # --- controls --------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return bool(db.get_state("paused", False))

    def pause(self, why: str = "manual"):
        db.set_state("paused", True)
        db.log_event("WARN", f"Trading paused ({why}) — no new entries; open trades still managed")
        self._notify_update()

    def resume(self):
        db.set_state("paused", False)
        db.log_event("INFO", "Trading resumed")
        self._notify_update()

    def stop(self):
        self._stop.set()

    def close_trade_manual(self, trade_id: int):
        with self._lock:
            t = db.get_trade(trade_id)
            if not t or t["status"] != "OPEN":
                return
            self._sell(t, t["qty_open"], "manual close")
        self._notify_update()

    def close_all(self, reason: str = "close all"):
        """Panic button: market-sell every open trade in every mode."""
        with self._lock:
            for t in db.open_trades():
                try:
                    self._sell(t, t["qty_open"], reason)
                except Exception as exc:
                    self._error(f"close {t['symbol']} failed", exc, t["id"])
        self._notify_update()

    # --- account ---------------------------------------------------------------

    def equity(self, mode: str | None = None) -> float:
        mode = mode or self.mode
        b = self._broker_for(mode)
        cash = b.quote_balance()
        held = 0.0
        for t in db.open_trades(mode):
            try:
                held += t["qty_open"] * b.price(t["symbol"])
            except Exception:
                held += t["qty_open"] * t["entry_price"]
        return cash + held

    def _daily_loss_hit(self, mode: str | None = None) -> bool:
        mode = mode or self.mode
        day = _utc_day_start()
        if db.get_state(f"halt_day|{mode}") == day:
            return True
        start_eq = db.get_state(f"day_start_equity|{mode}")
        if not start_eq or start_eq.get("day") != day:
            start_eq = {"day": day, "equity": self.equity(mode)}
            db.set_state(f"day_start_equity|{mode}", start_eq)
        loss = -db.realized_pnl_since(day, mode)
        if loss >= start_eq["equity"] * config.DAILY_LOSS_LIMIT_PCT / 100:
            db.set_state(f"halt_day|{mode}", day)
            msg = (f"Daily loss limit hit: -{loss:.2f} {config.QUOTE_ASSET} "
                   f"(limit {config.DAILY_LOSS_LIMIT_PCT}% of {start_eq['equity']:.2f}). No new entries until 00:00 UTC.")
            db.log_event("WARN", msg, mode=mode)
            notifier.alert("Daily loss limit hit — trading halted for today", msg, mode=mode)
            return True
        return False

    # --- backtest gate ---------------------------------------------------------

    def _backtest(self, symbol: str, timeframe: str) -> dict | None:
        key = f"bt|{symbol}|{timeframe}"
        cached = db.get_state(key)
        if cached and time.time() - cached["ts"] < config.AUTO_BACKTEST_MAX_AGE_HOURS * 3600:
            return cached["result"]
        try:
            r = run_backtest(fetch_ohlcv_history(symbol=symbol, timeframe=timeframe))
            result = {"n_trades": int(r.n_trades), "win_rate": float(r.win_rate),
                      "expectancy_pct": float(r.expectancy_pct), "profit_factor": float(r.profit_factor)}
            summary = (f"{result['n_trades']} trades, win {result['win_rate']:.0f}%, "
                       f"expectancy {result['expectancy_pct']:+.3f}%/trade, PF {result['profit_factor']:.2f}")
        except Exception as exc:
            result = {"error": str(exc)}
            summary = f"error: {exc}"
        db.set_state(key, {"ts": time.time(), "result": result})
        db.log_event("INFO", f"Backtest {symbol} {timeframe}: {summary}")
        return result

    # --- scan ------------------------------------------------------------------

    def scan(self):
        # Network-heavy work (candles, backtests) runs WITHOUT the lock so the
        # manage loop keeps checking stops/targets meanwhile; the lock is only
        # taken to actually open or sell.
        self._signal_exits(db.open_trades())

        rows, candidates = [], []
        held = {t["symbol"] for t in db.open_trades(self.mode)}
        for symbol in config.COINS:
            for tf in config.AUTO_TIMEFRAMES:
                if self._stop.is_set():
                    return
                row = {"symbol": symbol, "timeframe": tf}
                try:
                    df = fetch_ohlcv(symbol=symbol, timeframe=tf, limit=_CANDLES)
                    htf = HIGHER_TIMEFRAME.get(tf)
                    htf_df = fetch_ohlcv(symbol=symbol, timeframe=htf, limit=_CANDLES) if htf else None
                    bt = self._backtest(symbol, tf) if config.AUTO_REQUIRE_BACKTEST else None
                    rec = build_live_trade(df, tf, htf_df=htf_df, backtest=bt)
                    row.update(verdict=rec["verdict"], direction=rec["direction"], quality=rec["quality"],
                               provisional=rec["provisional"], price=rec["entry_price"])
                    skip = self._entry_block_reason(rec, bt, symbol, held)
                    row["skip"] = skip
                    if skip is None:
                        candidates.append((rec["quality"], rec["live_signal"]["confidence"], symbol, tf, rec))
                except Exception as exc:
                    row.update(verdict="ERROR", skip=str(exc))
                rows.append(row)
        self.last_scan, self.last_scan_at = rows, time.time()

        if self.paused or not self.auto_entries or self._daily_loss_hit():
            self._notify_update()
            return
        with self._lock:
            open_now = db.open_trades(self.mode)
            held = {t["symbol"] for t in open_now}
            slots = config.MAX_OPEN_TRADES - len(open_now)
            candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
            for quality, _conf, symbol, tf, rec in candidates:
                if slots <= 0:
                    break
                if symbol in held:
                    continue
                if self._open(symbol, tf, rec):
                    held.add(symbol)
                    slots -= 1
        self._notify_update()

    def open_manual(self, symbol: str, timeframe: str, rec: dict, usd: float | None = None) -> str | None:
        """Open a user-requested long with the same sizing caps, stop and exits as auto trades.
        Returns None on success or the reason it was refused."""
        with self._lock:
            if rec.get("direction") != "BUY" or rec.get("stop_price") is None:
                return "Only a live BUY signal can be opened on spot (no levels for HOLD/SELL)."
            if any(t["symbol"] == symbol for t in db.open_trades(self.mode)):
                return f"Already holding {symbol}."
            if len(db.open_trades(self.mode)) >= config.MAX_OPEN_TRADES:
                return f"Max open trades ({config.MAX_OPEN_TRADES}) reached."
            if self._daily_loss_hit():
                return "Daily loss limit hit — no new entries until 00:00 UTC."
            rec = {**rec, "reasons": ["Manual entry from dashboard"] + rec.get("reasons", [])}
            ok = self._open(symbol, timeframe, rec, usd_override=usd, strategy="manual")
        self._notify_update()
        return None if ok else "Order not placed — see the event log."

    def _entry_block_reason(self, rec: dict, bt: dict | None, symbol: str, held: set) -> str | None:
        if rec["verdict"] != "ENTRY_LONG":
            return "not a long entry" if rec["verdict"] != "ENTRY_SHORT" else "short (spot is long-only)"
        if rec["quality"] < config.AUTO_MIN_QUALITY:
            return f"quality {rec['quality']} < {config.AUTO_MIN_QUALITY}"
        if config.AUTO_REQUIRE_CONFIRMED and rec["provisional"]:
            return "not confirmed by closed candle"
        if config.AUTO_REQUIRE_BACKTEST and not (bt and "error" not in bt and bt["expectancy_pct"] > 0 and bt["n_trades"] >= 20):
            return "backtest edge not proven"
        if symbol in held:
            return "already holding"
        last = db.last_close_time(symbol, self.mode)
        if last and time.time() - last < config.SYMBOL_COOLDOWN_MINUTES * 60:
            return "cooldown"
        return None

    def _signal_exits(self, open_now: list[dict]):
        for t in open_now:
            if t.get("strategy") in ("forecast", "spot"):
                continue
            try:
                df = fetch_ohlcv(symbol=t["symbol"], timeframe=t["timeframe"], limit=_CANDLES)
                rec = build_live_trade(df, t["timeframe"])
                if rec["closed_signal"]["direction"] == "SELL":
                    with self._lock:
                        current = db.get_trade(t["id"])   # may have closed while we fetched
                        if current and current["status"] == "OPEN":
                            self._sell(current, current["qty_open"], f"SELL signal on closed {t['timeframe']} candle")
            except Exception as exc:
                self._error(f"signal-exit check {t['symbol']}", exc, t["id"])

    # --- open ------------------------------------------------------------------

    def _open(self, symbol: str, tf: str, rec: dict, usd_override: float | None = None,
              mode: str | None = None, strategy: str = "rules", usd_exact: float | None = None,
              expires_at: float | None = None) -> bool:
        """Market-buy `symbol` on `mode` (default: the active account) and record the trade.

        Sizing: risk-based (RISK_PER_TRADE_PCT of equity / stop distance, capped by
        MAX_POSITION_USD) unless `usd_exact` is given — the forecast bot invests a
        fixed share of the demo balance instead.
        """
        mode = mode or self.mode
        b = self._broker_for(mode)
        try:
            equity = self.equity(mode)
            cash = b.quote_balance()
            stop_frac = (rec["entry_price"] - rec["stop_price"]) / rec["entry_price"]
            if stop_frac <= 0:
                return False
            # The signal may be a scan (or a Prophet fit) old — don't chase if price already ran.
            now_px = b.price(symbol)
            if abs(now_px - rec["entry_price"]) > 0.5 * (rec["entry_price"] - rec["stop_price"]):
                db.log_event("INFO", f"Skip {symbol} {tf}: price moved {now_px:.4f} vs signal {rec['entry_price']:.4f} "
                                     "(more than half the stop distance)", mode=mode)
                return False
            if usd_exact:
                usd = min(usd_exact, cash * 0.98)
            else:
                usd = equity * config.RISK_PER_TRADE_PCT / 100 / stop_frac
                if usd_override:
                    usd = min(usd, usd_override)
                usd = min(usd, config.MAX_POSITION_USD, cash * 0.98)
            min_usd = b.min_order_usd(symbol)
            if usd < min_usd:
                db.log_event("INFO", f"Skip {symbol} {tf}: size {usd:.2f} < min order {min_usd:.2f} "
                                     f"(cash {cash:.2f}, equity {equity:.2f})", mode=mode)
                return False

            fill = b.market_buy(symbol, usd)
            # Keep the planned distances, re-anchored on the real fill price.
            stop = fill.price - (rec["entry_price"] - rec["stop_price"])
            tp1 = fill.price + (rec["tp1_price"] - rec["entry_price"]) if rec.get("tp1_price") else None
            tp2 = fill.price + (rec["target_price"] - rec["entry_price"])
            trade_id = db.insert_trade(
                mode=mode, symbol=symbol, timeframe=tf, side="LONG", status="OPEN", strategy=strategy,
                quality=rec["quality"],
                reasons=rec["reasons"] + [f"{c['label']}: {c['detail']}" for c in rec.get("checks", []) if c["ok"]],
                qty=fill.qty, qty_open=fill.qty, entry_price=fill.price, cost_usd=fill.quote,
                stop_price=stop, initial_stop=stop, tp1_price=tp1, target_price=tp2,
                entry_time=time.time(), entry_order_id=fill.order_id, fees_usd=fill.fee_usd, expires_at=expires_at,
            )
            source = {"rules": "auto trader", "forecast": "forecast bot", "manual": "manual entry"}.get(strategy, strategy)
            oid = db.insert_order(mode=mode, symbol=symbol, side="BUY", type="MARKET", qty=fill.qty, status="FILLED",
                                  filled_qty=fill.qty, avg_price=fill.price, fee_usd=fill.fee_usd, trade_id=trade_id,
                                  tp_price=tp2, sl_price=stop, note=source)
            db.add_fill(mode, symbol, "BUY", fill.qty, fill.price, fill.quote, fill.fee_usd, source,
                        trade_id=trade_id, order_id=oid)
            db.log_event("TRADE", f"OPEN LONG {symbol} {tf} [{strategy}] qty={fill.qty:.8f} @ {fill.price:.4f} "
                                  f"cost={fill.quote:.2f} stop={stop:.4f}"
                                  + (f" tp1={tp1:.4f}" if tp1 else "") + f" target={tp2:.4f}", trade_id)

            if b.has_exchange_stops:
                try:
                    db.update_trade(trade_id, stop_order_id=b.place_stop(symbol, fill.qty, stop))
                except Exception as exc:
                    # Never hold a position without a stop on the exchange.
                    self._error(f"placing stop for {symbol} failed — closing position", exc, trade_id)
                    self._sell(db.get_trade(trade_id), fill.qty, "could not place protective stop")
                    return False

            t = db.get_trade(trade_id)
            t["reasons_list"] = json.loads(t["reasons"] or "[]")
            notifier.trade_opened(t)
            return True
        except Exception as exc:
            self._error(f"open {symbol} {tf} failed", exc)
            return False

    # --- manage ----------------------------------------------------------------

    def manage(self):
        with self._lock:
            self.process_orders()
            for t in db.open_trades():
                try:
                    self._manage_one(t)
                except Exception as exc:
                    self._error(f"manage {t['symbol']} failed", exc, t["id"])
        for mode in {"paper", self.mode}:
            self._snapshot(mode)
        self._notify_update()

    def _manage_one(self, t: dict):
        symbol = t["symbol"]
        b = self._broker_for(t["mode"])
        if b.has_exchange_stops:
            fill = b.stop_fill(symbol, t["stop_order_id"])
            if fill:
                self._record_sell(t, fill, "stop hit (exchange order)")
                return

        price = b.price(symbol)

        if price <= t["stop_price"]:
            gapped = price <= t["stop_price"] * (1 - 2 * config.STOP_LIMIT_BUFFER_PCT / 100)
            if not b.has_exchange_stops or gapped:
                reason = "stop hit" if not b.has_exchange_stops else "stop gapped past stop-limit"
                self._sell(t, t["qty_open"], reason + (" (breakeven)" if t["tp1_done"] else ""))
            return   # otherwise the exchange stop is working it

        if t["target_price"] and price >= t["target_price"]:
            self._sell(t, t["qty_open"], "TP2 target hit")
            return

        if not t["tp1_done"] and t["tp1_price"] and price >= t["tp1_price"]:
            self._take_tp1(t, price)
            return

        if t.get("expires_at") and time.time() >= t["expires_at"]:
            self._sell(t, t["qty_open"], "forecast horizon reached")
            return

        if t.get("strategy") == "spot":
            return   # a holding bought from the order form: only its own TP/SL (if any) close it

        max_hold = config.MAX_HOLD_BARS * timeframe_seconds(t["timeframe"])
        if time.time() - t["entry_time"] >= max_hold:
            self._sell(t, t["qty_open"], f"time stop ({config.MAX_HOLD_BARS} x {t['timeframe']})")

    def _take_tp1(self, t: dict, price: float):
        b = self._broker_for(t["mode"])
        part = t["qty_open"] * config.TP1_CLOSE_FRACTION
        min_usd = b.min_order_usd(t["symbol"])
        rest_ok = (t["qty_open"] - part) * price >= min_usd
        if part * price >= min_usd and rest_ok:
            self._sell(t, part, "TP1 partial", final=False)
            t = db.get_trade(t["id"])
        # Breakeven stop: entry plus round-trip fees, so the rest can't turn into a loss.
        be = t["entry_price"] * (1 + 2 * config.TAKER_FEE)
        new_stop = max(t["stop_price"], be)
        update = {"tp1_done": 1, "stop_price": new_stop}
        if b.has_exchange_stops:
            b.cancel_stop(t["symbol"], t["stop_order_id"])
            update["stop_order_id"] = b.place_stop(t["symbol"], t["qty_open"], new_stop)
        db.update_trade(t["id"], **update)
        db.log_event("TRADE", f"TP1 {t['symbol']} @ {price:.4f} — stop moved to breakeven {new_stop:.4f}", t["id"])

    # --- sells -----------------------------------------------------------------

    def _sell(self, t: dict, qty: float, reason: str, final: bool = True, order_id: int | None = None):
        """Market-sell `qty` of trade `t`; returns the Fill (None if the record was closed without an order)."""
        b = self._broker_for(t["mode"])
        if b.has_exchange_stops and t.get("stop_order_id"):
            b.cancel_stop(t["symbol"], t["stop_order_id"])
            t["stop_order_id"] = None
            db.update_trade(t["id"], stop_order_id=None)
        try:
            fill = b.market_sell(t["symbol"], qty)
        except BrokerError as exc:
            if final:
                # e.g. nothing left to sell (sold manually on the exchange) — close the record at the live price.
                db.log_event("WARN", f"{t['symbol']}: {exc} — closing record without an order", t["id"])
                px = b.price(t["symbol"])
                q = t["qty_open"]
                self._finalize(t, reason + " (no order — P&L estimated at market)", extra_qty=q,
                               extra_value=q * px, extra_quote=q * px * (1 - config.TAKER_FEE), fee=0.0, px=px)
                return None
            raise
        self._record_sell(t, fill, reason, final=final, order_id=order_id)
        return fill

    def _record_sell(self, t: dict, fill, reason: str, final: bool = True, order_id: int | None = None):
        if order_id is None:   # not from the order form: the bot's own exit — record it as an order too
            order_id = db.insert_order(mode=t["mode"], symbol=t["symbol"], side="SELL", type="MARKET", qty=fill.qty,
                                       status="FILLED", filled_qty=fill.qty, avg_price=fill.price,
                                       fee_usd=fill.fee_usd, trade_id=t["id"], note=reason)
        db.add_fill(t["mode"], t["symbol"], "SELL", fill.qty, fill.price, fill.quote, fill.fee_usd,
                    reason, trade_id=t["id"], order_id=order_id)
        if final or fill.qty >= t["qty_open"] * 0.999:
            self._finalize(t, reason, fill.qty, fill.qty * fill.price, fill.quote, fill.fee_usd, fill.price)
            return
        db.update_trade(
            t["id"],
            qty_open=t["qty_open"] - fill.qty,
            sold_qty=t["sold_qty"] + fill.qty,
            sold_value=t["sold_value"] + fill.qty * fill.price,
            realized_usd=t["realized_usd"] + fill.quote,
            fees_usd=t["fees_usd"] + fill.fee_usd,
        )
        db.log_event("TRADE", f"{reason}: sold {fill.qty:.8f} {t['symbol']} @ {fill.price:.4f} (+{fill.quote:.2f})", t["id"])

    def _finalize(self, t, reason, extra_qty, extra_value, extra_quote, fee, px):
        sold_qty = t["sold_qty"] + extra_qty
        sold_value = t["sold_value"] + extra_value
        realized = t["realized_usd"] + extra_quote
        pnl = realized - t["cost_usd"]
        db.update_trade(
            t["id"], status="CLOSED", qty_open=0.0, sold_qty=sold_qty, sold_value=sold_value,
            realized_usd=realized, fees_usd=t["fees_usd"] + fee,
            exit_price=(sold_value / sold_qty) if sold_qty else px, exit_time=time.time(),
            exit_reason=reason, pnl_usd=pnl, pnl_pct=pnl / t["cost_usd"] * 100 if t["cost_usd"] else 0.0,
            stop_order_id=None,
        )
        closed = db.get_trade(t["id"])
        self._last_snapshot.pop(t["mode"], None)   # next manage() records the new equity
        db.log_event("TRADE", f"CLOSE {t['symbol']} {t['timeframe']} — {reason} — P&L {pnl:+.2f} "
                              f"({closed['pnl_pct']:+.2f}%)", t["id"])
        notifier.trade_closed(closed)

    # --- demo account ----------------------------------------------------------

    def demo_funds(self, action: str, amount: float) -> str | None:
        """Deposit / withdraw / reset the demo (paper) account. Returns None or the error."""
        b = self._broker_for("paper")
        try:
            with self._lock:
                getattr(b, action)(float(amount))
        except Exception as exc:
            return str(exc)
        db.log_event("INFO", f"Demo account {action} {float(amount):.2f} — cash now {b.quote_balance():.2f}", mode="paper")
        self._snapshot("paper", force=True)
        self._notify_update()
        return None

    def _snapshot(self, mode: str, force: bool = False):
        """Equity curve point every 5 minutes per mode (and on every fund change / close)."""
        now = time.time()
        if not force and now - self._last_snapshot.get(mode, 0) < 300:
            return
        try:
            b = self._broker_for(mode)
            db.add_equity_snapshot(mode, b.quote_balance(), self.equity(mode))
            self._last_snapshot[mode] = now
        except Exception:
            traceback.print_exc()

    # --- forecast bot (demo account) -------------------------------------------
    # Trades the demo account from the Prophet forecast alone: once per candle of
    # the chosen horizon it re-fits each selected coin and buys when the projected
    # move beats costs and is large relative to the forecast's own uncertainty.
    # Exit at the forecast target, the lower uncertainty band (stop), or when the
    # horizon is reached — whichever comes first.

    FBOT_DEFAULTS = {"enabled": False, "coins": ["BTC/USDT", "ETH/USDT"], "horizon": "1h",
                     "alloc_pct": 20.0, "min_snr": 0.3, "min_move_pct": 0.6, "max_open": 3}

    def fbot_settings(self) -> dict:
        return {**self.FBOT_DEFAULTS, **(db.get_state("fbot") or {})}

    def set_fbot_settings(self, new: dict) -> str | None:
        cur = self.fbot_settings()
        try:
            if "enabled" in new:
                cur["enabled"] = bool(new["enabled"])
            if "coins" in new:
                coins = [c for c in new["coins"] if c in config.COINS]
                if not coins:
                    return "Pick at least one coin"
                cur["coins"] = coins
            if "horizon" in new:
                if new["horizon"] not in config.FORECAST_HORIZONS:
                    return f"Unknown horizon {new['horizon']}"
                if new["horizon"] != cur["horizon"]:
                    self.fbot_evals = {}
                cur["horizon"] = new["horizon"]
            for key, lo, hi in (("alloc_pct", 1, 100), ("min_snr", 0, 10), ("min_move_pct", 0, 50), ("max_open", 1, 10)):
                if key in new:
                    v = float(new[key])
                    if not lo <= v <= hi:
                        return f"{key} must be between {lo} and {hi}"
                    cur[key] = int(v) if key == "max_open" else v
        except (TypeError, ValueError) as exc:
            return f"Bad setting: {exc}"
        db.set_state("fbot", cur)
        db.log_event("INFO", f"Forecast bot settings: {'ON' if cur['enabled'] else 'OFF'}, {cur['horizon']}, "
                             f"{', '.join(cur['coins'])}, invest {cur['alloc_pct']:.0f}%/trade, "
                             f"min SNR {cur['min_snr']}, min move {cur['min_move_pct']}%", mode="paper")
        self._notify_update()
        return None

    def _fbot_evaluate(self, symbol: str, s: dict) -> dict:
        horizon = s["horizon"]
        fc = run_forecast(symbol, horizon)
        b = self._broker_for("paper")
        price = b.price(symbol)
        hp = next((p for p in fc["forecast"] if p["time"] == fc.get("horizon_time")), None)
        band = fc["fit_details"]["uncertainty_band_pct_of_price"] / 2.0
        change = fc["projected_change_pct"]
        snr = change / band if band > 0 else 0.0
        cost_pct = (config.TAKER_FEE + config.SLIPPAGE) * 2 * 100
        min_move = s["min_move_pct"]            # exactly what the user set — no hidden floor
        tf_sec = timeframe_seconds(fc["timeframe"])
        ev = {
            "symbol": symbol, "horizon": horizon, "timeframe": fc["timeframe"], "ts": time.time(),
            "last_time": fc.get("last_time"), "price": price, "change_pct": change, "band_pct": band,
            "snr": snr, "target": hp["yhat"] if hp else None, "lower": hp["yhat_lower"] if hp else None,
            # The horizon point is a candle's close, reached at its open time + one candle.
            "expires_at": (fc["horizon_time"] + tf_sec) if fc.get("horizon_time") else None,
            "decision": "SKIP",
        }
        # Check everything and report every failing rule, not just the first.
        fails = []
        if hp is None:
            fails.append("forecast returned no horizon point")
        if change <= 0:
            fails.append(f"forecast down {change:+.2f}% (spot is long-only)")
        elif change < min_move:
            fails.append(f"move {change:+.2f}% < your min {min_move:.2f}%")
        if snr < s["min_snr"]:
            fails.append(f"signal/noise {snr:.2f} < your min {s['min_snr']} (move {change:+.2f}% vs ±{band:.2f}% band)")
        if fails:
            ev["reason"] = "; ".join(fails)
        else:
            ev["decision"] = "BUY"
            ev["reason"] = f"move {change:+.2f}% ≥ {min_move:.2f}%, signal/noise {snr:.2f} ≥ {s['min_snr']}"
            if change < cost_pct:
                ev["reason"] += (f" — note: below the ≈{cost_pct:.2f}% round-trip fees+slippage, "
                                 "so even a correct forecast loses money")
        return ev

    def _fbot_cycle(self):
        s = self.fbot_settings()
        if not s["enabled"]:
            return
        tf = config.FORECAST_HORIZONS[s["horizon"]]["timeframe"]
        tf_sec = timeframe_seconds(tf)
        candle_open = int(time.time() // tf_sec * tf_sec)
        for symbol in s["coins"]:
            if self._stop.is_set() or not self.fbot_settings()["enabled"]:
                return
            prev = self.fbot_evals.get(symbol)
            # One fit per candle of the horizon's timeframe (last_time = open of the candle it was fitted through).
            if prev and prev["horizon"] == s["horizon"] and (prev.get("last_time") or 0) >= candle_open:
                continue
            try:
                ev = self._fbot_evaluate(symbol, s)
            except Exception as exc:
                self._error(f"forecast bot {symbol}", exc)
                continue
            self.fbot_evals[symbol] = ev
            db.log_event("INFO", f"Forecast bot {symbol} {s['horizon']}: {ev['decision']} — {ev['reason']}", mode="paper")
            if ev["decision"] == "BUY":
                ev["opened"] = self._fbot_open(ev, s)
            self._notify_update()

    def fbot_run_now(self) -> str | None:
        """Re-fit every selected coin immediately (skips the once-per-candle wait).
        Trades BUY decisions only if the bot is ON; otherwise just shows the decisions."""
        if self.fbot_run and self.fbot_run.get("running"):
            return "A forecast run is already in progress"
        coins = self.fbot_settings()["coins"]
        self.fbot_run = {"running": True, "done": 0, "total": len(coins), "started": time.time()}
        threading.Thread(target=self._fbot_run_all, args=(coins,), daemon=True).start()
        self._notify_update()
        return None

    def _fbot_run_all(self, coins: list[str]):
        s = self.fbot_settings()
        db.log_event("INFO", f"Forecast bot: manual run for {', '.join(coins)} ({s['horizon']}), "
                             f"{'will trade BUY decisions' if s['enabled'] else 'bot OFF — decisions only'}", mode="paper")
        try:
            for symbol in coins:
                try:
                    ev = self._fbot_evaluate(symbol, s)
                    ev["manual"] = True
                    self.fbot_evals[symbol] = ev
                    db.log_event("INFO", f"Forecast bot {symbol} {s['horizon']} (run now): {ev['decision']} — {ev['reason']}", mode="paper")
                    if ev["decision"] == "BUY":
                        if s["enabled"]:
                            ev["opened"] = self._fbot_open(ev, s)
                        else:
                            ev["reason"] += " — bot is OFF, not traded"
                except Exception as exc:
                    self._error(f"forecast bot {symbol}", exc)
                self.fbot_run["done"] += 1
                self._notify_update()
        finally:
            self.fbot_run["running"] = False
            self.fbot_run["finished"] = time.time()
            self._notify_update()

    def _fbot_open(self, ev: dict, s: dict) -> bool:
        with self._lock:
            open_demo = db.open_trades("paper")
            if any(t["symbol"] == ev["symbol"] for t in open_demo):
                ev["reason"] += " — already holding"
                return False
            if sum(1 for t in open_demo if t.get("strategy") == "forecast") >= s["max_open"]:
                ev["reason"] += f" — max {s['max_open']} forecast trades open"
                return False
            if self.paused:
                ev["reason"] += " — trading paused"
                return False
            if self._daily_loss_hit("paper"):
                ev["reason"] += " — daily loss limit hit"
                return False
            price = ev["price"]
            # Stop at the forecast's lower band, kept within 0.3%..10% of price.
            stop = min(max(ev["lower"], price * 0.90), price * 0.997)
            rec = {
                "entry_price": price, "stop_price": stop, "tp1_price": None, "target_price": ev["target"],
                "quality": min(100.0, ev["snr"] * 100),
                "reasons": [f"Forecast bot ({ev['horizon']}): {ev['reason']}",
                            f"Target = forecast {ev['target']:.4f}, stop = lower band {stop:.4f}, "
                            f"exit by {_fmt_local(ev['expires_at'])}"],
            }
            usd = self._broker_for("paper").quote_balance() * s["alloc_pct"] / 100
            return self._open(ev["symbol"], ev["timeframe"], rec, mode="paper", strategy="forecast",
                              usd_exact=usd, expires_at=ev["expires_at"])

    def _fbot_loop(self):
        while not self._stop.is_set():
            try:
                self._fbot_cycle()
            except Exception as exc:
                self._error("forecast bot loop", exc)
            self._stop.wait(20)

    def account_status(self) -> dict:
        """Demo account view: funds, equity, ledger, trades, bot, logs."""
        b = self._broker_for("paper")
        cash = b.quote_balance()
        open_demo = db.open_trades("paper")
        for t in open_demo:
            try:
                t["price"] = b.price(t["symbol"])
            except Exception:
                t["price"] = t["entry_price"]
            t["unrealized_usd"] = t["realized_usd"] + t["qty_open"] * t["price"] * (1 - config.TAKER_FEE) - t["cost_usd"]
        invested = sum(t["qty_open"] * t["price"] for t in open_demo)
        equity = cash + invested
        deposits = db.net_deposits("paper")
        since = db.last_reset_ts("paper")
        closed = [t for t in db.closed_trades("paper", 500) if (t["exit_time"] or 0) >= since]
        wins = [t for t in closed if (t["pnl_usd"] or 0) > 0]
        s = self.fbot_settings()
        return {
            "cash": cash, "invested": invested, "equity": equity, "net_deposits": deposits,
            "pnl_usd": equity - deposits, "return_pct": (equity - deposits) / deposits * 100 if deposits else 0.0,
            "closed_count": len(closed), "win_rate": 100 * len(wins) / len(closed) if closed else 0.0,
            "realized_usd": sum(t["pnl_usd"] or 0 for t in closed),
            "open": open_demo,
            "trades": db.all_trades("paper", 300),
            "ledger": db.ledger("paper", 300),
            "equity_curve": db.equity_curve("paper", since),
            "events": db.recent_events(150, mode="paper"),
            "fbot": {**s, "evals": sorted(self.fbot_evals.values(), key=lambda e: -e["ts"]), "run": self.fbot_run,
                     "horizons": list(config.FORECAST_HORIZONS), "coins_all": list(config.COINS)},
            "paused": self.paused,
        }

    # --- helpers ---------------------------------------------------------------

    def _error(self, what: str, exc: Exception, trade_id: int | None = None):
        traceback.print_exc()
        msg = f"{what}: {exc}"
        db.log_event("ERROR", msg, trade_id)
        # Email each distinct error at most every 15 minutes.
        if time.time() - self._last_alert.get(what, 0) > 900:
            self._last_alert[what] = time.time()
            notifier.alert(f"Trader error — {what}", msg, mode=self.mode)

    def status(self) -> dict:
        open_list = db.open_trades()
        for t in open_list:
            try:
                px = self._broker_for(t["mode"]).price(t["symbol"])
            except Exception:
                px = t["entry_price"]
            t["price"] = px
            t["unrealized_usd"] = t["realized_usd"] + t["qty_open"] * px * (1 - config.TAKER_FEE) - t["cost_usd"]
        try:
            cash = self.broker.quote_balance()
        except Exception:
            cash = None
        return {
            "mode": self.mode,
            "auto_entries": self.auto_entries,
            "paused": self.paused,
            "halted_today": db.get_state(f"halt_day|{self.mode}") == _utc_day_start(),
            "cash": cash,
            "equity": (cash or 0) + sum(t["qty_open"] * t["price"] for t in open_list if t["mode"] == self.mode),
            "binance": binance_status(),
            "desk": self.desk_status(),
            "today_pnl": db.realized_pnl_since(_utc_day_start(), self.mode),
            "stats": db.stats(self.mode),
            "open": open_list,
            "closed": db.closed_trades(self.mode, 30),
            "events": db.recent_events(30),
            "scan": self.last_scan,
            "scan_at": self.last_scan_at,
            "email": notifier.email_configured(),
            "limits": {
                "risk_pct": config.RISK_PER_TRADE_PCT, "max_position_usd": config.MAX_POSITION_USD,
                "max_open": config.MAX_OPEN_TRADES, "daily_loss_pct": config.DAILY_LOSS_LIMIT_PCT,
                "min_quality": config.AUTO_MIN_QUALITY, "timeframes": config.AUTO_TIMEFRAMES,
            },
        }

    def _notify_update(self):
        if self.on_update:
            try:
                self.on_update()
            except Exception:
                traceback.print_exc()

    # --- loop ------------------------------------------------------------------

    def _scan_loop(self):
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as exc:
                self._error("scan loop", exc)
            self._stop.wait(config.AUTO_SCAN_SECONDS)

    def run_forever(self):
        """Manage loop here (stops/targets every AUTO_MANAGE_SECONDS); scanning runs on its own thread."""
        threading.Thread(target=self._scan_loop, daemon=True).start()
        threading.Thread(target=self._fbot_loop, daemon=True).start()
        while not self._stop.is_set():
            try:
                self._heartbeat()
                self.manage()
            except Exception as exc:
                self._error("trader loop", exc)
            self._stop.wait(config.AUTO_MANAGE_SECONDS)


if __name__ == "__main__":
    trader = AutoTrader()
    if not trader.auto_entries:
        print("Auto trade is OFF (Settings page) — only managing open trades. Switch it on in the dashboard.")
    notifier.alert(f"Auto trader started ({trader.mode})",
                   f"Mode {trader.mode}, equity {trader.equity():.2f} {config.QUOTE_ASSET}", mode=trader.mode)
    try:
        trader.run_forever()
    except KeyboardInterrupt:
        db.log_event("INFO", "Auto trader stopped (Ctrl+C) — open trades stay open; exchange stops remain in place")
