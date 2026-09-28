"""
Order execution for the auto trader, behind one small interface:

  PaperBroker   — DEMO mode: simulated fills at the live Binance price, with
                  config.TAKER_FEE and config.SLIPPAGE, USDT balance kept in
                  MySQL. No API key, no real orders.
  BinanceBroker — REAL mode: real spot orders through ccxt at
                  config.BINANCE_API_URL. Places an exchange-side
                  STOP_LOSS_LIMIT after every buy so a position stays
                  protected even if this bot crashes.

API keys come only from the MySQL `credentials` table (Settings page) — never
hardcode them. Give the key trading permission only (NO withdrawals) and
restrict it to your IP.
"""

from __future__ import annotations
from dataclasses import dataclass

import ccxt

import config
import db
from data_fetcher import apply_binance_url, get_exchange


@dataclass
class Fill:
    qty: float          # base qty actually received (buy, net of base-asset fee) or sold
    price: float        # average fill price
    quote: float        # buy: quote spent incl. fee / sell: quote received net of fee
    fee_usd: float
    order_id: str | None = None


class BrokerError(RuntimeError):
    pass


class PaperBroker:
    mode = "paper"
    has_exchange_stops = False

    def __init__(self):
        self.ex = get_exchange()   # public market data only
        db.init_db()
        if db.get_state("paper_cash") is None:
            db.set_state("paper_cash", config.PAPER_START_BALANCE)
            db.add_ledger("paper", "DEPOSIT", config.PAPER_START_BALANCE, config.PAPER_START_BALANCE,
                          "Initial demo balance")

    # --- demo funds (the user's own "investment") ------------------------------

    def _set_cash(self, cash: float, type_: str, amount: float, note: str):
        db.set_state("paper_cash", cash)
        db.add_ledger("paper", type_, amount, cash, note)

    def deposit(self, amount: float):
        if amount <= 0:
            raise BrokerError("Deposit must be positive")
        self._set_cash(self.quote_balance() + amount, "DEPOSIT", amount, "Deposit")

    def withdraw(self, amount: float):
        cash = self.quote_balance()
        if amount <= 0 or amount > cash:
            raise BrokerError(f"Withdraw must be between 0 and available cash {cash:.2f}")
        self._set_cash(cash - amount, "WITHDRAW", -amount, "Withdraw")

    def reset(self, amount: float):
        """Start the demo account over with `amount` (only when nothing is open)."""
        if amount < 0:
            raise BrokerError("Reset amount can't be negative")
        if db.open_trades("paper"):
            raise BrokerError("Close all open demo trades before resetting the account")
        self._set_cash(amount, "RESET", amount, f"Account reset to {amount:.2f}")

    def price(self, symbol: str) -> float:
        t = self.ex.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close"))

    def quote_balance(self) -> float:
        return float(db.get_state("paper_cash", 0.0))

    def min_order_usd(self, symbol: str) -> float:
        return config.MIN_ORDER_USD

    def market_buy(self, symbol: str, usd: float) -> Fill:
        cash = self.quote_balance()
        if usd > cash:
            raise BrokerError(f"Paper balance {cash:.2f} < order {usd:.2f}")
        px = self.price(symbol) * (1 + config.SLIPPAGE)
        fee = usd * config.TAKER_FEE
        qty = (usd - fee) / px
        db.set_state("paper_cash", cash - usd)
        db.add_ledger("paper", "BUY", -usd, cash - usd, f"Buy {qty:.8f} {symbol} @ {px:.4f} (fee {fee:.4f})")
        return Fill(qty=qty, price=px, quote=usd, fee_usd=fee, order_id="paper")

    def market_sell(self, symbol: str, qty: float) -> Fill:
        px = self.price(symbol) * (1 - config.SLIPPAGE)
        gross = qty * px
        fee = gross * config.TAKER_FEE
        cash = self.quote_balance() + gross - fee
        db.set_state("paper_cash", cash)
        db.add_ledger("paper", "SELL", gross - fee, cash, f"Sell {qty:.8f} {symbol} @ {px:.4f} (fee {fee:.4f})")
        return Fill(qty=qty, price=px, quote=gross - fee, fee_usd=fee, order_id="paper")

    def fill_at(self, symbol: str, side: str, qty: float, px: float) -> Fill:
        """Demo LIMIT order execution at its limit price (the trader calls this once price crosses it)."""
        gross = qty * px
        fee = gross * config.TAKER_FEE
        cash = self.quote_balance()
        if side == "BUY":
            if gross + fee > cash + 1e-9:
                raise BrokerError(f"Demo cash {cash:.2f} < {gross + fee:.2f} needed")
            cash -= gross + fee
            db.set_state("paper_cash", cash)
            db.add_ledger("paper", "BUY", -(gross + fee), cash, f"Limit buy {qty:.8f} {symbol} @ {px:.4f} (fee {fee:.4f})")
            return Fill(qty=qty, price=px, quote=gross + fee, fee_usd=fee, order_id="paper")
        cash += gross - fee
        db.set_state("paper_cash", cash)
        db.add_ledger("paper", "SELL", gross - fee, cash, f"Limit sell {qty:.8f} {symbol} @ {px:.4f} (fee {fee:.4f})")
        return Fill(qty=qty, price=px, quote=gross - fee, fee_usd=fee, order_id="paper")

    # Paper stops are enforced by the trader's own price checks.
    def place_stop(self, symbol: str, qty: float, stop_price: float) -> str | None:
        return None

    def cancel_stop(self, symbol: str, order_id: str | None):
        return None

    def stop_fill(self, symbol: str, order_id: str | None) -> Fill | None:
        return None


class BinanceBroker:
    mode = "live"
    has_exchange_stops = True

    def __init__(self):
        if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
            raise BrokerError("Binance API key / secret not set — add them in Settings > Credentials")
        self.ex = apply_binance_url(ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "fetchMarkets": ["spot"]},
        }))
        self.ex.load_markets()
        self.ex.fetch_balance()   # fail fast on bad keys / permissions

    def price(self, symbol: str) -> float:
        t = self.ex.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close"))

    def quote_balance(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal.get("free", {}).get(config.QUOTE_ASSET) or 0.0)

    def base_free(self, symbol: str) -> float:
        base = self.ex.market(symbol)["base"]
        return float(self.ex.fetch_balance().get("free", {}).get(base) or 0.0)

    def min_order_usd(self, symbol: str) -> float:
        limits = self.ex.market(symbol).get("limits", {})
        exch_min = (limits.get("cost") or {}).get("min") or 0.0
        return max(config.MIN_ORDER_USD, float(exch_min) * 1.1)

    def _fill_from_order(self, symbol: str, order: dict, side: str) -> Fill:
        if order.get("id") and (order.get("filled") is None or order.get("average") is None):
            order = self.ex.fetch_order(order["id"], symbol)
        market = self.ex.market(symbol)
        filled = float(order.get("filled") or 0.0)
        cost = float(order.get("cost") or 0.0)
        avg = float(order.get("average") or (cost / filled if filled else 0.0))
        if filled <= 0:
            raise BrokerError(f"{side} order {order.get('id')} on {symbol} did not fill")
        fee_usd, fee_base = 0.0, 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            c, cur = float(f.get("cost") or 0.0), f.get("currency")
            if cur == market["quote"]:
                fee_usd += c
            elif cur == market["base"]:
                fee_base += c
                fee_usd += c * avg
            else:   # e.g. paid in BNB — approximate at the standard taker rate
                fee_usd += cost * config.TAKER_FEE
        if side == "buy":
            quote_fee = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == market["quote"])
            return Fill(qty=filled - fee_base, price=avg, quote=cost + quote_fee, fee_usd=fee_usd, order_id=str(order["id"]))
        quote_fee = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == market["quote"])
        return Fill(qty=filled, price=avg, quote=cost - quote_fee, fee_usd=fee_usd, order_id=str(order["id"]))

    def market_buy(self, symbol: str, usd: float) -> Fill:
        cost = float(self.ex.cost_to_precision(symbol, usd))
        order = self.ex.create_market_buy_order_with_cost(symbol, cost)
        return self._fill_from_order(symbol, order, "buy")

    def market_sell(self, symbol: str, qty: float) -> Fill:
        qty = min(qty, self.base_free(symbol))
        amount = float(self.ex.amount_to_precision(symbol, qty))
        if amount <= 0:
            raise BrokerError(f"Nothing to sell on {symbol} (free balance too small)")
        order = self.ex.create_order(symbol, "market", "sell", amount)
        return self._fill_from_order(symbol, order, "sell")

    # --- limit orders (Trade page order form) ---------------------------------

    def limit_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        amount = float(self.ex.amount_to_precision(symbol, qty))
        px = float(self.ex.price_to_precision(symbol, price))
        if amount <= 0:
            raise BrokerError("Amount too small for this market's precision")
        order = self.ex.create_order(symbol, "limit", side.lower(), amount, px, {"timeInForce": "GTC"})
        return str(order["id"])

    def order_update(self, symbol: str, order_id: str):
        """(status, Fill|None) for a limit order: status is 'open', 'closed' or 'canceled'."""
        order = self.ex.fetch_order(order_id, symbol)
        status = order.get("status") or "open"
        fill = None
        if status in ("closed", "canceled") and float(order.get("filled") or 0) > 0:
            fill = self._fill_from_order(symbol, order, order["side"])
        return status, fill

    def cancel_order(self, symbol: str, order_id: str):
        try:
            self.ex.cancel_order(order_id, symbol)
        except ccxt.OrderNotFound:
            pass

    def place_stop(self, symbol: str, qty: float, stop_price: float) -> str | None:
        qty = min(qty, self.base_free(symbol))
        amount = float(self.ex.amount_to_precision(symbol, qty))
        trigger = float(self.ex.price_to_precision(symbol, stop_price))
        limit = float(self.ex.price_to_precision(symbol, stop_price * (1 - config.STOP_LIMIT_BUFFER_PCT / 100)))
        order = self.ex.create_order(symbol, "STOP_LOSS_LIMIT", "sell", amount, limit,
                                     {"stopPrice": trigger, "timeInForce": "GTC"})
        return str(order["id"])

    def cancel_stop(self, symbol: str, order_id: str | None):
        if not order_id:
            return
        try:
            self.ex.cancel_order(order_id, symbol)
        except ccxt.OrderNotFound:
            pass

    def stop_fill(self, symbol: str, order_id: str | None) -> Fill | None:
        """If the exchange-side stop has (fully) filled, return that fill."""
        if not order_id:
            return None
        order = self.ex.fetch_order(order_id, symbol)
        if order.get("status") == "closed" and float(order.get("filled") or 0) > 0:
            return self._fill_from_order(symbol, order, "sell")
        return None


def make_broker(mode: str | None = None):
    mode = (mode or config.TRADING_MODE).lower()
    if mode == "paper":
        return PaperBroker()
    if mode == "live":
        return BinanceBroker()
    raise BrokerError(f"Unknown trading mode '{mode}' (use paper = Demo / live = Real)")
