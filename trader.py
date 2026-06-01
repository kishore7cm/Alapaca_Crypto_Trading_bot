import logging
import time

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import REST

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    STOCK_STOP_LOSS,
)

_log = logging.getLogger(__name__)

_FILL_POLL_INTERVAL = 2   # seconds
_FILL_TIMEOUT       = 30  # seconds before cancelling


# ── API factory ───────────────────────────────────────────────────────────────

def _get_api() -> tradeapi.REST:
    return tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)


# ── Account ───────────────────────────────────────────────────────────────────

def get_account_value() -> float:
    return float(_get_api().get_account().portfolio_value)


# ── Price ─────────────────────────────────────────────────────────────────────

def get_current_price(symbol: str) -> float:
    api = _get_api()
    if "/" in symbol:
        bars = api.get_latest_crypto_bars([symbol], loc="us")
        return float(bars[symbol].close)
    return float(api.get_latest_trade(symbol).price)


# ── Positions ─────────────────────────────────────────────────────────────────

def get_open_positions() -> list:
    return _get_api().list_positions()


# ── Order helpers ─────────────────────────────────────────────────────────────

def _alpaca_sym(symbol: str) -> str:
    return symbol.replace("/", "")


def _wait_for_fill(api: tradeapi.REST, order_id: str):
    """Poll until filled or 30 s; cancel on timeout and return None."""
    deadline = time.monotonic() + _FILL_TIMEOUT
    while time.monotonic() < deadline:
        order = api.get_order(order_id)
        if order.status == "filled":
            return order
        if order.status in ("canceled", "expired", "rejected"):
            _log.warning("Order %s ended unfilled: status=%s", order_id, order.status)
            return None
        time.sleep(_FILL_POLL_INTERVAL)

    try:
        api.cancel_order(order_id)
        _log.warning("Order %s timed out after %ds — cancelled", order_id, _FILL_TIMEOUT)
    except Exception as e:
        _log.error("Cancel failed for order %s: %s", order_id, e)
    return None


# ── Buy / Sell ────────────────────────────────────────────────────────────────

def place_buy_order(symbol: str, shares: float) -> dict | None:
    api = _get_api()
    try:
        order = api.submit_order(
            symbol=_alpaca_sym(symbol),
            qty=shares,
            side="buy",
            type="market",
            time_in_force="gtc",
        )
    except Exception as e:
        _log.error("BUY rejected %s: %s", symbol, e)
        print(f"[TRADER] BUY REJECTED  {symbol} | {e}")
        return None

    print(f"[TRADER] BUY SUBMITTED {symbol} | qty={shares} | order_id={order.id}")

    filled = _wait_for_fill(api, order.id)
    if filled is None:
        return None

    result = {
        "order_id":     filled.id,
        "filled_price": float(filled.filled_avg_price),
        "shares":       float(filled.filled_qty),
        "timestamp":    filled.filled_at,
    }
    print(
        f"[TRADER] BUY FILLED    {symbol} | "
        f"price={result['filled_price']:.4f} | shares={result['shares']} | "
        f"id={result['order_id']}"
    )
    return result


def place_sell_order(symbol: str, shares: float, reason: str) -> dict | None:
    api = _get_api()
    try:
        order = api.submit_order(
            symbol=_alpaca_sym(symbol),
            qty=shares,
            side="sell",
            type="market",
            time_in_force="gtc",
        )
    except Exception as e:
        _log.error("SELL rejected %s (%s): %s", symbol, reason, e)
        print(f"[TRADER] SELL REJECTED  {symbol} | reason={reason} | {e}")
        return None

    print(f"[TRADER] SELL SUBMITTED {symbol} | qty={shares} | reason={reason} | order_id={order.id}")

    filled = _wait_for_fill(api, order.id)
    if filled is None:
        return None

    result = {
        "order_id":     filled.id,
        "filled_price": float(filled.filled_avg_price),
        "shares":       float(filled.filled_qty),
        "timestamp":    filled.filled_at,
    }
    print(
        f"[TRADER] SELL FILLED    {symbol} | "
        f"price={result['filled_price']:.4f} | shares={result['shares']} | "
        f"reason={reason} | id={result['order_id']}"
    )
    return result


# ── TradeSession ──────────────────────────────────────────────────────────────

class TradeSession:
    """Tracks all trades opened in the current bot cycle."""

    def __init__(self):
        self._positions: dict = {}  # symbol -> trade info

    @property
    def active_positions(self) -> dict:
        return dict(self._positions)

    def open_trade(self, symbol: str, entry_price: float, shares: float, entry_time):
        self._positions[symbol] = {
            "symbol":      symbol,
            "entry_price": entry_price,
            "shares":      shares,
            "entry_time":  entry_time,
        }
        _log.info("SESSION open  %s | entry=%.4f | shares=%s", symbol, entry_price, shares)

    def close_trade(self, symbol: str, exit_price: float, exit_time, reason: str):
        trade = self._positions.pop(symbol, None)
        if trade is None:
            _log.warning("close_trade: no open position for %s", symbol)
            return

        pnl     = (exit_price - trade["entry_price"]) * trade["shares"]
        pnl_pct = (exit_price / trade["entry_price"] - 1) * 100

        record = {
            **trade,
            "exit_price": exit_price,
            "exit_time":  exit_time,
            "reason":     reason,
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
        }
        _log.info(
            "SESSION close %s | exit=%.4f | pnl=$%.2f (%.2f%%) | reason=%s",
            symbol, exit_price, pnl, pnl_pct, reason,
        )

        try:
            from logger import log_trade
            log_trade(record)
        except ImportError:
            _log.debug("logger.log_trade unavailable — trade record not persisted")


# ── Legacy stock-bot interface (used by main.py) ──────────────────────────────

from risk_manager import RiskManager
from notifier import Notifier


def execute_signals(api: REST, signals: dict, risk: RiskManager, notifier: Notifier):
    for symbol, signal in signals.items():
        try:
            if signal == "BUY":
                _buy_stock(api, symbol, risk, notifier)
            elif signal == "SELL":
                _sell_stock(api, symbol, risk, notifier)
        except Exception as e:
            notifier.error(symbol, e)


def _buy_stock(api: REST, symbol: str, risk: RiskManager, notifier: Notifier):
    if risk.already_holding(symbol):
        notifier.order_skipped(symbol, "already holding position")
        return
    if not risk.can_trade():
        notifier.order_skipped(symbol, "risk check failed")
        return

    price   = float(api.get_latest_trade(symbol).price)
    dollars = risk.trade_dollar_amount()
    qty     = int(dollars / price)

    if qty < 1:
        notifier.order_skipped(symbol, f"insufficient funds for 1 share at {price:.2f}")
        return

    stop_price = round(price * (1 - STOCK_STOP_LOSS), 2)

    api.submit_order(
        symbol=symbol,
        qty=qty,
        side="buy",
        type="market",
        time_in_force="day",
        order_class="oto",
        stop_loss={"stop_price": str(stop_price)},
    )
    notifier.order_placed(symbol, "buy", qty, price)


def _sell_stock(api: REST, symbol: str, risk: RiskManager, notifier: Notifier):
    if not risk.already_holding(symbol):
        notifier.order_skipped(symbol, "no position to sell")
        return

    pos = api.get_position(symbol)
    qty = abs(int(float(pos.qty)))

    api.submit_order(
        symbol=symbol,
        qty=qty,
        side="sell",
        type="market",
        time_in_force="day",
    )
    notifier.order_placed(symbol, "sell", qty, float(pos.current_price))
