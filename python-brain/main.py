"""
Gold Blueprint Trading System — Python Brain Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Boots ALL coroutines in one asyncio.gather() call:

  1. MarketDataFeed      — live XAUUSD data via yfinance (every 60s)
  2. ForexNewsFeed       — ForexFactory calendar auto-detection (every 5min)
  3. TelegramListener    — scrapes Signals & News folders (real-time)
  4. MarketAnalyzer      — 5-filter validation pipeline (live data)
  5. DualAccountFIXExecutor — maintains Live + Demo FIX sessions
  6. ExecutionBridge     — relays validated signals to both FIX sessions

Signal flow:
  Telegram → signal_queue → MarketAnalyzer (5 filters + lot sizing)
           → validated_queue → ExecutionBridge (TTL check + audit)
           → DualAccountFIXExecutor.execute_signal()
           → asyncio.gather(LIVE FIX, DEMO FIX)  ← simultaneous
"""

import asyncio
import logging
import sys
import os

# Configure logging first
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)-15s: %(message)s',
    handlers=[
        logging.FileHandler('gold_blueprint.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('main')

from market_data_feed  import MarketDataFeed
from news_feed         import ForexNewsFeed
from telegram_listener import TelegramListener
from market_analyzer   import MarketAnalyzer
from fix_executor      import DualAccountFIXExecutor
from signal_queue      import ExecutionBridge


async def main():
    # ── Shared queues ──────────────────────────────────
    signal_queue:    asyncio.Queue = asyncio.Queue(maxsize=100)
    news_queue:      asyncio.Queue = asyncio.Queue(maxsize=200)
    validated_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    # ── Component instantiation ────────────────────────
    market_feed  = MarketDataFeed()
    news_feed    = ForexNewsFeed()
    listener     = TelegramListener(signal_queue, news_queue)
    analyzer     = MarketAnalyzer(signal_queue, validated_queue, market_feed, news_feed)
    fix_executor = DualAccountFIXExecutor()
    bridge       = ExecutionBridge(validated_queue, fix_executor)

    logger.info("=" * 65)
    logger.info("  GOLD BLUEPRINT TRADING SYSTEM — FULL STACK ONLINE")
    logger.info("  Brain (Python) + Body (FIX API) + Live Data + Calendar")
    logger.info("=" * 65)

    # ── Boot all coroutines simultaneously ─────────────
    try:
        await asyncio.gather(
            market_feed.run_forever(),          # Live XAUUSD data
            news_feed.run_forever(),            # ForexFactory calendar
            listener.run_with_reconnect(),      # Telegram userbot
            analyzer.process_signals_loop(),    # 5-filter pipeline
            fix_executor.run_forever(),         # LIVE + DEMO FIX sessions
            bridge.relay_loop(),                # Execution relay
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await listener.stop()
        stats = bridge.stats()
        logger.info(f"Session stats: {stats}")
        logger.info("Gold Blueprint Brain — shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
