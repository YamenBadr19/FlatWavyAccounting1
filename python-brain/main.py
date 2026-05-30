"""
Gold Blueprint Trading System — Python Brain Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Boots all three async coroutines in one event loop:
  1. TelegramListener  — scrapes Signals & News folders
  2. MarketAnalyzer    — applies 3 filters, determines lot size
  3. SignalBridge      — relays validated signals to C# cBot

Start this file to run the entire Python brain.
"""

import asyncio
import logging
import sys
from telegram_listener import TelegramListener
from market_analyzer import MarketAnalyzer
from signal_queue import SignalBridge

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('gold_blueprint.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('main')


async def main():
    signal_queue:    asyncio.Queue = asyncio.Queue(maxsize=100)
    news_queue:      asyncio.Queue = asyncio.Queue(maxsize=200)
    validated_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    listener = TelegramListener(signal_queue, news_queue)
    analyzer = MarketAnalyzer(signal_queue, news_queue, validated_queue)
    bridge   = SignalBridge(validated_queue)

    logger.info("=" * 60)
    logger.info("  GOLD BLUEPRINT TRADING SYSTEM — BRAIN ONLINE")
    logger.info("  Architecture: Brain (Python) + Body (C# cTrader)")
    logger.info("=" * 60)

    try:
        await asyncio.gather(
            listener.run_with_reconnect(),
            analyzer.run(),
            bridge.relay_loop(),
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
        logger.info("Gold Blueprint Brain shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
