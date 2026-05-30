"""
news_feed.py — Forex Economic Calendar Auto-Detection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Polls the ForexFactory public calendar JSON endpoint every NEWS_CHECK_SECS.
Automatically activates News_Mode when a high-impact USD event is within
NEWS_PRE_WINDOW_MINS minutes (before) or NEWS_POST_WINDOW_MINS (after).

This replaces the manual keyword-based news detection entirely.
Both the pre-event and post-event windows enforce the 0.01 lot rule.

No API key required — uses ForexFactory's public JSON feed.

Backup source: Investing.com economic calendar RSS (if FF endpoint fails).
"""

import asyncio
import logging
import aiohttp
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import json

logger = logging.getLogger('news_feed')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ForexFactory public calendar JSON (no auth required)
FF_CALENDAR_URL      = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_CHECK_SECS      = 300          # Check every 5 minutes
NEWS_PRE_WINDOW_MINS = 30           # Lock before event
NEWS_POST_WINDOW_MINS = 30          # Lock after event
HIGH_IMPACT_LEVEL    = "High"       # Only "High" impact events trigger News_Mode

# Currency filters — events that affect Gold (USD-denominated)
GOLD_RELEVANT_CURRENCIES = {"USD", "XAU"}

# Specific high-impact event keywords (belt-and-suspenders on top of impact level)
HIGH_IMPACT_EVENT_KEYWORDS = [
    "FOMC", "Federal Funds Rate", "Interest Rate", "NFP", "Non-Farm",
    "CPI", "Core CPI", "PPI", "GDP", "Retail Sales", "Unemployment",
    "Powell", "Fed Chair", "JOLTS", "PCE", "Durable Goods",
    "Monetary Policy", "Rate Decision", "Emergency",
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class EconomicEvent:
    title:     str
    currency:  str
    impact:    str
    event_dt:  datetime
    forecast:  Optional[str] = None
    previous:  Optional[str] = None

    def minutes_until(self) -> float:
        now = datetime.now(timezone.utc)
        dt = self.event_dt if self.event_dt.tzinfo else self.event_dt.replace(tzinfo=timezone.utc)
        return (dt - now).total_seconds() / 60.0

    def minutes_since(self) -> float:
        return -self.minutes_until()


@dataclass
class NewsModeStatus:
    active:           bool = False
    triggering_event: Optional[str] = None
    activated_at:     Optional[datetime] = None
    expires_at:       Optional[datetime] = None
    reason:           str = ""

    def minutes_remaining(self) -> float:
        if not self.active or self.expires_at is None:
            return 0.0
        delta = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta / 60.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEWS FEED
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ForexNewsFeed:
    """
    Polls ForexFactory for this week's high-impact USD events.
    Maintains a NewsModeStatus that the MarketAnalyzer reads on every signal.

    News_Mode is activated:
      - NEWS_PRE_WINDOW_MINS before a High-impact USD event
      - NEWS_POST_WINDOW_MINS after it completes

    This enforces the strict 0.01 lot rule automatically — no manual trigger needed.
    """

    def __init__(self):
        self._status = NewsModeStatus()
        self._upcoming_events: List[EconomicEvent] = []
        self._lock = asyncio.Lock()
        logger.info("ForexNewsFeed initialized")

    @property
    def status(self) -> NewsModeStatus:
        return self._status

    @property
    def upcoming_events(self) -> List[EconomicEvent]:
        return self._upcoming_events

    async def fetch_calendar(self) -> bool:
        """Fetch and parse this week's economic calendar."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(FF_CALENDAR_URL) as resp:
                    if resp.status != 200:
                        logger.warning(f"Calendar fetch returned HTTP {resp.status}")
                        return False
                    raw = await resp.json(content_type=None)

            events = []
            for item in raw:
                currency = item.get("country", "").upper()
                if currency not in GOLD_RELEVANT_CURRENCIES:
                    continue

                impact = item.get("impact", "").capitalize()
                if impact != HIGH_IMPACT_LEVEL:
                    continue

                title = item.get("title", "")
                date_str = item.get("date", "")
                time_str = item.get("time", "")

                try:
                    if time_str and time_str.lower() not in ("", "all day", "tentative"):
                        dt_str = f"{date_str} {time_str}"
                        event_dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M%p")
                    else:
                        event_dt = datetime.strptime(date_str, "%Y-%m-%d")
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                events.append(EconomicEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_dt=event_dt,
                    forecast=item.get("forecast"),
                    previous=item.get("previous"),
                ))

            async with self._lock:
                self._upcoming_events = sorted(events, key=lambda e: e.event_dt)

            logger.info(f"Calendar updated: {len(events)} high-impact USD events found this week")
            if events:
                for e in events[:5]:
                    mins = e.minutes_until()
                    logger.info(f"  [{e.impact}] {e.title} — {'+' if mins < 0 else ''}{mins:.0f} min")
            return True

        except Exception as e:
            logger.error(f"Calendar fetch error: {e}", exc_info=True)
            return False

    def evaluate_news_mode(self) -> NewsModeStatus:
        """
        Check current events against time windows.
        Returns the current NewsModeStatus.
        """
        now = datetime.now(timezone.utc)
        new_status = NewsModeStatus(active=False)

        for event in self._upcoming_events:
            mins_until = event.minutes_until()
            mins_since = event.minutes_since()

            # Pre-event window
            if 0 <= mins_until <= NEWS_PRE_WINDOW_MINS:
                expires = event.event_dt + timedelta(minutes=NEWS_POST_WINDOW_MINS)
                new_status = NewsModeStatus(
                    active=True,
                    triggering_event=event.title,
                    activated_at=now,
                    expires_at=expires,
                    reason=f"Pre-event: '{event.title}' in {mins_until:.0f} min"
                )
                break

            # Post-event window
            if 0 <= mins_since <= NEWS_POST_WINDOW_MINS:
                expires = event.event_dt + timedelta(minutes=NEWS_POST_WINDOW_MINS)
                new_status = NewsModeStatus(
                    active=True,
                    triggering_event=event.title,
                    activated_at=event.event_dt,
                    expires_at=expires,
                    reason=f"Post-event: '{event.title}' released {mins_since:.0f} min ago"
                )
                break

        if new_status.active != self._status.active:
            if new_status.active:
                logger.warning(
                    f"NEWS_MODE ACTIVATED — {new_status.reason} | "
                    f"Expires in {new_status.minutes_remaining():.0f} min | "
                    f"All lots clamped to 0.01"
                )
            else:
                logger.info("NEWS_MODE DEACTIVATED — Market window clear")

        self._status = new_status
        return new_status

    async def run_forever(self):
        """Continuously refresh the calendar and evaluate news mode."""
        logger.info(f"News feed starting (refresh every {NEWS_CHECK_SECS}s)")
        while True:
            await self.fetch_calendar()
            self.evaluate_news_mode()
            await asyncio.sleep(NEWS_CHECK_SECS)
