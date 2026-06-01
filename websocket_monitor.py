"""
Real-time WebSocket price monitor using alpaca-py CryptoDataStream.

Subscribes to 1-minute bars for all 10 pairs.
When any pair drops > TRIGGER_DROP_PCT in a single bar, fires an
immediate strategy cycle instead of waiting for the next hourly check.

Runs in its own daemon thread. A cooldown prevents flooding.
"""
import asyncio
import logging
import threading
import time
from collections import defaultdict

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, CRYPTO_SYMBOLS

logger = logging.getLogger(__name__)

TRIGGER_DROP_PCT = 0.5    # % drop in one 1-min bar that triggers an early cycle
COOLDOWN_MINUTES = 10     # min gap between WebSocket-triggered cycles per symbol


class WebSocketMonitor:
    def __init__(self, on_trigger):
        self._on_trigger   = on_trigger
        self._price_cache  = {}
        self._last_trigger = defaultdict(lambda: 0.0)
        self._thread       = None
        self._running      = False

    def _cooled_down(self, symbol: str) -> bool:
        return (time.monotonic() - self._last_trigger[symbol]) > COOLDOWN_MINUTES * 60

    async def _bar_handler(self, bar):
        sym   = bar.symbol   # alpaca-py uses "BTC/USD" format directly
        close = float(bar.close)
        prev  = self._price_cache.get(sym)
        self._price_cache[sym] = close

        if prev is None or prev == 0:
            return

        change_pct = (close - prev) / prev * 100

        if change_pct <= -TRIGGER_DROP_PCT and self._cooled_down(sym):
            logger.info(
                "WS TRIGGER %s | %.4f | 1-min chg=%.2f%% → immediate cycle",
                sym, close, change_pct,
            )
            self._last_trigger[sym] = time.monotonic()
            threading.Thread(
                target=self._on_trigger,
                args=(sym, close, change_pct),
                daemon=True,
            ).start()

    def _thread_target(self):
        from alpaca.data.live import CryptoDataStream

        delay = 20
        while self._running:
            try:
                stream = CryptoDataStream(
                    api_key=ALPACA_API_KEY,
                    secret_key=ALPACA_SECRET_KEY,
                )
                stream.subscribe_bars(self._bar_handler, *CRYPTO_SYMBOLS)
                logger.info(
                    "WebSocket connected — watching %d pairs for >%.1f%% drops",
                    len(CRYPTO_SYMBOLS), TRIGGER_DROP_PCT,
                )
                delay = 20  # reset backoff on successful connection
                stream.run()
            except Exception as e:
                if self._running:
                    logger.warning("WebSocket dropped: %s — reconnecting in %ds", e, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 300)  # exponential backoff, cap at 5min

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._thread_target, daemon=True, name="ws-monitor"
        )
        self._thread.start()
        logger.info("WebSocket monitor thread started")

    def stop(self):
        self._running = False
