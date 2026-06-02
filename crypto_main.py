"""
Entry point — starts three threads in one process:
  • Scheduler thread   : hourly trading cycle + daily Telegram report
  • API server thread  : FastAPI control layer for Claude / HTTP clients
  • WebSocket thread   : real-time 1-min bars; triggers immediate cycle on sharp drops
"""
import logging
import threading
import time

import schedule
import uvicorn

from crypto_data import get_api, fetch_crypto_bars
from crypto_strategy import scan
from crypto_trader import execute_signals, crypto_position_count, manage_exits
from risk_manager import RiskManager
from notifier import Notifier
from performance import _print_report as report
from telegram_notifier import send_daily_summary, send_alert
from config import CRYPTO_RISK_PCT, MAX_CRYPTO_POSITIONS

logging.basicConfig(
    filename="crypto_bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(console)

logger = logging.getLogger(__name__)

# Prevent concurrent cycles (hourly + WebSocket trigger could overlap)
_cycle_lock = threading.Lock()


# ── Trading cycle ──────────────────────────────────────────────────────────────

def run_cycle(trigger: str = "scheduled"):
    from control_api import _paused
    if _paused.is_set():
        logger.info("Bot is paused — skipping cycle (%s)", trigger)
        return

    if not _cycle_lock.acquire(blocking=False):
        logger.info("Cycle already running — skipping (%s trigger)", trigger)
        return

    try:
        api      = get_api()
        risk     = RiskManager(api, risk_pct=CRYPTO_RISK_PCT)
        notifier = Notifier()

        notifier.cycle_start(f"CRYPTO [{trigger}]")
        acc   = api.get_account()
        n_pos = crypto_position_count(api)
        logger.info(
            "Portfolio=$%.2f  Cash=$%.2f  CryptoPos=%d/%d  trigger=%s",
            float(acc.portfolio_value), float(acc.cash),
            n_pos, MAX_CRYPTO_POSITIONS, trigger,
        )

        manage_exits(api, notifier)

        if n_pos >= MAX_CRYPTO_POSITIONS:
            logger.info("Slots full — monitoring exits")
            notifier.cycle_end("CRYPTO")
            return

        signals = scan(api, fetch_crypto_bars)
        for sym, (sig, ctx) in signals.items():
            if sig != "HOLD":
                notifier.signal(sym, sig, ctx)
            elif ctx.get("gates") and sum(ctx["gates"].values()) >= 4:
                notifier.signal(sym, f"NEAR-MISS ({sum(ctx['gates'].values())}/5)", ctx)

        execute_signals(api, signals, risk, notifier)
        notifier.cycle_end("CRYPTO")

    except Exception as e:
        logger.error("Cycle error: %s", e, exc_info=True)
    finally:
        _cycle_lock.release()


def run_daily_report():
    try:
        report(days=7)
    except Exception as e:
        logger.error("Performance report failed: %s", e)


# ── WebSocket trigger callback ─────────────────────────────────────────────────

def _on_ws_trigger(symbol: str, price: float, change_pct: float):
    """Called by WebSocketMonitor when a sharp drop is detected on any pair."""
    logger.info(
        "WS TRIGGER received: %s dropped %.2f%% to %.4f — firing immediate cycle",
        symbol, abs(change_pct), price,
    )
    try:
        send_alert(
            f"⚡ <b>WebSocket Trigger</b>\n"
            f"  {symbol} dropped <b>{change_pct:.2f}%</b> in 1 min (${price:,.4f})\n"
            f"  Running immediate strategy check..."
        )
    except Exception:
        pass
    run_cycle(trigger=f"websocket:{symbol}")


# ── Scheduler thread ───────────────────────────────────────────────────────────

def _start_scheduler():
    import os
    report_time_utc = os.environ.get("REPORT_TIME_UTC", "05:00")

    schedule.every().hour.at(":00").do(run_cycle, trigger="scheduled")
    schedule.every().day.at("00:00").do(run_daily_report)
    schedule.every().day.at(report_time_utc).do(send_daily_summary)

    logger.info(
        "Scheduler started — hourly cycles + WebSocket triggers | daily report at %s UTC",
        report_time_utc,
    )
    run_cycle(trigger="startup")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── API server thread ──────────────────────────────────────────────────────────

def _start_api():
    import os
    port = int(os.environ.get("PORT", 8080))
    logger.info("Control API starting on port %d", port)
    uvicorn.run(
        "control_api:app",
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    if not os.environ.get("CRYPTO_ENABLED", "").lower() == "true":
        logger.info("Crypto bot DISABLED (set CRYPTO_ENABLED=true to re-enable). Exiting.")
        raise SystemExit(0)

    logger.info(
        "Crypto bot v3 | 10 pairs | BB(48)+RSI+ADX+News | "
        "Bracket orders TP=+2.5%% SL=-1.5%% | WebSocket real-time"
    )

    # API thread
    threading.Thread(target=_start_api, daemon=True, name="api").start()

    # WebSocket monitor thread
    from websocket_monitor import WebSocketMonitor
    ws = WebSocketMonitor(on_trigger=_on_ws_trigger)
    ws.start()

    # Scheduler — blocks main thread
    _start_scheduler()
