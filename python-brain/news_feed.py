"""
news_feed.py — ForexFactory Economic Calendar Auto-Detection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Polls ForexFactory's public JSON endpoint every 5 minutes.
Automatically activates News_Mode (0.01 lot cap) when a High-impact
USD event is within ±30 minutes.

No API key required.
"""

import asyncio
import logging
import aiohttp
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

logger = logging.getLogger('news_feed')

FF_CALENDAR_URL       = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_CHECK_SECS       = 300
NEWS_PRE_WINDOW_MINS  = 30
NEWS_POST_WINDOW_MINS = 30
HIGH_IMPACT_LEVEL     = "High"
GOLD_CURRENCIES       = {"USD", "XAU"}


@dataclass
class EconomicEvent:
    title:    str
    currency: str
    impact:   str
    event_dt: datetime

    def minutes_until(self) -> float:
        now = datetime.now(timezone.utc)
        dt  = self.event_dt if self.event_dt.tzinfo else self.event_dt.replace(tzinfo=timezone.utc)
        return (dt - now).total_seconds() / 60.0

    def minutes_since(self) -> float:
        return -self.minutes_until()


@dataclass
class NewsModeStatus:
    active:           bool             = False
    triggering_event: Optional[str]    = None
    activated_at:     Optional[datetime] = None
    expires_at:       Optional[datetime] = None
    reason:           str              = ""

    def minutes_remaining(self) -> float:
        if not self.active or not self.expires_at:
            return 0.0
        return max(0.0, (self.expires_at - datetime.now(timezone.utc)).total_seconds() / 60.0)


class ForexNewsFeed:
    def __init__(self):
        self._status: NewsModeStatus     = NewsModeStatus()
        self._events: List[EconomicEvent] = []
        logger.info("ForexNewsFeed initialized")

    @property
    def status(self) -> NewsModeStatus:
        return self._status

    async def fetch_calendar(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(FF_CALENDAR_URL) as resp:
                    if resp.status != 200:
                        return False
                    raw = await resp.json(content_type=None)

            events = []
            for item in raw:
                if item.get("country", "").upper() not in GOLD_CURRENCIES:
                    continue
                if item.get("impact", "").capitalize() != HIGH_IMPACT_LEVEL:
                    continue
                date_str = item.get("date", "")
                time_str = item.get("time", "")
                try:
                    if time_str and time_str.lower() not in ("", "all day", "tentative"):
                        event_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M%p")
                    else:
                        event_dt = datetime.strptime(date_str, "%Y-%m-%d")
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                events.append(EconomicEvent(
                    title=item.get("title", ""),
                    currency=item.get("country", "").upper(),
                    impact=HIGH_IMPACT_LEVEL,
                    event_dt=event_dt,
                ))

            self._events = sorted(events, key=lambda e: e.event_dt)
            logger.info(f"Calendar: {len(events)} high-impact USD events this week")
            return True
        except Exception as e:
            logger.error(f"Calendar error: {e}")
            return False

    def evaluate_news_mode(self) -> NewsModeStatus:
        now    = datetime.now(timezone.utc)
        status = NewsModeStatus()

        for ev in self._events:
            mu = ev.minutes_until()
            ms = ev.minutes_since()
            if 0 <= mu <= NEWS_PRE_WINDOW_MINS:
                expires = ev.event_dt + timedelta(minutes=NEWS_POST_WINDOW_MINS)
                status  = NewsModeStatus(True, ev.title, now, expires,
                                         f"Pre-event: '{ev.title}' in {mu:.0f} min")
                break
            if 0 <= ms <= NEWS_POST_WINDOW_MINS:
                expires = ev.event_dt + timedelta(minutes=NEWS_POST_WINDOW_MINS)
                status  = NewsModeStatus(True, ev.title, ev.event_dt, expires,
                                         f"Post-event: '{ev.title}' {ms:.0f} min ago")
                break

        if status.active != self._status.active:
            if status.active:
                logger.warning(f"NEWS_MODE ON — {status.reason} | Lots clamped to 0.01")
            else:
                logger.info("NEWS_MODE OFF — Market clear")

        self._status = status
        return status

    async def run_forever(self):
        logger.info(f"News feed starting (refresh every {NEWS_CHECK_SECS}s)")
        while True:
            await self.fetch_calendar()
            self.evaluate_news_mode()
            await asyncio.sleep(NEWS_CHECK_SECS)
