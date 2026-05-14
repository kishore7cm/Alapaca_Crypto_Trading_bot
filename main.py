import logging
import time
from datetime import datetime

import schedule

from data import get_api, fetch_bars
from strategy import scan
from trader import execute_signals
from risk_manager import RiskManager
from notifier import Notifier
from config import STOCK_RISK_PCT

logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def run_cycle():
    api      = get_api()
    risk     = RiskManager(api, risk_pct=STOCK_RISK_PCT)
    notifier = Notifier()

    notifier.cycle_start("STOCK")
    signals = scan(api, fetch_bars)
    execute_signals(api, signals, risk, notifier)
    notifier.cycle_end("STOCK")


def market_open() -> bool:
    clock = get_api().get_clock()
    return clock.is_open


def scheduled_run():
    if market_open():
        run_cycle()
    else:
        logger.info("Market closed — skipping cycle")


# Run at 9:35 AM ET on market days
schedule.every().day.at("09:35").do(scheduled_run)

if __name__ == "__main__":
    logger.info("Stock trading bot started")
    while True:
        schedule.run_pending()
        time.sleep(30)
