"""
Web dashboard for the analysis bot.

Runs a Flask + Socket.IO server (default port 3100):
  - Live candlestick chart per coin (BTC/BNB/LTC/... from config.COINS),
    updated in real time over a websocket.
  - Real-time trade recommendation (live_trade.py) for whichever coin and
    timeframe the viewer has selected: provisional signal on the forming
    candle, checklist, entry/stop/targets, candle-close countdown.
  - On-demand backtest (backtester.py) for the selected coin + timeframe.
  - On-demand Prophet forecast (forecast.py) for the selected horizon.
  - Auto trade panel (auto_trader.py), switched on/off in Settings: status,
    open/closed trades from MySQL, pause/resume, manual close.

Places orders from the Trade page order form, and by itself when Auto trade is ON in Settings.
Two modes, switched on the Trade page: Demo (simulated) and Real (Binance, keys in Settings > Credentials).

Run: python dashboard.py
"""

from __future__ import annotations
import threading
import time
import traceback
from collections import deque

import os

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

import config
from data_fetcher import get_exchange, fetch_ohlcv, fetch_ohlcv_history
from indicators import add_all_indicators
from backtester import run_backtest
from forecast import run_forecast
from live_trade import HIGHER_TIMEFRAME, build_live_trade

app = Flask(__name__)
app.json.sort_keys = False   # keep config.COINS / TIME_OPTIONS order for the frontend
# Same-origin sockets only: this server can pause trading / close positions,
# so no other web page open in your browser may connect to it.
socketio = SocketIO(app, async_mode="threading")
_trader = None        # AutoTrader: manages manual + auto trades; opens trades itself only when Auto trade is ON (Settings, MySQL)
_trader_error = None  # why the trader couldn't start (bad keys, another trader running, ...)
_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

_lock = threading.Lock()
_backtest_cache: dict[str, dict] = {}   # f"{symbol}|{timeframe}" -> {"running": bool, "result": ...}
_forecast_cache: dict[str, dict] = {}   # f"{symbol}|{horizon}" -> {"running": bool, "result": ...}
_last_price: dict[str, float] = {}      # symbol -> latest ticker price
_subs: dict[str, tuple] = {}            # socket sid -> (symbol, timeframe, horizon)
_candles: dict[tuple, dict] = {}        # (symbol, timeframe) -> {"df": ..., "fetched_at": ...}
_live_history: dict[tuple, deque] = {}  # (symbol, timeframe) -> recent recommendation changes
_LIVE_LIMIT = max(200, config.EMA_TREND + config.SR_LOOKBACK + 10)


def _room(sub: tuple) -> str:
    return "|".join(sub)


def _get_candles(symbol: str, timeframe: str, max_age: float, exchange=None):
    """Cached fetch_ohlcv so several viewers / recomputes don't each hit the exchange."""
    key = (symbol, timeframe)
    with _lock:
        cached = _candles.get(key)
    if cached and time.time() - cached["fetched_at"] < max_age:
        return cached["df"]
    df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=_LIVE_LIMIT, exchange=exchange)
    with _lock:
        _candles[key] = {"df": df, "fetched_at": time.time()}
    return df


def _compute_live(sub: tuple, max_age: float = config.LIVE_SIGNAL_SECONDS, exchange=None) -> dict:
    """Build the live trade recommendation for one (symbol, timeframe, horizon) subscription."""
    symbol, timeframe, horizon = sub
    try:
        df = _get_candles(symbol, timeframe, max_age, exchange)
        htf = HIGHER_TIMEFRAME.get(timeframe)
        htf_df = None
        if htf:
            try:
                htf_df = _get_candles(symbol, htf, 60, exchange)
            except Exception:
                traceback.print_exc()
        with _lock:
            fc = _forecast_cache.get(f"{symbol}|{horizon}", {}).get("result")
            bt = _backtest_cache.get(f"{symbol}|{timeframe}", {}).get("result")
            live_price = _last_price.get(symbol)
        rec = build_live_trade(df, timeframe, live_price=live_price, htf_df=htf_df, forecast=fc, backtest=bt)
    except Exception as exc:
        traceback.print_exc()
        return {"symbol": symbol, "timeframe": timeframe, "horizon": horizon,
                "verdict": "WAIT", "error": str(exc), "reasons": [f"Error: {exc}"], "history": []}

    # Log every change of call so the viewer sees what fired and how it did since.
    key = (symbol, timeframe)
    with _lock:
        hist = _live_history.setdefault(key, deque(maxlen=30))
        last = hist[-1] if hist else None
        if not last or last["verdict"] != rec["verdict"] or last["direction"] != rec["direction"]:
            hist.append({
                "ts": time.time(), "verdict": rec["verdict"], "direction": rec["direction"],
                "price": rec["entry_price"], "quality": rec["quality"],
                "stop_price": rec.get("stop_price"), "target_price": rec.get("target_price"),
            })
        history = list(hist)[::-1]
    return {"symbol": symbol, "horizon": horizon, "updated_at": time.time(), "history": history, **rec}


def _active_subs() -> set[tuple]:
    with _lock:
        return set(_subs.values())


def _live_signal_loop():
    """Recompute and push the live recommendation for every coin/timeframe someone is watching."""
    exchange = get_exchange()
    while True:
        for sub in _active_subs():
            rec = _compute_live(sub, exchange=exchange)
            socketio.emit("live", rec, to=_room(sub))
            _refresh_forecast_if_stale(sub, rec)
        time.sleep(config.LIVE_SIGNAL_SECONDS)


def _refresh_forecast_if_stale(sub: tuple, rec: dict):
    """A forecast is made from the candles up to its fit; once a new candle
    opens on that timeframe it's stale — re-fit it so Trade shows a current one."""
    symbol, timeframe, horizon = sub
    spec = config.FORECAST_HORIZONS.get(horizon)
    candle = rec.get("candle") or {}
    if not spec or spec["timeframe"] != timeframe or not candle.get("time"):
        return
    with _lock:
        cached = _forecast_cache.get(f"{symbol}|{horizon}")
    if not cached or cached.get("running"):
        return   # never run for this pair yet (the viewer triggers the first) or already fitting
    result = cached.get("result") or {}
    if result.get("last_time") and candle["time"] > result["last_time"]:
        _run_forecast_async(symbol, horizon)


def _market_depth_loop():
    """Order book + recent trades for every coin someone is viewing (Trade page), every ~2s."""
    exchange = get_exchange()
    while True:
        for symbol in {s[0] for s in _active_subs()}:
            try:
                ob = exchange.fetch_order_book(symbol, limit=20)
                tr = exchange.fetch_trades(symbol, limit=40)
                socketio.emit("depth", {
                    "symbol": symbol,
                    "bids": [[float(p), float(a)] for p, a, *_ in ob.get("bids", [])[:16]],
                    "asks": [[float(p), float(a)] for p, a, *_ in ob.get("asks", [])[:16]],
                    "trades": [{"ts": t["timestamp"] / 1000, "price": float(t["price"]), "qty": float(t["amount"]),
                                "side": t.get("side")} for t in reversed(tr)][:40],
                }, to="depth|" + symbol)
            except Exception:
                traceback.print_exc()
        time.sleep(2)


def _realtime_price_loop():
    """Push a live price tick per coin over the websocket every few seconds."""
    exchange = get_exchange()
    while True:
        for symbol in config.COINS:
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker.get("last") or ticker.get("close")
                if price is not None:
                    with _lock:
                        _last_price[symbol] = float(price)
                    socketio.emit("price", {
                        "symbol": symbol,
                        "price": float(price),
                        "ts": time.time(),
                        "change_pct": ticker.get("percentage"),
                        "high": ticker.get("high"),
                        "low": ticker.get("low"),
                        "quote_volume": ticker.get("quoteVolume"),
                    })
            except Exception:
                traceback.print_exc()
        time.sleep(config.REALTIME_POLL_SECONDS)


def _run_backtest_async(symbol: str, timeframe: str):
    key = f"{symbol}|{timeframe}"

    def work():
        try:
            df = fetch_ohlcv_history(symbol=symbol, timeframe=timeframe)
            result = run_backtest(df)
            with _lock:
                _backtest_cache[key] = {
                    "running": False,
                    "result": {
                        "timeframe": timeframe,
                        "n_trades": result.n_trades,
                        "win_rate": result.win_rate,
                        "avg_win_pct": result.avg_win_pct,
                        "avg_loss_pct": result.avg_loss_pct,
                        "expectancy_pct": result.expectancy_pct,
                        "profit_factor": result.profit_factor,
                        "total_return_pct": result.total_return_pct,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "directional_hit_rate": result.directional_hit_rate,
                        "directional_signals": result.directional_signals,
                    },
                }
        except Exception as exc:
            traceback.print_exc()
            with _lock:
                _backtest_cache[key] = {"running": False, "result": {"error": str(exc)}}
        with _lock:
            payload = _backtest_cache[key]["result"]
        socketio.emit("backtest", {"symbol": symbol, "timeframe": timeframe, "result": payload})
        _emit_entry(symbol)

    with _lock:
        if _backtest_cache.get(key, {}).get("running"):
            return
        _backtest_cache[key] = {"running": True, "result": _backtest_cache.get(key, {}).get("result")}
    threading.Thread(target=work, daemon=True).start()


def _emit_entry(symbol: str):
    """Inputs changed (backtest/forecast finished) — re-push the live call to viewers of `symbol`."""
    for sub in _active_subs():
        if sub[0] == symbol:
            socketio.emit("live", _compute_live(sub, max_age=float("inf")), to=_room(sub))


def _run_forecast_async(symbol: str, horizon: str):
    key = f"{symbol}|{horizon}"

    def work():
        try:
            result = run_forecast(symbol, horizon)
            with _lock:
                _forecast_cache[key] = {"running": False, "result": result}
        except Exception as exc:
            traceback.print_exc()
            with _lock:
                _forecast_cache[key] = {"running": False, "result": {"error": str(exc)}}
        with _lock:
            payload = _forecast_cache[key]["result"]
        socketio.emit("forecast", {"symbol": symbol, "horizon": horizon, "result": payload})
        _emit_entry(symbol)

    with _lock:
        if _forecast_cache.get(key, {}).get("running"):
            return
        _forecast_cache[key] = {"running": True, "result": _forecast_cache.get(key, {}).get("result")}
    threading.Thread(target=work, daemon=True).start()


_best_time_cache: dict[str, dict] = {}   # symbol -> {"running": bool, "result": ...}


def _run_best_time_async(symbol: str):
    """
    Scan every configured forecast horizon for `symbol` and rank them by
    signal-to-noise: |projected move| divided by the forecast's own
    uncertainty band. This is still just Prophet's trend extrapolation
    repeated at different candle sizes — a high ratio means "this horizon's
    trend is large relative to how wide its own uncertainty is," not
    "this will happen." Ties/no-signal horizons rank low on purpose.
    """
    def work():
        try:
            ranked = []
            for horizon in config.FORECAST_HORIZONS:
                try:
                    fc = run_forecast(symbol, horizon)
                except Exception as exc:
                    ranked.append({"horizon": horizon, "error": str(exc)})
                    continue
                band = fc["fit_details"]["uncertainty_band_pct_of_price"] / 2.0
                score = abs(fc["projected_change_pct"]) / max(band, 0.01)
                ranked.append({
                    "horizon": horizon,
                    "timeframe": fc["timeframe"],
                    "projected_change_pct": fc["projected_change_pct"],
                    "uncertainty_band_pct": band,
                    "score": score,
                })
                with _lock:
                    _forecast_cache[f"{symbol}|{horizon}"] = {"running": False, "result": fc}

            valid = [r for r in ranked if "error" not in r]
            valid.sort(key=lambda r: r["score"], reverse=True)
            best = valid[0] if valid else None

            with _lock:
                _best_time_cache[symbol] = {
                    "running": False,
                    "result": {"ranked": valid, "best": best, "errors": [r for r in ranked if "error" in r]},
                }
        except Exception as exc:
            traceback.print_exc()
            with _lock:
                _best_time_cache[symbol] = {"running": False, "result": {"error": str(exc)}}
        with _lock:
            payload = _best_time_cache[symbol]["result"]
        socketio.emit("best_time", {"symbol": symbol, "result": payload})
        _emit_entry(symbol)

    with _lock:
        if _best_time_cache.get(symbol, {}).get("running"):
            return
        _best_time_cache[symbol] = {"running": True, "result": _best_time_cache.get(symbol, {}).get("result")}
    threading.Thread(target=work, daemon=True).start()




# --- React frontend (frontend/dist, built with `npm run build`) ---------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def frontend(path):
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    if not os.path.exists(os.path.join(_DIST, "index.html")):
        return ("Frontend not built. Run:  cd frontend && npm install && npm run build\n"
                "(or `npm run dev` and open http://localhost:5173 while developing)"), 503, {"Content-Type": "text/plain"}
    full = os.path.join(_DIST, path)
    if path and os.path.isfile(full):
        return send_from_directory(_DIST, path)
    return send_from_directory(_DIST, "index.html")   # client-side routes


@app.route("/api/config")
def api_config():
    import notifier
    return jsonify({
        "exchange": config.EXCHANGE_ID,
        "coins": config.COINS,
        "time_options": config.TIME_OPTIONS,
        "default_time_key": next((o["key"] for o in config.TIME_OPTIONS if o["timeframe"] == config.CHART_TIMEFRAME),
                                 config.TIME_OPTIONS[0]["key"]),
        "backtest_candles": config.BACKTEST_CANDLES,
        "trading": {
            "mode": _trader.mode if _trader else config.TRADING_MODE,
            "auto_entries": _trader.auto_entries if _trader else False,
            "trader_running": _trader is not None,
            "trader_error": _trader_error,
            "email_configured": notifier.email_configured(),
            "email_to": config.EMAIL_TO,
            "database": config.DB_LABEL,
            "limits": {
                "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
                "max_position_usd": config.MAX_POSITION_USD,
                "min_order_usd": config.MIN_ORDER_USD,
                "max_open_trades": config.MAX_OPEN_TRADES,
                "daily_loss_limit_pct": config.DAILY_LOSS_LIMIT_PCT,
                "symbol_cooldown_minutes": config.SYMBOL_COOLDOWN_MINUTES,
                "max_hold_bars": config.MAX_HOLD_BARS,
                "tp1_close_fraction": config.TP1_CLOSE_FRACTION,
                "auto_min_quality": config.AUTO_MIN_QUALITY,
                "auto_require_confirmed": config.AUTO_REQUIRE_CONFIRMED,
                "auto_require_backtest": config.AUTO_REQUIRE_BACKTEST,
                "auto_timeframes": config.AUTO_TIMEFRAMES,
                "atr_stop_mult": config.ATR_STOP_MULT,
                "atr_target_mult": config.ATR_TARGET_MULT,
                "paper_start_balance": config.PAPER_START_BALANCE,
            },
        },
    })


@app.route("/api/trades")
def api_trades():
    import db
    db.init_db()
    mode = request.args.get("mode") or None
    return jsonify({"trades": db.all_trades(mode, int(request.args.get("limit", 1000))),
                    "stats": db.stats(mode or config.TRADING_MODE)})


@app.route("/api/trades.csv")
def api_trades_csv():
    import csv
    import io
    import db
    db.init_db()
    rows = db.all_trades(request.args.get("mode") or None, 100000)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue(), 200, {"Content-Type": "text/csv",
                                 "Content-Disposition": "attachment; filename=trades.csv"}


@app.route("/api/history")
def api_history():
    symbol = request.args.get("symbol", config.SYMBOL)
    timeframe = request.args.get("timeframe", config.CHART_TIMEFRAME)
    limit = int(request.args.get("limit", config.CHART_HISTORY_LIMIT))
    try:
        df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        candles = [
            {
                "time": int(ts.timestamp()),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
            for ts, row in df.iterrows()
        ]
        volume = [
            {
                "time": int(ts.timestamp()),
                "value": float(row.volume),
                "color": "rgba(61,220,132,0.5)" if row.close >= row.open else "rgba(255,92,92,0.5)",
            }
            for ts, row in df.iterrows()
        ]
        return jsonify({"candles": candles, "volume": volume})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/entry")
def api_entry():
    sub = (
        request.args.get("symbol", config.SYMBOL),
        request.args.get("timeframe", config.CHART_TIMEFRAME),
        request.args.get("horizon", "15m"),
    )
    return jsonify(_compute_live(sub))


@app.route("/api/indicators")
def api_indicators():
    symbol = request.args.get("symbol", config.SYMBOL)
    timeframe = request.args.get("timeframe", config.CHART_TIMEFRAME)
    limit = int(request.args.get("limit", config.CHART_HISTORY_LIMIT))
    try:
        df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        enriched = add_all_indicators(df)
        rsi = [
            {"time": int(ts.timestamp()), "value": float(v)}
            for ts, v in enriched["rsi"].dropna().items()
        ]
        macd_hist = [
            {
                "time": int(ts.timestamp()),
                "value": float(v),
                "color": "rgba(61,220,132,0.7)" if v >= 0 else "rgba(255,92,92,0.7)",
            }
            for ts, v in enriched["macd_hist"].dropna().items()
        ]
        return jsonify({"rsi": rsi, "macd_hist": macd_hist})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/backtest", methods=["GET", "POST"])
def api_backtest():
    symbol = request.args.get("symbol", config.SYMBOL)
    timeframe = request.args.get("timeframe", config.TIMEFRAME)
    if request.method == "POST":
        _run_backtest_async(symbol, timeframe)
    with _lock:
        return jsonify(_backtest_cache.get(f"{symbol}|{timeframe}", {"running": False, "result": None}))


@app.route("/api/forecast", methods=["GET", "POST"])
def api_forecast():
    symbol = request.args.get("symbol", config.SYMBOL)
    horizon = request.args.get("horizon", "month")
    key = f"{symbol}|{horizon}"
    if request.method == "POST":
        _run_forecast_async(symbol, horizon)
    with _lock:
        return jsonify(_forecast_cache.get(key, {"running": False, "result": None}))


@app.route("/api/best_time", methods=["GET", "POST"])
def api_best_time():
    symbol = request.args.get("symbol", config.SYMBOL)
    if request.method == "POST":
        _run_best_time_async(symbol)
    with _lock:
        return jsonify(_best_time_cache.get(symbol, {"running": False, "result": None}))


# --- Socket.IO events ---------------------------------------------------------
# The browser talks to the server over the websocket only: it subscribes to a
# coin + timeframe to join that room and get the current live recommendation,
# asks for jobs to run, and every update after that is pushed — no polling.

@socketio.on("subscribe")
def ws_subscribe(data):
    data = data or {}
    sub = (
        data.get("symbol", config.SYMBOL),
        data.get("timeframe", config.CHART_TIMEFRAME),
        data.get("horizon", "15m"),
    )
    with _lock:
        old = _subs.get(request.sid)
        _subs[request.sid] = sub
    if old and old != sub:
        leave_room(_room(old))
        leave_room("depth|" + old[0])
    join_room(_room(sub))
    join_room("depth|" + sub[0])
    emit("live", _compute_live(sub))


@socketio.on("disconnect")
def ws_disconnect(*_args):
    with _lock:
        _subs.pop(request.sid, None)


def _trader_status() -> dict:
    if _trader is None:
        import db
        db.init_db()
        return {"enabled": False, "mode": config.TRADING_MODE, "error": _trader_error,
                "closed": db.closed_trades(limit=30), "stats": db.stats(config.TRADING_MODE)}
    return {"enabled": True, **_trader.status()}


def _push_trader():
    socketio.emit("trader", _trader_status(), to="trader")
    if _trader is not None:
        socketio.emit("account", _trader.account_status(), to="account")


@socketio.on("trader_subscribe")
def ws_trader_subscribe(*_args):
    join_room("trader")
    emit("trader", _trader_status())


def _trader_action(fn):
    if _trader is None:
        emit("trader_error", {"error": f"Trader is not running: {_trader_error or 'unknown error'}"})
        return
    threading.Thread(target=fn, daemon=True).start()


@socketio.on("trader_pause")
def ws_trader_pause(*_args):
    _trader_action(lambda: _trader.pause("dashboard"))


@socketio.on("trader_resume")
def ws_trader_resume(*_args):
    _trader_action(_trader.resume)


@socketio.on("trader_close")
def ws_trader_close(data):
    trade_id = int((data or {}).get("id", 0))
    _trader_action(lambda: _trader.close_trade_manual(trade_id))


@socketio.on("account_subscribe")
def ws_account_subscribe(*_args):
    join_room("account")
    if _trader is None:
        emit("trader_error", {"error": f"Trader is not running: {_trader_error}"})
        return
    emit("account", _trader.account_status())


@socketio.on("account_funds")
def ws_account_funds(data):
    """Demo account: {action: deposit|withdraw|reset, amount}."""
    data = data or {}
    action = data.get("action")
    sid = request.sid
    if action not in ("deposit", "withdraw", "reset"):
        emit("trader_result", {"ok": False, "message": f"Unknown action '{action}'"})
        return
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        emit("trader_result", {"ok": False, "message": "Amount must be a number"})
        return

    def work():
        err = _trader.demo_funds(action, amount) if _trader else f"Trader is not running: {_trader_error}"
        socketio.emit("trader_result", {"ok": err is None,
                                        "message": err or f"Demo {action} of ${amount:,.2f} done"}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("fbot_settings")
def ws_fbot_settings(data):
    sid = request.sid

    def work():
        err = _trader.set_fbot_settings(data or {}) if _trader else f"Trader is not running: {_trader_error}"
        socketio.emit("trader_result", {"ok": err is None, "message": err or "Forecast bot settings saved"}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("order_place")
def ws_order_place(data):
    """Order form: {symbol, side BUY|SELL, type MARKET|LIMIT, price?, qty?, quote?, tp?, sl?} on the active account."""
    data = data or {}
    sid = request.sid
    if _trader is None:
        emit("trader_result", {"ok": False, "message": f"Trader is not running: {_trader_error}"})
        return

    def num(k):
        try:
            v = float(data.get(k)) if data.get(k) not in (None, "") else None
            return v if v and v > 0 else None
        except (TypeError, ValueError):
            return None

    def work():
        ok, msg = _trader.place_order(data.get("symbol"), data.get("side"), data.get("type"), price=num("price"),
                                      qty=num("qty"), quote=num("quote"), tp=num("tp"), sl=num("sl"))
        socketio.emit("trader_result", {"ok": ok, "message": msg}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("order_cancel")
def ws_order_cancel(data):
    sid = request.sid
    if _trader is None:
        return

    def work():
        ok, msg = _trader.cancel_order(int((data or {}).get("id", 0)))
        socketio.emit("trader_result", {"ok": ok, "message": msg}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("fbot_run_now")
def ws_fbot_run_now(data):
    """Run forecast now — optionally saving the (edited) bot settings first, in order."""
    sid = request.sid

    def work():
        if _trader is None:
            err = f"Trader is not running: {_trader_error}"
        else:
            new = (data or {}).get("settings")
            err = _trader.set_fbot_settings(new) if new else None
            err = err or _trader.fbot_run_now()
        socketio.emit("trader_result", {"ok": err is None,
                                        "message": err or "Forecast run started — decisions appear as each coin finishes"}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("trader_open_manual")
def ws_trader_open_manual(data):
    """Open a long now on the viewer's selected coin+timeframe, using the live recommendation's levels."""
    data = data or {}
    symbol = data.get("symbol", config.SYMBOL)
    timeframe = data.get("timeframe", config.CHART_TIMEFRAME)
    horizon = data.get("horizon", "15m")
    usd = float(data["usd"]) if data.get("usd") else None
    sid = request.sid
    if _trader is None:
        emit("trader_result", {"ok": False, "message": f"Trader is not running: {_trader_error}"})
        return

    def work():
        rec = _compute_live((symbol, timeframe, horizon), max_age=0)
        if rec.get("error"):
            err = rec["error"]
        else:
            err = _trader.open_manual(symbol, timeframe, rec, usd)
        socketio.emit("trader_result", {"ok": err is None,
                                        "message": err or f"Opened LONG {symbol} {timeframe}"}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("trader_set_mode")
def ws_trader_set_mode(data):
    """Trade page switch: 'demo' -> simulated paper account, 'real' -> Binance with real money."""
    choice = (data or {}).get("mode")
    mode = {"demo": "paper", "real": "live"}.get(choice)
    sid = request.sid
    if mode is None:
        emit("trader_result", {"ok": False, "message": f"Unknown mode '{choice}'"})
        return
    if _trader is None:
        emit("trader_result", {"ok": False, "message": f"Trader is not running: {_trader_error}"})
        return

    def work():
        err = _trader.set_mode(mode)
        label = "Demo" if mode == "paper" else "Real (Binance)"
        socketio.emit("trader_result", {"ok": err is None,
                                        "message": f"Refused — {err}" if err else f"Now trading in {label} mode"}, to=sid)
        _push_trader()
    threading.Thread(target=work, daemon=True).start()


@socketio.on("settings_get")
def ws_settings_get(*_args):
    import settings
    emit("settings", {"values": settings.current(), "schema": settings.schema(),
                      "auto_trade": settings.auto_trade_enabled()})


@socketio.on("settings_update")
def ws_settings_update(data):
    """{values: {...}} to save trading limits, or {reset: true} for config.py defaults."""
    import settings
    data = data or {}
    if data.get("reset"):
        settings.reset_defaults()
        applied, errors = {"reset": True}, []
    else:
        applied, errors = settings.update(data.get("values") or {})
    if not errors:
        import db
        db.log_event("WARN", f"Settings changed from dashboard: {applied}", mode=_trader.mode if _trader else None)
    emit("trader_result", {"ok": not errors, "message": "; ".join(errors) if errors else "Settings saved — active now"})
    socketio.emit("settings", {"values": settings.current(), "schema": settings.schema(),
                               "auto_trade": settings.auto_trade_enabled()})
    if _trader is not None:
        _push_trader()


@socketio.on("trader_set_auto")
def ws_trader_set_auto(data):
    import settings
    on = bool((data or {}).get("enabled"))
    if _trader is None:
        settings.set_auto_trade(on)
    else:
        _trader.set_auto_trade(on)
    emit("trader_result", {"ok": True, "message": f"Auto trade {'ON' if on else 'OFF'}"})
    socketio.emit("settings", {"values": settings.current(), "schema": settings.schema(), "auto_trade": on})


@socketio.on("trader_test_email")
def ws_trader_test_email(*_args):
    import notifier
    sid = request.sid

    def work():
        mode = _trader.mode if _trader else config.TRADING_MODE
        err = notifier.send_email(f"[{config.MODE_NAMES[mode]}] Test email from the trading bot",
                                  "If you can read this, trade open/close emails will reach you.", wait=True)
        socketio.emit("trader_result", {"ok": err is None, "message": err or f"Test email sent to {config.EMAIL_TO}"}, to=sid)
    threading.Thread(target=work, daemon=True).start()


@socketio.on("credentials_get")
def ws_credentials_get(*_args):
    import credentials
    emit("credentials", credentials.public_view())


@socketio.on("credentials_update")
def ws_credentials_update(data):
    """{values: {NAME: value}, clear?: [NAME]} — blank secrets keep the stored value."""
    import credentials
    import db
    data = data or {}
    sid = request.sid

    def work():
        changed, errors = credentials.update({**(data.get("values") or {}), "clear": data.get("clear") or []})
        msg = "; ".join(errors) if errors else ("Credentials saved — active now" if changed else "No changes")
        ok = not errors
        if changed:
            # Names only — values never go into the event log.
            db.log_event("WARN", f"Credentials changed from dashboard: {', '.join(changed)}",
                         mode=_trader.mode if _trader else None)
            if _trader is not None and any(n in credentials.BINANCE_FIELDS for n in changed):
                err = _trader.reload_binance()
                if err:
                    ok, msg = False, f"Saved, but Binance rejected the new credentials: {err}"
        socketio.emit("trader_result", {"ok": ok, "message": msg}, to=sid)
        socketio.emit("credentials", credentials.public_view())
        if _trader is not None:
            _push_trader()
    threading.Thread(target=work, daemon=True).start()


@socketio.on("trader_close_all")
def ws_trader_close_all(*_args):
    _trader_action(lambda: _trader.close_all("closed from dashboard"))


@socketio.on("run_backtest")
def ws_run_backtest(data):
    data = data or {}
    _run_backtest_async(data.get("symbol", config.SYMBOL), data.get("timeframe", config.TIMEFRAME))


@socketio.on("run_forecast")
def ws_run_forecast(data):
    data = data or {}
    _run_forecast_async(data.get("symbol", config.SYMBOL), data.get("horizon", "month"))


@socketio.on("run_best_time")
def ws_run_best_time(data):
    _run_best_time_async((data or {}).get("symbol", config.SYMBOL))


if __name__ == "__main__":
    import credentials
    credentials.apply_saved()   # MySQL: create tables, load Binance URL/keys, SMTP, Telegram
    threading.Thread(target=_live_signal_loop, daemon=True).start()
    threading.Thread(target=_realtime_price_loop, daemon=True).start()
    threading.Thread(target=_market_depth_loop, daemon=True).start()
    # The trader always runs so manual trades get their stops/targets managed;
    # it only opens trades by itself when Auto trade is switched on in Settings (MySQL).
    try:
        from auto_trader import AutoTrader
        _trader = AutoTrader(on_update=_push_trader)
        threading.Thread(target=_trader.run_forever, daemon=True).start()
    except Exception as exc:
        traceback.print_exc()
        _trader_error = str(exc)
    socketio.run(app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=False, allow_unsafe_werkzeug=True)
