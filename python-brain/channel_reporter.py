"""
channel_reporter.py — Telegram Channel Reporting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sends real-time trading updates to Telegram.

USAGE:
  from channel_reporter import ChannelReporter
  reporter = ChannelReporter()
  reporter.set_client(telegram_client)
  await reporter.report_position_opened(position)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger('reporter')


class ChannelReporter:
    """
    Reports trading events to Telegram.
    """

    def __init__(self):
        self.client = None
        self.channel_id = -1001234567890  # Placeholder
        logger.info("ChannelReporter initialized")

    def set_client(self, client):
        """Set Telegram client."""
        self.client = client

    async def report_position_opened(self, position):
        """Report position opened."""
        try:
            msg = (
                f"🟢 POSITION OPENED\n"
                f"Symbol: {position.symbol}\n"
                f"Type: {'BUY' if position.buy else 'SELL'}\n"
                f"Entry: ${position.entry_price:.5f}\n"
                f"Volume: {position.volume}L\n"
                f"SL: ${position.stop_loss:.5f}\n"
                f"TP: ${position.take_profit:.5f}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
            )
            if self.client:
                await self.client.send_message(self.channel_id, msg)
            logger.info(f"✓ Reported position open")
        except Exception as e:
            logger.error(f"Report failed: {e}")

    async def report_position_closed(self, position, close_price: float, pnl: float):
        """Report position closed."""
        try:
            msg = (
                f"{'🟢' if pnl >= 0 else '🔴'} POSITION CLOSED\n"
                f"Symbol: {position.symbol}\n"
                f"Close: ${close_price:.5f}\n"
                f"P&L: ${pnl:+.2f}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
            )
            if self.client:
                await self.client.send_message(self.channel_id, msg)
            logger.info(f"✓ Reported position close")
        except Exception as e:
            logger.error(f"Report failed: {e}")
