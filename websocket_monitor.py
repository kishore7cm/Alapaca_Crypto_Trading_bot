"""
Real-time WebSocket price monitor for the crypto bot.

Subscribes to 1-minute bars for all 10 pairs via Alpaca's crypto stream.
When any pair drops sharply (> TRIGGER_DROP_PCT in one bar), fires an
immediate strategy cycle instead of waiting for the next hourly check.

Architecture:
  - Runs in its own daemon thread with a dedicated asyncio event loop
  - Communicates with the main scheduler via a threading.Event
  - A cooldown prevents the same symbol triggering more than once per
    COOLDOWN_MINUTES to avoid flooding the strategy with micro-moves
"""
import asyncio
import logging
import threading
import time
from collections import defaultdict

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, CRYPTO_SYMBOLS

logger = logging.getLogger(__name__)

TRIGGER_DROP_PCT  = 0.5    # % drop in a single 1-min bar that triggers early cycle
COOLDOWN_MINUTES  = 10     # min gap between WebSocket-triggered cycles per symbol


class WebSocketMonitor:
    def __init__(self, on_trigger):
        """
        on_trigger(symbol, price, change_pct) — called when a sharp drop is detected.
        Runs the full strategy cycle from crypto_main.
        """
        self._on_trigger   = on_trigger
        self._price_cache  = {}                       # symbol → last close
        self._last_trigger = defaultdict(lambda: 0)  # symbol → last trigger time
        self._loop         = None
        self._thread       = None
        self._running      = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _is_cooled_down(self, symbol: str) -> bool:
        elapsed = time.monotonic() - self._last_trigger[symbol]
        return elapsed > COOLDOWN_MINUTES * 60

    def _alpaca_to_pair(self, alpaca_sym: str) -> str:
        """BTCUSD → BTC/USD"""
        for pair in CRYPTO_SYMBOLS:
            if pair.replace("/", "") == alpaca_sym:
                return pair
        return alpaca_sym

    # ── WebSocket handler ─────────────────────────────────────────────────────

    async def _bar_handler(self, bar):
        sym   = self._alpaca_to_pair(bar.symbol)
        close = float(bar.close)
        prev  = self._price_cache.get(sym)

        self._price_cache[sym] = close

        if prev is None or prev == 0:
            return

        change_pct = (close - prev) / prev * 100

        if change_pct <= -TRIGGER_DROP_PCT and self._is_cooled_down(sym):
            logger.info(
                "WS TRIGGER %s | price=%.4f | 1-min change=%.2f%% — running immediate cycle",
                sym, close, change_pct,
            )
            self._last_trigger[sym] = time.monotonic()
            # Call on_trigger in a separate thread to avoid blocking the event loop
            threading.Thread(
                target=self._on_trigger,
                args=(sym, close, change_pct),
                daemon=True,
            ).start()

    # ── Stream lifecycle ──────────────────────────────────────────────────────

    async def _run_stream(self):
        from alpaca_trade_api.stream import Stream

        stream = Stream(
            key_id=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            data_feed="crypto",
            raw_data=False,
        )

        alpaca_syms = [s.replace("/", "") for s in CRYPTO_SYMBOLS]
        stream.subscribe_crypto_bars(self._bar_handler, *alpaca_syms)

        logger.info("WebSocket connected — monitoring %d pairs for >%.1f%% drops",
                    len(alpaca_syms), TRIGGER_DROP_PCT)
        await stream._run_forever()

    def _thread_target(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        while self._running:
            try:
                self._loop.run_until_complete(self._run_stream())
            except Exception as e:
                if self._running:
                    logger.error("WebSocket disconnected: %s — reconnecting in 15s", e)
                    time.sleep(15)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._thread_target,
            daemon=True,
            name="ws-monitor",
        )
        self._thread.start()
        logger.info("WebSocket monitor thread started")

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("WebSocket monitor stopped")
