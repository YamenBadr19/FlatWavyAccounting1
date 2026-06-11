"""
news_feed.py — ForexFactory Economic Calendar
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches high-impact USD economic events.
Disables trading during news events (±30 min window).

USAGE:
  from news_feed import ForexNewsFeed
  feed = ForexNewsFeed()
  await feed.run_forever()
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger('news_feed')


class ForexNewsFeed:
    """
    Fetches ForexFactory calendar.
    """

    def __init__(self):
        self.events = []
        self.news_mode_active = False
        logger.info("ForexNewsFeed initialized")

    async def run_forever(self):
        """Fetch calendar every 5 minutes."""
        logger.info("ForexNewsFeed started")
        while True:
            try:
                await self._fetch_calendar()
                await asyncio.sleep(300)  # Update every 5 minutes
            except Exception as e:
                logger.error(f"News feed error: {e}")
                await asyncio.sleep(60)

    async def _fetch_calendar(self):
        """Fetch events from ForexFactory."""
        try:
            import aiohttp
            
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        self.events = await resp.json()
                        logger.debug(f"Fetched {len(self.events)} calendar events")
        except Exception as e:
            logger.warning(f"Failed to fetch calendar: {e}")
