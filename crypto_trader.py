import logging
from datetime import date
from alpaca_trade_api.rest import REST

from risk_manager import RiskManager
from notifier import Notifier
from config import (
    CRYPTO_STOP_LOSS, CRYPTO_TAKE_PROFIT,
    MAX_CRYPTO_POSITIONS, CRYPTO_MAX_DAILY_LOSS,
)

logger = logging.getLogger(__name__)

MIN_NOTIONAL = 5.0

_CRYPTO_SYMS = {
    "BTCUSD", "ETHUSD", "XRPUSD", "UNIUSD",
    "LINKUSD", "LTCUSD", "AAVEUSD", "AVAXUSD",
    "DOTUSD", "ADAUSD",
}


# ── Portfolio helpers ─────────────────────────────────────────────────────────

def crypto_position_count(api: REST) -> int:
    return sum(1 for p in api.list_positions() if p.symbol in _CRYPTO_SYMS)


def _has_pending_order(api: REST, alpaca_sym: str) -> bool:
    return any(o.symbol == alpaca_sym for o in api.list_orders(status="open"))


def _daily_loss_breached(api: REST) -> bool:
    """
    Compare today's portfolio value against the day-open snapshot stored in
    today's first activity entry. Falls back to a rough equity check.
    Returns True if we've lost more than CRYPTO_MAX_DAILY_LOSS today.
    """
    try:
        today = date.today().isoformat()
        history = api.get_portfolio_history(period="1D", timeframe="1H")
        if history.equity and len(history.equity) >= 2:
            open_val  = history.equity[0]
            cur_val   = float(api.get_account().portfolio_value)
            daily_ret = (cur_val - open_val) / open_val
            if daily_ret < -CRYPTO_MAX_DAILY_LOSS:
                logger.warning(
                    "Daily loss limit breached: %.2f%% (limit %.2f%%)",
                    daily_ret * 100, CRYPTO_MAX_DAILY_LOSS * 100,
                )
                return True
    except Exception as e:
        logger.debug("Daily loss check failed: %s", e)
    return False


# ── Order execution ───────────────────────────────────────────────────────────

def execute_signals(api: REST, signals: dict, risk: RiskManager, notifier: Notifier):
    if _daily_loss_breached(api):
        notifier.order_skipped("ALL", "daily loss limit hit — trading halted for today")
        return

    for symbol, (signal, ctx) in signals.items():
        try:
            if signal == "BUY":
                _buy(api, symbol, ctx, risk, notifier)
        except Exception as e:
            notifier.error(symbol, e)


def _buy(api: REST, symbol: str, ctx: dict, risk: RiskManager, notifier: Notifier):
    alpaca_sym = symbol.replace("/", "")

    if risk.already_holding(symbol):
        notifier.order_skipped(symbol, "already holding position")
        return
    if _has_pending_order(api, alpaca_sym):
        notifier.order_skipped(symbol, "pending order already exists")
        return
    if crypto_position_count(api) >= MAX_CRYPTO_POSITIONS:
        notifier.order_skipped(symbol, f"max crypto positions ({MAX_CRYPTO_POSITIONS}) reached")
        return
    if not risk.can_trade():
        notifier.order_skipped(symbol, "risk check failed")
        return

    price   = ctx.get("close") or _latest_price(api, symbol)
    dollars = risk.trade_dollar_amount()

    if dollars < MIN_NOTIONAL:
        notifier.order_skipped(symbol, f"trade amount ${dollars:.2f} below minimum")
        return

    qty            = round(dollars / price, 6)
    take_profit_px = round(price * (1 + CRYPTO_TAKE_PROFIT), 4)
    stop_px        = round(price * (1 - CRYPTO_STOP_LOSS), 4)

    # Bracket order: market entry + limit take-profit + stop-loss.
    # Both exit legs live at the exchange — zero polling required.
    api.submit_order(
        symbol=alpaca_sym,
        qty=qty,
        side="buy",
        type="market",
        time_in_force="gtc",
        order_class="bracket",
        take_profit={"limit_price": str(take_profit_px)},
        stop_loss={"stop_price": str(stop_px)},
    )
    notifier.order_placed(symbol, "buy", qty, price)
    logger.info(
        "BUY %s | qty=%.6f | entry≈%.4f | TP=%.4f (+2.5%%) | SL=%.4f (-1.5%%) "
        "| RSI=%.1f | ADX=%.1f | BBW=%.4f",
        symbol, qty, price, take_profit_px, stop_px,
        ctx.get("rsi", 0), ctx.get("adx", 0), ctx.get("bbw", 0),
    )
    try:
        from telegram_notifier import send_alert
        send_alert(
            f"🚀 <b>BUY ORDER PLACED</b>\n\n"
            f"  Pair:   <b>{symbol}</b>\n"
            f"  Entry:  ${price:,.4f}\n"
            f"  Qty:    {qty:.6f}\n"
            f"  TP:     ${take_profit_px:,.4f}  <i>(+2.5%)</i>\n"
            f"  SL:     ${stop_px:,.4f}  <i>(-1.5%)</i>\n"
            f"  RSI:    {ctx.get('rsi', 0):.1f}   ADX: {ctx.get('adx', 0):.1f}"
        )
    except Exception:
        pass


def _latest_price(api: REST, symbol: str) -> float:
    bar = api.get_latest_crypto_bars([symbol], loc="us")
    return float(bar[symbol].close)
