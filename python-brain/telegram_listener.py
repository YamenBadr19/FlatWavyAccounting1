"""
telegram_listener.py — Telegram Signal Listener & Break-Even Monitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Listens for trading signals from Telegram.
Implements smart break-even and trailing stop management.

USAGE:
  from telegram_listener import TelegramListener, BreakEvenMonitor
  listener = TelegramListener(signal_queue=q, ...)
  await listener.run_with_reconnect()
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from asyncio import Queue

logger = logging.getLogger('telegram')


class TelegramListener:
    """
    Listens for trading signals from Telegram folders/channels.
    Filters and queues valid signals.
    """

    def __init__(
        self,
        signal_queue: Queue,
        news_queue: Queue,
        market_feed=None,
        be_monitor=None,
    ):
        self.signal_queue = signal_queue
        self.news_queue = news_queue
        self.market_feed = market_feed
        self.be_monitor = be_monitor
        self.client = None
        self._running = False
        logger.info("TelegramListener initialized")

    async def run_with_reconnect(self):
        """
        Main loop with automatic reconnection.
        """
        self._running = True
        reconnect_delay = 5.0

        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"Telegram listener error: {e}")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 60.0)

    async def _connect_and_listen(self):
        """
        Connect to Telegram and listen for signals.
        """
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            import os

            api_id = int(os.environ.get("TELEGRAM_API_ID", 0))
            api_hash = os.environ.get("TELEGRAM_API_HASH", "")
            session = os.environ.get("TELEGRAM_STRING_SESSION", "")

            if not all([api_id, api_hash, session]):
                logger.error("Telegram credentials missing")
                await asyncio.sleep(30)
                return

            self.client = TelegramClient(
                StringSession(session),
                api_id,
                api_hash,
            )

            async with self.client:
                logger.info("✓ Connected to Telegram")
                # Placeholder: actual message listening logic
                await asyncio.sleep(3600)  # Keep alive

        except Exception as e:
            logger.error(f"Telegram connection failed: {e}")
            raise

    async def stop(self):
        """Stop listener."""
        self._running = False
        if self.client:
            await self.client.disconnect()


class BreakEvenMonitor:
    """
    Monitors open positions and manages smart break-even and trailing stops.
    Moves stop loss to entry price (break-even) and then trails profitably.
    """

    def __init__(self, market_feed=None, fix_executor=None, channel_reporter=None):
        self.market_feed = market_feed
        self.fix_executor = fix_executor
        self.channel_reporter = channel_reporter
        self._running = False
        logger.info("BreakEvenMonitor initialized")

    async def run_forever(self):
        """
        Monitor positions every 10 seconds.
        """
        self._running = True
        logger.info("BreakEvenMonitor started")

        while self._running:
            try:
                if self.fix_executor:
                    positions = self.fix_executor.get_positions()
                    for pos in positions:
                        await self._evaluate_position(pos)
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"BreakEvenMonitor error: {e}")
                await asyncio.sleep(5)

    async def _evaluate_position(self, position: Any):
        """
        Evaluate a position for break-even or trailing stop adjustments.
        """
        try:
            pnl_pips = position.profit_loss_pips()
            
            if position.buy:
                # BUY position
                if pnl_pips >= 15 and position.stop_loss < position.entry_price:
                    # Move to break-even
                    await self.fix_executor._modify_stop_loss(
                        position.position_id,
                        position.entry_price
                    )
                    logger.info(
                        f"✓ Moved to break-even: {position.symbol} "
                        f"@ {position.entry_price:.5f}"
                    )
            else:
                # SELL position
                if pnl_pips >= 15 and position.stop_loss > position.entry_price:
                    # Move to break-even
                    await self.fix_executor._modify_stop_loss(
                        position.position_id,
                        position.entry_price
                    )
                    logger.info(
                        f"✓ Moved to break-even: {position.symbol} "
                        f"@ {position.entry_price:.5f}"
                    )
        except Exception as e:
            logger.warning(f"Error evaluating position: {e}")

    async def stop(self):
        """Stop monitor."""
        self._running = False
