import logging
from datetime import date
from alpaca_trade_api.rest import REST

from risk_manager import RiskManager
from notifier import Notifier
from config import (
    CRYPTO_STOP_LOSS, CRYPTO_TAKE_PROFIT,
    MAX_CRYPTO_POSITIONS, CRYPTO_MAX_DAILY_LOSS,
)

# Alpaca crypto minimum price gap between entry and TP limit price
_ALPACA_MIN_TP_GAP = 0.01

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

def _btc_bearish_regime(signals: dict) -> tuple[bool, str]:
    """Return (True, reason) if BTC is below its 20-bar MA and trending (ADX > 25).
    Uses the BTC/USD context already computed by scan() — no extra API call needed."""
    btc = signals.get("BTC/USD")
    if not btc:
        return False, ""
    _, ctx = btc
    close = ctx.get("close", 0)
    ma    = ctx.get("ma", float("inf"))
    adx   = ctx.get("adx", 0)
    if close < ma and adx > 25:
        return True, f"BTC below MA ({close:,.0f} < {ma:,.0f}), ADX={adx:.1f}"
    return False, ""


def execute_signals(api: REST, signals: dict, risk: RiskManager, notifier: Notifier):
    if _daily_loss_breached(api):
        notifier.order_skipped("ALL", "daily loss limit hit — trading halted for today")
        return

    bearish, reason = _btc_bearish_regime(signals)
    if bearish:
        logger.info("BTC REGIME FILTER blocked all entries — %s", reason)
        try:
            from telegram_notifier import send_alert
            send_alert(f"🚫 <b>BTC Regime Filter Active</b>\n  {reason}\n  All new entries blocked this cycle.")
        except Exception:
            pass
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

    # Gate 6 — news sentiment: skip if bad news in last 4 hours
    from news_filter import is_safe_to_trade
    safe, headline = is_safe_to_trade(symbol)
    if not safe:
        notifier.order_skipped(symbol, f"bad news detected: {headline[:60]}")
        return

    price   = ctx.get("close") or _latest_price(api, symbol)
    dollars = risk.trade_dollar_amount()

    if dollars < MIN_NOTIONAL:
        notifier.order_skipped(symbol, f"trade amount ${dollars:.2f} below minimum")
        return

    qty = round(dollars / price, 6)
    # Ensure TP is at least $0.01 above entry — Alpaca crypto minimum gap
    take_profit_px = max(round(price * (1 + CRYPTO_TAKE_PROFIT), 4), round(price + _ALPACA_MIN_TP_GAP, 4))
    stop_px        = round(price * (1 - CRYPTO_STOP_LOSS), 4)
    tp_pct         = (take_profit_px / price - 1) * 100

    # Alpaca crypto does not support bracket/OCO orders — place market buy then
    # a separate GTC limit sell for the TP leg. SL is enforced in manage_exits().
    api.submit_order(
        symbol=alpaca_sym,
        qty=qty,
        side="buy",
        type="market",
        time_in_force="gtc",
    )

    try:
        api.submit_order(
            symbol=alpaca_sym,
            qty=qty,
            side="sell",
            type="limit",
            time_in_force="gtc",
            limit_price=str(take_profit_px),
        )
    except Exception as tp_err:
        logger.warning("TP limit order failed for %s: %s — SL-only mode", symbol, tp_err)

    notifier.order_placed(symbol, "buy", qty, price)
    logger.info(
        "BUY %s | qty=%.6f | entry≈%.4f | TP=%.4f (+%.1f%%) | SL=%.4f (-1.5%%) "
        "| RSI=%.1f | ADX=%.1f | BBW=%.4f",
        symbol, qty, price, take_profit_px, tp_pct, stop_px,
        ctx.get("rsi", 0), ctx.get("adx", 0), ctx.get("bbw", 0),
    )
    try:
        from telegram_notifier import send_alert
        send_alert(
            f"🚀 <b>BUY ORDER PLACED</b>\n\n"
            f"  Pair:   <b>{symbol}</b>\n"
            f"  Entry:  ${price:,.4f}\n"
            f"  Qty:    {qty:.6f}\n"
            f"  TP:     ${take_profit_px:,.4f}  <i>(+{tp_pct:.1f}%)</i>\n"
            f"  SL:     ${stop_px:,.4f}  <i>(-1.5%)</i>\n"
            f"  RSI:    {ctx.get('rsi', 0):.1f}   ADX: {ctx.get('adx', 0):.1f}"
        )
    except Exception:
        pass


def manage_exits(api: REST, notifier: Notifier):
    """Enforce stop-loss on open crypto positions each cycle.

    Called because Alpaca crypto does not support bracket/OCO orders — the TP
    leg is a standalone limit sell already at the exchange; the SL leg must be
    polled here and fired as a market sell when breached.
    """
    for pos in api.list_positions():
        if pos.symbol not in _CRYPTO_SYMS:
            continue
        symbol    = pos.symbol[:-3] + "/USD"   # "BTCUSD" → "BTC/USD"
        entry_px  = float(pos.avg_entry_price)
        cur_px    = float(pos.current_price)
        pnl_pct   = (cur_px - entry_px) / entry_px

        if pnl_pct <= -CRYPTO_STOP_LOSS:
            logger.warning(
                "SL HIT %s | entry=%.4f | now=%.4f | P&L=%.2f%%",
                symbol, entry_px, cur_px, pnl_pct * 100,
            )
            for order in api.list_orders(status="open"):
                if order.symbol == pos.symbol and order.side == "sell":
                    try:
                        api.cancel_order(order.id)
                        logger.info("Cancelled TP order %s for %s", order.id, symbol)
                    except Exception as e:
                        logger.warning("Failed to cancel TP order %s: %s", order.id, e)
            try:
                api.submit_order(
                    symbol=pos.symbol,
                    qty=abs(float(pos.qty)),
                    side="sell",
                    type="market",
                    time_in_force="gtc",
                )
                notifier.order_placed(symbol, "sell (stop-loss)", abs(float(pos.qty)), cur_px)
                logger.info("SL SELL %s | qty=%s | price=%.4f", symbol, pos.qty, cur_px)
                try:
                    from telegram_notifier import send_alert
                    send_alert(
                        f"🛑 <b>STOP-LOSS TRIGGERED</b>\n\n"
                        f"  Pair:   <b>{symbol}</b>\n"
                        f"  Entry:  ${entry_px:,.4f}\n"
                        f"  Exit:   ${cur_px:,.4f}\n"
                        f"  P&L:    {pnl_pct*100:.2f}%"
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.error("SL market sell failed for %s: %s", symbol, e)


def _latest_price(api: REST, symbol: str) -> float:
    bar = api.get_latest_crypto_bars([symbol], loc="us")
    return float(bar[symbol].close)
