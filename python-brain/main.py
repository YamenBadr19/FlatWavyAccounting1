"""
Gold Blueprint Trading System — Python Brain
Full Stack Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Signal flow:
  Telegram → [signal_queue] → MarketAnalyzer (5 filters + lot size)
           → [validated_queue] → ExecutionBridge (TTL check + audit)
           → DualAccountFIXExecutor.execute_signal()
           → asyncio.gather(LIVE FIX, DEMO FIX)   ← simultaneous

Coroutines running in parallel (8 total):
  1. MarketDataFeed.run_forever()          yfinance Gold data, 60s
  2. ForexNewsFeed.run_forever()           ForexFactory calendar, 5 min
  3. TelegramListener.run_with_reconnect() Telegram userbot
  4. MarketAnalyzer.process_signals_loop() 5-filter pipeline
  5. DualAccountFIXExecutor.run_forever()  LIVE + DEMO FIX sessions
  6. ExecutionBridge.relay_loop()          Execution relay + audit
  7. BreakEvenMonitor.run_forever()        TP1/TP2 → SL to break-even
  8. _heartbeat_loop()                     Watchdog liveness signal
"""

import asyncio
import logging
import sys
import os
import time
from pathlib import Path

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)-16s: %(message)s',
    handlers=[
        logging.FileHandler('gold_blueprint.log'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger('main')

from market_data_feed  import MarketDataFeed
from news_feed         import ForexNewsFeed
from telegram_listener import TelegramListener, BreakEvenMonitor
from market_analyzer   import MarketAnalyzer
from fix_executor      import DualAccountFIXExecutor
from signal_queue      import ExecutionBridge

# Heartbeat file — watchdog checks this to detect hung brain
HEARTBEAT_FILE     = Path("/tmp/gold_blueprint_heartbeat")
HEARTBEAT_INTERVAL = 30   # seconds


async def _heartbeat_loop():
    """
    Writes a Unix timestamp to HEARTBEAT_FILE every HEARTBEAT_INTERVAL seconds.
    watchdog.py reads this file — if it goes stale the watchdog restarts the brain.
    """
    logger.info(f"Heartbeat loop started → {HEARTBEAT_FILE} (every {HEARTBEAT_INTERVAL}s)")
    while True:
        try:
            HEARTBEAT_FILE.write_text(str(time.time()))
        except OSError as e:
            logger.warning(f"Heartbeat write failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def main():
    # ── Queues ─────────────────────────────────────────
    signal_queue:    asyncio.Queue = asyncio.Queue(maxsize=100)
    news_queue:      asyncio.Queue = asyncio.Queue(maxsize=200)
    validated_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    # ── Components ─────────────────────────────────────
    market_feed  = MarketDataFeed()
    news_feed    = ForexNewsFeed()
    fix_executor = DualAccountFIXExecutor()
    be_monitor   = BreakEvenMonitor(market_feed=market_feed, fix_executor=fix_executor)
    listener     = TelegramListener(
        signal_queue = signal_queue,
        news_queue   = news_queue,
        market_feed  = market_feed,
        be_monitor   = be_monitor,
    )
    analyzer = MarketAnalyzer(signal_queue, validated_queue, market_feed, news_feed)
    bridge   = ExecutionBridge(validated_queue, fix_executor)

    logger.info("=" * 65)
    logger.info("  GOLD BLUEPRINT TRADING SYSTEM v2.0")
    logger.info("  Python Brain — Full production stack online")
    logger.info("=" * 65)
    logger.info("  Coroutines: MarketData | NewsCalendar | Telegram")
    logger.info("             5-Filter Analyzer | FIX LIVE+DEMO")
    logger.info("             ExecutionBridge | BreakEvenMonitor | Heartbeat")
    logger.info("=" * 65)

    try:
        await asyncio.gather(
            market_feed.run_forever(),            # 1. Live XAUUSD data
            news_feed.run_forever(),              # 2. ForexFactory calendar
            listener.run_with_reconnect(),        # 3. Telegram userbot
            analyzer.process_signals_loop(),      # 4. 5-filter pipeline
            fix_executor.run_forever(),           # 5. FIX LIVE + DEMO
            bridge.relay_loop(),                  # 6. Execution relay
            be_monitor.run_forever(),             # 7. Break-even monitor
            _heartbeat_loop(),                    # 8. Watchdog liveness
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await listener.stop()
        try:
            HEARTBEAT_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        stats = bridge.stats()
        logger.info(f"Session stats: {stats}")
        logger.info("Gold Blueprint Brain — shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
