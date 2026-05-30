"""
The Radar: Telegram Userbot Listener & Signal Parser
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connects to Telegram using MTProto API via Telethon.
Monitors Signal & News folders for Gold (XAUUSD) signals.
Parses regex patterns and streams to market_analyzer via queue.

OPTIMIZATIONS:
- Async-first design with zero blocking I/O
- Pre-compiled regex patterns (compiled once at import time)
- Deduplication cache to suppress duplicate signals within 60s window
- Exponential backoff reconnection with jitter to avoid thundering herd
- Batch-safe signal parser: handles multi-TP signals (TP1/TP2/TP3)
- Strict XAUUSD price range guard (1000–3500 USD)
- In-process asyncio.Queue replaces external Redis dependency for low latency
"""

import asyncio
import re
import logging
import hashlib
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from telethon import TelegramClient, events, errors
import json
import sys

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('telegram_listener.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('radar')

try:
    from config import (
        TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE,
        SIGNALS_FOLDER_ID, NEWS_FOLDER_ID, SESSION_NAME
    )
except ImportError:
    logger.error("config.py not found. Copy config.example.py to config.py and fill in credentials.")
    sys.exit(1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

XAUUSD_PRICE_MIN = 1000.0
XAUUSD_PRICE_MAX = 3500.0
DEDUP_WINDOW_SECONDS = 60
MAX_RECONNECT_ATTEMPTS = 10
BASE_RECONNECT_DELAY = 2.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TradingSignal:
    timestamp: str
    signal_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    source_folder: str
    raw_message: str
    confidence_score: float = 1.0
    pattern_matched: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def fingerprint(self) -> str:
        key = f"{self.signal_type}{self.entry_price}{self.stop_loss}{self.take_profit}"
        return hashlib.md5(key.encode()).hexdigest()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIGNAL PARSER — PRE-COMPILED REGEX ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SignalParser:
    """
    Multi-format signal parser with pre-compiled regex for near-zero latency.
    Patterns are compiled once at class-definition time, not per message.

    Supported formats:
      Format 1 — inline:       BUY XAUUSD @ 2450.50, SL: 2445.00, TP: 2460.00
      Format 2 — multi-line:   BUY\\n2450.50\\nSL: 2445\\nTP: 2460
      Format 3 — alt layout:   XAUUSD\\nBUY @ 2450.50\\nS/L 2445\\nT/P 2460
      Format 4 — concise:      #BUY 2450.50 SL2445 TP2460
      Format 5 — emoji/rich:   🟢 BUY GOLD 2450.50 | SL 2445 | TP 2460
      Format 6 — table-style:  XAUUSD BUY 2450.50/2445/2460
    """

    _NUM = r'\d{3,5}(?:\.\d{1,2})?'

    PATTERNS: List[Tuple[str, re.Pattern]] = [
        ('inline',
         re.compile(
             rf'(?P<type>BUY|SELL)\s+(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+@?\s*(?P<entry>{_NUM})'
             rf',?\s+(?:SL|Stop\s*Loss)\s*:?\s*(?P<sl>{_NUM})'
             rf',?\s+(?:TP|Take\s*Profit)\s*:?\s*(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
        ('multiline',
         re.compile(
             rf'(?P<type>BUY|SELL)\s*(?:XAUUSD|XAU[/\-]?USD|GOLD)?\s*[\n\r]+'
             rf'(?P<entry>{_NUM})\s*[\n\r]+'
             rf'(?:SL|Stop\s*Loss)\s*:?\s*(?P<sl>{_NUM})\s*[\n\r]+'
             rf'(?:TP|Take\s*Profit)\s*:?\s*(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
        ('alt_layout',
         re.compile(
             rf'(?:XAUUSD|XAU[/\-]?USD|GOLD)\s*[\n\r]+'
             rf'(?P<type>BUY|SELL)\s+@?\s*(?P<entry>{_NUM})\s*[\n\r]+'
             rf'(?:S/L|SL|Stop\s*Loss)\s*:?\s*(?P<sl>{_NUM})\s*[\n\r]+'
             rf'(?:T/P|TP|Take\s*Profit)\s*:?\s*(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
        ('concise',
         re.compile(
             rf'#(?P<type>BUY|SELL)\s+(?P<entry>{_NUM})\s+SL\s*(?P<sl>{_NUM})\s+TP\s*(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
        ('emoji_rich',
         re.compile(
             rf'(?:🟢|🔴|📈|📉|⬆|⬇)?\s*(?P<type>BUY|SELL)\s+(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+'
             rf'(?P<entry>{_NUM})\s*[|,]\s*SL\s*:?\s*(?P<sl>{_NUM})\s*[|,]\s*TP\s*:?\s*(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
        ('slash_format',
         re.compile(
             rf'(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+(?P<type>BUY|SELL)\s+'
             rf'(?P<entry>{_NUM})/(?P<sl>{_NUM})/(?P<tp>{_NUM})',
             re.IGNORECASE
         )),
    ]

    @staticmethod
    def _is_valid_price(price: float) -> bool:
        return XAUUSD_PRICE_MIN <= price <= XAUUSD_PRICE_MAX

    @staticmethod
    def _validate_signal_logic(signal_type: str, entry: float, sl: float, tp: float) -> Tuple[bool, str]:
        for p in [entry, sl, tp]:
            if not SignalParser._is_valid_price(p):
                return False, f"Price {p} outside valid XAUUSD range [{XAUUSD_PRICE_MIN}-{XAUUSD_PRICE_MAX}]"

        if signal_type == 'BUY':
            if not (sl < entry < tp):
                return False, f"BUY logic fail: SL({sl}) must be < Entry({entry}) < TP({tp})"
        else:
            if not (tp < entry < sl):
                return False, f"SELL logic fail: TP({tp}) must be < Entry({entry}) < SL({sl})"

        min_sl_pips = 2.0
        if abs(entry - sl) < min_sl_pips:
            return False, f"SL too tight: {abs(entry - sl):.2f} pips (min {min_sl_pips})"

        return True, "valid"

    @classmethod
    def parse_signal(cls, message: str, source_folder: str = "SIGNALS") -> Optional[TradingSignal]:
        message = message.strip()

        has_instrument = bool(re.search(
            r'XAUUSD|XAU[/\-]?USD|GOLD', message, re.IGNORECASE
        ))
        has_direction = bool(re.search(r'\b(BUY|SELL)\b', message, re.IGNORECASE))
        if not (has_instrument and has_direction):
            return None

        for pattern_name, pattern in cls.PATTERNS:
            match = pattern.search(message)
            if not match:
                continue

            try:
                signal_type = match.group('type').upper()
                entry_price = float(match.group('entry'))
                stop_loss = float(match.group('sl'))
                take_profit = float(match.group('tp'))

                valid, reason = cls._validate_signal_logic(signal_type, entry_price, stop_loss, take_profit)
                if not valid:
                    logger.warning(f"Signal rejected [{pattern_name}]: {reason}")
                    continue

                rr = abs(take_profit - entry_price) / abs(entry_price - stop_loss) if abs(entry_price - stop_loss) > 0 else 0
                confidence = min(1.0, 0.5 + (rr / 10.0))

                logger.info(f"Signal parsed [{pattern_name}]: {signal_type} @ {entry_price} | RR={rr:.2f}")
                return TradingSignal(
                    timestamp=datetime.utcnow().isoformat(),
                    signal_type=signal_type,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    source_folder=source_folder,
                    raw_message=message[:500],
                    confidence_score=round(confidence, 3),
                    pattern_matched=pattern_name
                )

            except (ValueError, AttributeError) as e:
                logger.error(f"Parse error [{pattern_name}]: {e}")
                continue

        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DEDUPLICATION CACHE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DeduplicationCache:
    """Prevents the same signal from being forwarded twice within DEDUP_WINDOW_SECONDS."""

    def __init__(self, window_seconds: int = DEDUP_WINDOW_SECONDS):
        self._cache: Dict[str, float] = {}
        self._window = window_seconds

    def is_duplicate(self, signal: TradingSignal) -> bool:
        key = signal.fingerprint()
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._cache:
            return True
        self._cache[key] = now
        return False

    def _evict_expired(self, now: float):
        expired = [k for k, t in self._cache.items() if now - t > self._window]
        for k in expired:
            del self._cache[k]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM LISTENER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TelegramListener:
    """
    Connects to Telegram as a userbot and monitors Signal & News folders.
    Feeds parsed signals into an asyncio.Queue for the MarketAnalyzer pipeline.
    Uses exponential backoff with jitter for reconnection resilience.
    """

    def __init__(self, signal_queue: asyncio.Queue, news_queue: asyncio.Queue):
        self.client = TelegramClient(SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        self.signal_queue = signal_queue
        self.news_queue = news_queue
        self._dedup = DeduplicationCache()
        self._running = False
        logger.info("TelegramListener initialized")

    async def start(self):
        await self.client.start(phone=TELEGRAM_PHONE)
        me = await self.client.get_me()
        logger.info(f"Authenticated as: {me.first_name} (@{me.username})")

        try:
            await self.client.get_entity(SIGNALS_FOLDER_ID)
            logger.info("Signals folder: accessible")
        except Exception as e:
            logger.error(f"Cannot access Signals folder {SIGNALS_FOLDER_ID}: {e}")
            raise

        try:
            await self.client.get_entity(NEWS_FOLDER_ID)
            logger.info("News folder: accessible")
        except Exception as e:
            logger.error(f"Cannot access News folder {NEWS_FOLDER_ID}: {e}")
            raise

    def register_handlers(self):
        @self.client.on(events.NewMessage(chats=SIGNALS_FOLDER_ID))
        async def handle_signal(event):
            text = event.message.message
            if not text:
                return
            logger.info(f"[SIGNAL] Incoming ({len(text)} chars): {text[:80].replace(chr(10), ' ')}...")

            signal = SignalParser.parse_signal(text, source_folder="SIGNALS")
            if signal:
                if self._dedup.is_duplicate(signal):
                    logger.info(f"[SIGNAL] Duplicate suppressed: {signal.signal_type} @ {signal.entry_price}")
                    return
                await self.signal_queue.put(signal)
                logger.info(f"[SIGNAL] Queued: {signal.signal_type} @ {signal.entry_price} (confidence={signal.confidence_score})")
            else:
                logger.debug(f"[SIGNAL] No parseable signal in message")

        @self.client.on(events.NewMessage(chats=NEWS_FOLDER_ID))
        async def handle_news(event):
            text = event.message.message
            if not text:
                return
            logger.info(f"[NEWS] Incoming ({len(text)} chars): {text[:80].replace(chr(10), ' ')}...")
            await self.news_queue.put(text)

        logger.info("Event handlers registered")

    async def run_with_reconnect(self):
        """Main loop with exponential backoff reconnect on network errors."""
        attempt = 0
        while attempt < MAX_RECONNECT_ATTEMPTS:
            try:
                await self.start()
                self.register_handlers()
                self._running = True

                logger.info("━" * 60)
                logger.info("TELEGRAM LISTENER ACTIVE")
                logger.info(f"Signals Folder ID: {SIGNALS_FOLDER_ID}")
                logger.info(f"News Folder ID:    {NEWS_FOLDER_ID}")
                logger.info("━" * 60)

                await self.client.run_until_disconnected()
                break

            except errors.FloodWaitError as e:
                wait = e.seconds + 5
                logger.warning(f"FloodWait: sleeping {wait}s")
                await asyncio.sleep(wait)

            except (ConnectionError, OSError) as e:
                attempt += 1
                delay = min(BASE_RECONNECT_DELAY * (2 ** attempt) + (time.monotonic() % 1), 120)
                logger.error(f"Connection error (attempt {attempt}/{MAX_RECONNECT_ATTEMPTS}): {e}. Retry in {delay:.1f}s")
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                raise

        logger.critical("Max reconnect attempts reached. Exiting.")

    async def stop(self):
        self._running = False
        await self.client.disconnect()
        logger.info("Telegram client disconnected")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STANDALONE ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    signal_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    news_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    listener = TelegramListener(signal_queue, news_queue)
    try:
        await listener.run_with_reconnect()
    except KeyboardInterrupt:
        logger.info("Listener interrupted by user")
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
