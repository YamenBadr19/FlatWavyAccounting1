"""
Gold Blueprint Trading System — Python Brain
Full Stack Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Signal flow:
  Telegram → [signal_queue] → MarketAnalyzer (5 filters + dynamic lot size)
           → [validated_queue] → ExecutionBridge (TTL check + audit)
           → MCPExecutor.execute_signal()
           → cTrader Local MCP Server (http://127.0.0.1:9876/mcp/)

Coroutines running in parallel (11 total):
  1.  MarketDataFeed.run_forever()          yfinance Gold data, 60s
  2.  ForexNewsFeed.run_forever()           ForexFactory calendar, 5 min
  3.  TelegramListener.run_with_reconnect() Telegram userbot
  4.  MarketAnalyzer.run()                  5-filter pipeline
  5.  MCPExecutor.run_forever()             MCP keepalive + health check
  6.  ExecutionBridge.relay_loop()          Execution relay + audit
  7.  BreakEvenMonitor.run_forever()        Tier1/2 SL + trailing stop
  8.  BalanceManager.run_forever()          MCP balance polling (30s)
  9.  ControlBot.run_forever()              Telegram control commands
  10. _heartbeat_loop()                     Watchdog liveness signal
  11. _market_data_sync_loop()              Market data → analyzer (5s)
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
from fix_executor      import MCPExecutor
from signal_queue      import ExecutionBridge
from balance_manager   import BalanceManager
from channel_reporter  import ChannelReporter
from control_bot       import ControlBot

# Heartbeat file — watchdog checks this to detect hung brain
HEARTBEAT_FILE     = Path("/tmp/gold_blueprint_heartbeat")
HEARTBEAT_INTERVAL = 30   # seconds


async def _market_data_sync_loop(market_feed, analyzer):
    """
    Every 5 seconds, push the latest MarketDataFeed snapshot into
    MarketAnalyzer so its filters have current price, RSI, ATR, and pivots.
    """
    while True:
        snap = market_feed.snapshot
        analyzer.update_market_data({
            'high':          snap.prev_high,
            'low':           snap.prev_low,
            'close':         snap.prev_close,
            'current_price': snap.current_price,
            'rsi_14':        snap.rsi_14,
            'atr_14':        snap.atr_14,
            'ema_50':        snap.ema_50,
            'close_history': snap.close_history,
        })
        await asyncio.sleep(5)


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

    # ── Core components ────────────────────────────────
    market_feed     = MarketDataFeed()
    news_feed       = ForexNewsFeed()
    balance_manager = BalanceManager()
    channel_reporter = ChannelReporter()

    mcp_executor = MCPExecutor(
        balance_manager  = balance_manager,
        channel_reporter = channel_reporter,
    )

    be_monitor = BreakEvenMonitor(
        market_feed      = market_feed,
        fix_executor     = mcp_executor,
        channel_reporter = channel_reporter,
    )

    listener = TelegramListener(
        signal_queue = signal_queue,
        news_queue   = news_queue,
        market_feed  = market_feed,
        be_monitor   = be_monitor,
    )

    # Wire the shared Telegram client into channel reporter
    channel_reporter.set_client(listener.client)

    analyzer = MarketAnalyzer(
        signal_queue    = signal_queue,
        news_queue      = news_queue,
        validated_queue = validated_queue,
        balance_manager = balance_manager,
    )

    bridge = ExecutionBridge(
        validated_queue  = validated_queue,
        fix_executor     = mcp_executor,
        channel_reporter = channel_reporter,
    )

    control_bot = ControlBot(
        balance_manager = balance_manager,
        fix_executor    = mcp_executor,
        news_feed       = news_feed,
        market_feed     = market_feed,
    )

    logger.info("=" * 65)
    logger.info("  GOLD BLUEPRINT TRADING SYSTEM v3.0")
    logger.info("  Python Brain — Fully Autonomous Production Stack")
    logger.info("=" * 65)
    logger.info("  Coroutines: MarketData | NewsCalendar | Telegram")
    logger.info("             5-Filter Analyzer | MCP Executor")
    logger.info("             ExecutionBridge | Tier1/2+Trail SL Mgr")
    logger.info("             BalanceManager (MCP) | ControlBot")
    logger.info("             ChannelReporter | Heartbeat")
    logger.info(f"  Broker:    cTrader MCP @ http://127.0.0.1:9876/mcp/")
    logger.info("=" * 65)

    try:
        await asyncio.gather(
            market_feed.run_forever(),                       # 1.  Live XAUUSD data
            news_feed.run_forever(),                         # 2.  ForexFactory calendar
            listener.run_with_reconnect(),                   # 3.  Telegram userbot
            analyzer.run(),                                  # 4.  5-filter pipeline
            mcp_executor.run_forever(),                      # 5.  MCP keepalive
            bridge.relay_loop(),                             # 6.  Execution relay
            be_monitor.run_forever(),                        # 7.  Tier1/2 + trailing SL
            balance_manager.run_forever(),                   # 8.  MCP balance polling
            control_bot.run_forever(),                       # 9.  Telegram control bot
            _heartbeat_loop(),                               # 10. Watchdog liveness
            _market_data_sync_loop(market_feed, analyzer),  # 11. Market data → analyzer
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
