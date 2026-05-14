"""
Entry point — starts two threads in one process:
  • Scheduler thread : runs the trading cycle every hour
  • API server thread : FastAPI control layer for Claude / HTTP clients

Usage:
  python3 crypto_main.py            # both scheduler + API
  BOT_API_KEY=secret python3 crypto_main.py
"""
import logging
import threading
import time

import schedule
import uvicorn

from crypto_data import get_api, fetch_crypto_bars
from crypto_strategy import scan
from crypto_trader import execute_signals, crypto_position_count
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


# ── Trading cycle ─────────────────────────────────────────────────────────────

def run_cycle():
    from control_api import _paused   # respect pause flag set via API
    if _paused.is_set():
        logger.info("Bot is paused — skipping cycle")
        return

    api      = get_api()
    risk     = RiskManager(api, risk_pct=CRYPTO_RISK_PCT)
    notifier = Notifier()

    notifier.cycle_start("CRYPTO")
    try:
        acc      = api.get_account()
        n_pos    = crypto_position_count(api)
        logger.info(
            "Portfolio=$%.2f  Cash=$%.2f  CryptoPos=%d/%d",
            float(acc.portfolio_value), float(acc.cash),
            n_pos, MAX_CRYPTO_POSITIONS,
        )

        if n_pos >= MAX_CRYPTO_POSITIONS:
            logger.info("Slots full — waiting for bracket orders to close")
            notifier.cycle_end("CRYPTO")
            return

        signals = scan(api, fetch_crypto_bars)
        for sym, (sig, ctx) in signals.items():
            if sig != "HOLD":
                notifier.signal(sym, sig, ctx)
            elif ctx.get("gates") and sum(ctx["gates"].values()) >= 4:
                notifier.signal(sym, f"NEAR-MISS ({sum(ctx['gates'].values())}/5)", ctx)

        execute_signals(api, signals, risk, notifier)

    except Exception as e:
        logger.error("Cycle error: %s", e, exc_info=True)

    notifier.cycle_end("CRYPTO")


def run_daily_report():
    try:
        report(days=7)
    except Exception as e:
        logger.error("Performance report failed: %s", e)


# ── Scheduler thread ──────────────────────────────────────────────────────────

def _start_scheduler():
    import os
    # Daily email at 8 PM in user's timezone.
    # REPORT_TIME_UTC defaults to "00:00" (8 PM EST / midnight UTC).
    # Override with REPORT_TIME_UTC env var e.g. "20:00" for 8 PM UTC.
    report_time_utc = os.environ.get("REPORT_TIME_UTC", "00:00")

    schedule.every().hour.at(":00").do(run_cycle)
    schedule.every().day.at("00:00").do(run_daily_report)
    schedule.every().day.at(report_time_utc).do(send_daily_summary)
    logger.info(
        "Scheduler started — trading cycles every hour | email report at %s UTC",
        report_time_utc,
    )
    run_cycle()   # run immediately on start
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── API server thread ─────────────────────────────────────────────────────────

def _start_api():
    import os
    port = int(os.environ.get("PORT", 8080))
    logger.info("Control API starting on port %d", port)
    uvicorn.run(
        "control_api:app",
        host="0.0.0.0",
        port=port,
        log_level="warning",   # keep uvicorn quiet; trading logs use our handler
    )


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        "Crypto bot v2 starting | 10 pairs | BB(48)+RSI+Volume+ADX | "
        "Bracket orders TP=+2.5%% SL=-1.5%%"
    )

    api_thread = threading.Thread(target=_start_api, daemon=True, name="api")
    api_thread.start()

    _start_scheduler()   # blocks forever in main thread
