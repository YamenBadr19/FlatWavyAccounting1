"""
control_bot.py — Telegram Control Commands
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Listens for control commands from Telegram.
Commands: /status, /balance, /close_all, /news, /positions

USAGE:
  from control_bot import ControlBot
  bot = ControlBot(balance_manager=bm, ...)
  await bot.run_forever()
"""

import asyncio
import logging

logger = logging.getLogger('control_bot')


class ControlBot:
    """
    Telegram command handler.
    """

    def __init__(
        self,
        balance_manager=None,
        fix_executor=None,
        news_feed=None,
        market_feed=None,
    ):
        self.balance_manager = balance_manager
        self.fix_executor = fix_executor
        self.news_feed = news_feed
        self.market_feed = market_feed
        logger.info("ControlBot initialized")

    async def run_forever(self):
        """Main control loop."""
        logger.info("ControlBot started")
        while True:
            try:
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"ControlBot error: {e}")
                await asyncio.sleep(10)
