import logging

logger = logging.getLogger(__name__)


class Notifier:
    def order_placed(self, symbol: str, side: str, qty: float, price: float):
        logger.info("ORDER  | %-20s | %-20s | qty=%.6f | price=%.4f", symbol, side.upper(), qty, price)

    def order_skipped(self, symbol: str, reason: str):
        logger.info("SKIP   | %-20s | %s", symbol, reason)

    def error(self, symbol: str, exc: Exception):
        logger.error("ERROR  | %-20s | %s", symbol, exc)

    def cycle_start(self, label: str = ""):
        logger.info("=" * 60)
        logger.info("CYCLE START  %s", label)

    def cycle_end(self, label: str = ""):
        logger.info("CYCLE END    %s", label)
        logger.info("=" * 60)

    def signal(self, symbol: str, sig: str, ctx: dict):
        if ctx:
            logger.info(
                "SIGNAL | %-10s | %-4s | close=%.2f lower=%.2f upper=%.2f RSI=%.1f",
                symbol, sig,
                ctx.get("close", 0), ctx.get("lower", 0),
                ctx.get("upper", 0), ctx.get("rsi", 0),
            )
        else:
            logger.info("SIGNAL | %-10s | %s", symbol, sig)
