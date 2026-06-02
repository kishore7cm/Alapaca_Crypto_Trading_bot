import logging
import time
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

# Per-symbol SL cooldown: block re-entry for this many hours after a stop-loss exit
SL_COOLDOWN_HOURS = 4
_sl_cooldown: dict[str, float] = {}   # symbol → monotonic time of last SL exit


def _in_sl_cooldown(symbol: str) -> bool:
    last_sl = _sl_cooldown.get(symbol)
    if last_sl is None:
        return False
    return (time.monotonic() - last_sl) < SL_COOLDOWN_HOURS * 3600


def _set_sl_cooldown(symbol: str) -> None:
    _sl_cooldown[symbol] = time.monotonic()
    logger.info("SL cooldown set for %s — no re-entry for %dh", symbol, SL_COOLDOWN_HOURS)

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


def _normalize_sym(sym: str) -> str:
    return sym.replace("/", "")


def _has_pending_order(api: REST, alpaca_sym: str) -> bool:
    return any(_normalize_sym(o.symbol) == alpaca_sym for o in api.list_orders(status="open"))


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

    if _in_sl_cooldown(symbol):
        remaining = SL_COOLDOWN_HOURS - (time.monotonic() - _sl_cooldown[symbol]) / 3600
        notifier.order_skipped(symbol, f"SL cooldown active — {remaining:.1f}h remaining")
        return
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
    buy_order = api.submit_order(
        symbol=alpaca_sym,
        qty=qty,
        side="buy",
        type="market",
        time_in_force="gtc",
    )

    # Alpaca deducts the trading fee from received crypto (~0.25%), so the position
    # qty is less than the ordered qty. Poll until filled, then use the actual
    # position qty for the TP sell to avoid "insufficient qty" rejections.
    tp_qty = qty
    try:
        for _ in range(10):
            time.sleep(1)
            o = api.get_order(buy_order.id)
            if o.status == "filled":
                pos = api.get_position(alpaca_sym)
                tp_qty = abs(float(pos.qty))
                break
    except Exception as poll_err:
        logger.warning("TP qty poll failed for %s: %s — using calculated qty", symbol, poll_err)

    try:
        api.submit_order(
            symbol=alpaca_sym,
            qty=tp_qty,
            side="sell",
            type="limit",
            time_in_force="gtc",
            limit_price=str(take_profit_px),
        )
        logger.info("TP limit order placed for %s | qty=%.6f | limit=%.4f", symbol, tp_qty, take_profit_px)
    except Exception as tp_err:
        logger.warning("TP limit order failed for %s: %s — SL-only mode", symbol, tp_err)

    notifier.order_placed(symbol, "buy", qty, price)
    logger.info(
        "BUY %s | qty=%.6f | entry≈%.4f | TP=%.4f (+%.1f%%) | SL=%.4f (-%.1f%%) "
        "| RSI=%.1f | ADX=%.1f | BBW=%.4f",
        symbol, qty, price, take_profit_px, tp_pct, stop_px, CRYPTO_STOP_LOSS * 100,
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


def _open_sell_qty(api: REST, alpaca_sym: str) -> float:
    """Return total qty of open sell orders for a symbol (0.0 if none)."""
    return sum(
        float(o.qty)
        for o in api.list_orders(status="open")
        if _normalize_sym(o.symbol) == alpaca_sym and o.side == "sell"
    )


def manage_exits(api: REST, notifier: Notifier):
    """Enforce stop-loss on open crypto positions each cycle.

    Called because Alpaca crypto does not support bracket/OCO orders — the TP
    leg is a standalone limit sell already at the exchange; the SL leg must be
    polled here and fired as a market sell when breached.

    Also recovers missing TP orders: if a position has no open sell order
    (e.g. the original TP submission failed due to fee-reduced qty), a new
    GTC limit sell is placed at entry_price * (1 + CRYPTO_TAKE_PROFIT).
    """
    for pos in api.list_positions():
        if pos.symbol not in _CRYPTO_SYMS:
            continue
        symbol    = pos.symbol[:-3] + "/USD"   # "BTCUSD" → "BTC/USD"
        entry_px  = float(pos.avg_entry_price)
        cur_px    = float(pos.current_price)
        pnl_pct   = (cur_px - entry_px) / entry_px

        # Recovery: place a TP limit sell if no open sell order exists
        if _open_sell_qty(api, pos.symbol) == 0.0:
            tp_px  = max(
                round(entry_px * (1 + CRYPTO_TAKE_PROFIT), 4),
                round(entry_px + _ALPACA_MIN_TP_GAP, 4),
            )
            pos_qty = abs(float(pos.qty))
            try:
                api.submit_order(
                    symbol=pos.symbol,
                    qty=pos_qty,
                    side="sell",
                    type="limit",
                    time_in_force="gtc",
                    limit_price=str(tp_px),
                )
                logger.info(
                    "TP RECOVERY %s | qty=%.6f | limit=%.4f (+%.1f%%)",
                    symbol, pos_qty, tp_px, CRYPTO_TAKE_PROFIT * 100,
                )
            except Exception as e:
                logger.warning("TP recovery failed for %s: %s", symbol, e)

        if pnl_pct <= -CRYPTO_STOP_LOSS:
            logger.warning(
                "SL HIT %s | entry=%.4f | now=%.4f | P&L=%.2f%%",
                symbol, entry_px, cur_px, pnl_pct * 100,
            )
            for order in api.list_orders(status="open"):
                if _normalize_sym(order.symbol) == pos.symbol and order.side == "sell":
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
                _set_sl_cooldown(symbol)
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
