import logging
from alpaca_trade_api.rest import REST

from risk_manager import RiskManager
from notifier import Notifier
from config import STOCK_STOP_LOSS

logger = logging.getLogger(__name__)


def execute_signals(api: REST, signals: dict, risk: RiskManager, notifier: Notifier):
    for symbol, signal in signals.items():
        try:
            if signal == "BUY":
                _buy(api, symbol, risk, notifier)
            elif signal == "SELL":
                _sell(api, symbol, risk, notifier)
        except Exception as e:
            notifier.error(symbol, e)


def _buy(api: REST, symbol: str, risk: RiskManager, notifier: Notifier):
    if risk.already_holding(symbol):
        notifier.order_skipped(symbol, "already holding position")
        return
    if not risk.can_trade():
        notifier.order_skipped(symbol, "risk check failed")
        return

    quote     = api.get_latest_trade(symbol)
    price     = float(quote.price)
    dollars   = risk.trade_dollar_amount()
    qty       = int(dollars / price)

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


def _sell(api: REST, symbol: str, risk: RiskManager, notifier: Notifier):
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
