"""
Binance-style spot order desk for the Trade page: MARKET and LIMIT orders,
BUY and SELL, on the demo account or on Binance (whichever account is active).

Model:
  - One open *holding* (a row in `trades`, strategy "spot") per coin per account.
    A BUY fill opens it or adds to it (entry = average cost); a SELL sells part
    or all of it. Optional TP / SL on a buy become the holding's target / stop
    and are enforced like every other trade (exchange stop order on Binance).
  - Demo LIMIT orders fill when the live price crosses the limit (checked every
    manage cycle). Binance LIMIT orders rest on the exchange and are polled.
  - Funds reserved by open orders aren't available for new ones, like Binance.
  - Every execution is written to `fills` (trade history) and `orders`.

Mixed into AutoTrader so it shares the brokers, lock, stop management and
sell/close accounting.
"""

from __future__ import annotations
import time

import config
import db
import notifier
from broker import BrokerError


class OrderDesk:

    # --- balances ----------------------------------------------------------------

    def _reserved(self, mode: str) -> tuple[float, dict]:
        """USDT locked by open BUY limits, and base qty locked by open SELL orders."""
        quote, base = 0.0, {}
        for o in db.open_orders(mode):
            if o["side"] == "BUY":
                quote += (o["qty"] or 0) * (o["price"] or 0) * (1 + config.TAKER_FEE)
            else:
                base[o["symbol"]] = base.get(o["symbol"], 0.0) + (o["qty"] or 0)
        return quote, base

    def balances(self, mode: str | None = None) -> dict:
        mode = mode or self.mode
        b = self._broker_for(mode)
        cash = b.quote_balance()
        res_quote, res_base = self._reserved(mode)
        holdings = []
        for t in db.open_trades(mode):
            try:
                px = b.price(t["symbol"])
            except Exception:
                px = t["entry_price"]
            locked = res_base.get(t["symbol"], 0.0)
            holdings.append({
                "trade_id": t["id"], "symbol": t["symbol"], "asset": t["symbol"].split("/")[0],
                "qty": t["qty_open"], "available": max(0.0, t["qty_open"] - locked), "locked": locked,
                "avg_price": t["entry_price"], "price": px, "value": t["qty_open"] * px,
                "unrealized_usd": t["realized_usd"] + t["qty_open"] * px * (1 - config.TAKER_FEE) - t["cost_usd"],
                "stop_price": t["stop_price"] or None, "target_price": t["target_price"],
                "strategy": t.get("strategy") or "rules", "timeframe": t["timeframe"],
            })
        return {
            "mode": mode, "quote_asset": config.QUOTE_ASSET,
            "cash": cash, "cash_available": max(0.0, cash - res_quote), "cash_locked": res_quote,
            "holdings": holdings, "equity": cash + sum(h["value"] for h in holdings),
        }

    # --- place / cancel ------------------------------------------------------------

    def place_order(self, symbol: str, side: str, type_: str, price: float | None = None,
                    qty: float | None = None, quote: float | None = None,
                    tp: float | None = None, sl: float | None = None, mode: str | None = None) -> tuple[bool, str]:
        mode = mode or self.mode
        side, type_ = (side or "").upper(), (type_ or "").upper()
        if symbol not in config.COINS:
            return False, f"Unknown pair {symbol}"
        if side not in ("BUY", "SELL") or type_ not in ("MARKET", "LIMIT"):
            return False, "Order must be BUY/SELL and MARKET/LIMIT"
        b = self._broker_for(mode)
        with self._lock:
            try:
                last = b.price(symbol)
                if type_ == "LIMIT":
                    if not price or price <= 0:
                        return False, "Limit price required"
                    if quote and not qty:
                        qty = quote / price
                ref_px = price if type_ == "LIMIT" else last
                if not qty and not quote:
                    return False, "Enter an amount"
                notional = quote if (side == "BUY" and type_ == "MARKET" and quote) else (qty or 0) * ref_px
                if notional < b.min_order_usd(symbol):
                    return False, f"Order value {notional:.2f} below minimum {b.min_order_usd(symbol):.2f} {config.QUOTE_ASSET}"
                bal = self.balances(mode)

                if side == "BUY":
                    need = notional * (1 + config.TAKER_FEE)
                    if need > bal["cash_available"] + 1e-9:
                        return False, f"Insufficient {config.QUOTE_ASSET}: need {need:.2f}, available {bal['cash_available']:.2f}"
                    if sl and sl >= ref_px:
                        return False, "Stop-loss must be below the buy price"
                    if tp and tp <= ref_px:
                        return False, "Take-profit must be above the buy price"
                else:
                    held = next((h for h in bal["holdings"] if h["symbol"] == symbol), None)
                    avail = held["available"] if held else 0.0
                    if not qty:
                        qty = quote / ref_px
                    if qty > avail * 1.000001:
                        return False, f"Insufficient {symbol.split('/')[0]}: selling {qty:.8f}, available {avail:.8f}"
                    qty = min(qty, avail)

                order_id = db.insert_order(mode=mode, symbol=symbol, side=side, type=type_, price=price,
                                           qty=qty, quote=quote if not qty else None, tp_price=tp, sl_price=sl, status="NEW")
                if type_ == "MARKET":
                    return self._execute_market(db.get_order(order_id), b)
                # LIMIT: demo rests in our DB; Binance rests on the exchange.
                if b.has_exchange_stops:
                    ex_id = b.limit_order(symbol, side, qty, price)
                    db.update_order(order_id, exchange_order_id=ex_id)
                db.log_event("TRADE", f"Order #{order_id} placed: LIMIT {side} {qty:.8f} {symbol} @ {price:.4f}", mode=mode)
                return True, f"Limit {side.lower()} #{order_id} placed @ {price:.4f}"
            except BrokerError as exc:
                return False, str(exc)
            except Exception as exc:
                self._error(f"order {side} {symbol}", exc)
                return False, f"Order failed: {exc}"
            finally:
                self._notify_update()

    def cancel_order(self, order_id: int) -> tuple[bool, str]:
        with self._lock:
            o = db.get_order(order_id)
            if not o or o["status"] != "NEW":
                return False, "Order is not open"
            b = self._broker_for(o["mode"])
            if o.get("exchange_order_id"):
                b.cancel_order(o["symbol"], o["exchange_order_id"])
                status, fill = b.order_update(o["symbol"], o["exchange_order_id"])
                if fill:   # partly filled before the cancel landed
                    self._apply_fill(o, fill, b)
            if db.get_order(order_id)["status"] == "NEW":
                db.update_order(order_id, status="CANCELED")
            db.log_event("TRADE", f"Order #{order_id} canceled", mode=o["mode"])
        self._notify_update()
        return True, f"Order #{order_id} canceled"

    # --- execution -----------------------------------------------------------------

    def _execute_market(self, o: dict, b) -> tuple[bool, str]:
        symbol = o["symbol"]
        if o["side"] == "BUY":
            usd = o["quote"] or (o["qty"] * b.price(symbol))
            fill = b.market_buy(symbol, usd)
            self._apply_fill(o, fill, b)
            return True, f"Bought {fill.qty:.8f} {symbol.split('/')[0]} @ {fill.price:.4f}"
        t = next((t for t in db.open_trades(o["mode"]) if t["symbol"] == symbol), None)
        if not t:
            db.update_order(o["id"], status="REJECTED", note="nothing to sell")
            return False, "Nothing to sell"
        final = o["qty"] >= t["qty_open"] * 0.999
        fill = self._sell(t, min(o["qty"], t["qty_open"]), f"order #{o['id']} (market sell)", final=final, order_id=o["id"])
        if fill:
            db.update_order(o["id"], status="FILLED", filled_qty=fill.qty, avg_price=fill.price, fee_usd=fill.fee_usd, trade_id=t["id"])
            return True, f"Sold {fill.qty:.8f} {symbol.split('/')[0]} @ {fill.price:.4f}"
        db.update_order(o["id"], status="FILLED", trade_id=t["id"], note="closed without an exchange order")
        return True, "Position closed"

    def _apply_fill(self, o: dict, fill, b):
        """Book a BUY or SELL fill of order `o` against the coin's holding."""
        mode, symbol = o["mode"], o["symbol"]
        if o["side"] == "SELL":
            t = next((t for t in db.open_trades(mode) if t["symbol"] == symbol), None)
            if t:
                final = fill.qty >= t["qty_open"] * 0.999
                if b.has_exchange_stops and t.get("stop_order_id"):
                    b.cancel_stop(symbol, t["stop_order_id"])
                    db.update_trade(t["id"], stop_order_id=None)
                    t["stop_order_id"] = None
                self._record_sell(t, fill, f"order #{o['id']} ({o['type'].lower()} sell)", final=final, order_id=o["id"])
                if not final and b.has_exchange_stops and t["stop_price"]:
                    t2 = db.get_trade(t["id"])
                    db.update_trade(t["id"], stop_order_id=b.place_stop(symbol, t2["qty_open"], t2["stop_price"]))
            db.update_order(o["id"], status="FILLED", filled_qty=fill.qty, avg_price=fill.price,
                            fee_usd=fill.fee_usd, trade_id=t["id"] if t else None)
            return

        t = next((t for t in db.open_trades(mode) if t["symbol"] == symbol), None)
        tp, sl = o.get("tp_price"), o.get("sl_price")
        if t is None:
            trade_id = db.insert_trade(
                mode=mode, symbol=symbol, timeframe="spot", side="LONG", status="OPEN", strategy="spot",
                quality=None, reasons=[f"Order #{o['id']}: {o['type'].lower()} buy from the Trade page"],
                qty=fill.qty, qty_open=fill.qty, entry_price=fill.price, cost_usd=fill.quote,
                stop_price=sl or 0.0, initial_stop=sl or 0.0, tp1_price=None, target_price=tp,
                entry_time=time.time(), entry_order_id=fill.order_id, fees_usd=fill.fee_usd,
            )
            new = True
        else:
            trade_id, new = t["id"], False
            qty_open = t["qty_open"] + fill.qty
            db.update_trade(
                trade_id, qty=t["qty"] + fill.qty, qty_open=qty_open, cost_usd=t["cost_usd"] + fill.quote,
                entry_price=(t["entry_price"] * t["qty_open"] + fill.price * fill.qty) / qty_open,
                fees_usd=t["fees_usd"] + fill.fee_usd,
                stop_price=sl if sl else t["stop_price"], target_price=tp if tp else t["target_price"],
            )
        db.add_fill(mode, symbol, "BUY", fill.qty, fill.price, fill.quote, fill.fee_usd,
                    f"order #{o['id']} ({o['type'].lower()} buy)", trade_id=trade_id, order_id=o["id"])
        db.update_order(o["id"], status="FILLED", filled_qty=fill.qty, avg_price=fill.price,
                        fee_usd=fill.fee_usd, trade_id=trade_id)
        t = db.get_trade(trade_id)
        # Exchange-side stop for the whole holding (re-placed after adding to it).
        if b.has_exchange_stops and t["stop_price"]:
            if t.get("stop_order_id"):
                b.cancel_stop(symbol, t["stop_order_id"])
            try:
                db.update_trade(trade_id, stop_order_id=b.place_stop(symbol, t["qty_open"], t["stop_price"]))
            except Exception as exc:
                self._error(f"placing stop for {symbol} failed", exc, trade_id)
        db.log_event("TRADE", f"{'OPEN' if new else 'ADD'} {symbol} via order #{o['id']}: {fill.qty:.8f} @ {fill.price:.4f} "
                              f"(cost {fill.quote:.2f})" + (f" SL {sl:.4f}" if sl else "") + (f" TP {tp:.4f}" if tp else ""), trade_id)
        if new:
            t["reasons_list"] = [f"Order #{o['id']}: {o['type'].lower()} buy"]
            try:
                notifier.trade_opened(t)      # a notification problem must never fail a filled order
            except Exception as exc:
                self._error("open email", exc, trade_id)

    def process_orders(self):
        """Called every manage cycle: fill demo limits whose price crossed; poll Binance limits."""
        for o in db.open_orders():
            try:
                b = self._broker_for(o["mode"])
                if o.get("exchange_order_id"):
                    status, fill = b.order_update(o["symbol"], o["exchange_order_id"])
                    if fill:
                        self._apply_fill(o, fill, b)
                    if status == "canceled" and db.get_order(o["id"])["status"] == "NEW":
                        db.update_order(o["id"], status="CANCELED", note="canceled on exchange")
                    continue
                if o["mode"] != "paper" or o["type"] != "LIMIT":
                    continue
                px = b.price(o["symbol"])
                if (o["side"] == "BUY" and px <= o["price"]) or (o["side"] == "SELL" and px >= o["price"]):
                    if o["side"] == "SELL":
                        t = next((t for t in db.open_trades("paper") if t["symbol"] == o["symbol"]), None)
                        if not t:
                            db.update_order(o["id"], status="CANCELED", note="holding no longer open")
                            continue
                        qty = min(o["qty"], t["qty_open"])
                    else:
                        qty = o["qty"]
                    fill = b.fill_at(o["symbol"], o["side"], qty, o["price"])
                    self._apply_fill(o, fill, b)
                    db.log_event("TRADE", f"Order #{o['id']} filled: LIMIT {o['side']} {qty:.8f} {o['symbol']} @ {o['price']:.4f}",
                                 mode="paper")
            except BrokerError as exc:
                db.update_order(o["id"], status="REJECTED", note=str(exc))
                db.log_event("WARN", f"Order #{o['id']} rejected: {exc}", mode=o["mode"])
            except Exception as exc:
                self._error(f"order #{o['id']} check", exc)

    def desk_status(self, mode: str | None = None) -> dict:
        mode = mode or self.mode
        return {
            "balances": self.balances(mode),
            "open_orders": db.open_orders(mode),
            "order_history": db.order_history(mode, 150),
            "fills": db.fills(mode, 150),
        }
