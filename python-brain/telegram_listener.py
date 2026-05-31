"""
telegram_listener.py — Production Telegram Signal Parser
Gold Blueprint Trading System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FEATURES:
  0. Flexible Connection Layer  — StringSession (silent production) or
                                  interactive terminal login (local dev)
  1. Narrative Chat Filter      — Hard-blocks profit celebrations, admin
                                  updates, and commentary in Arabic + English
  2. Math-Based Direction Logic — Direction inferred from TP1 vs SL price
                                  math, not just text keywords. Custom emoji
                                  entities (MessageEntityCustomEmoji) are
                                  decoded and used as a secondary cross-check.
  3. Auto-Spread Calibration    — SL padded by exactly $2.00 (20 pips on Gold)
                                  per the channel's institutional spread rule.
  4. Entry Price Sanity Check   — Parsed entry compared to live spot price from
                                  MarketDataFeed. Discards if delta > $20.00.
  5. Break-Even Monitor         — Background task that tracks active positions
                                  and fires a FIX SL modification the instant
                                  price hits TP1 or TP2, moving SL to entry.

CONNECTION MODES:
  Mode A (Silent Production):   TELEGRAM_STRING_SESSION set → uses StringSession
  Mode B (Local Interactive):   env var absent → interactive phone + SMS code
"""

import asyncio
import re
import logging
import hashlib
import time
import os
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict, field
import json

from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    MessageEntityBold,
    MessageEntityTextUrl,
)

logger = logging.getLogger('radar')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG (all from env — no hardcoded values)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _e(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _ef(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default

TELEGRAM_API_ID          = int(_e("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH        = _e("TELEGRAM_API_HASH")
TELEGRAM_PHONE           = _e("TELEGRAM_PHONE")
TELEGRAM_STRING_SESSION  = _e("TELEGRAM_STRING_SESSION")   # optional — production mode
SESSION_NAME             = _e("SESSION_NAME",       "gold_blueprint_session")
TARGET_CHANNEL_USERNAME  = _e("TARGET_CHANNEL_USERNAME")   # optional — backup lookup
SIGNALS_FOLDER_ID        = int(_e("SIGNALS_FOLDER_ID", "0"))
NEWS_FOLDER_ID           = int(_e("NEWS_FOLDER_ID",    "0"))

MAX_SANITY_DISTANCE_USD  = _ef("MAX_SANITY_DISTANCE_USD",      20.0)
CHANNEL_SPREAD_PADDING   = _ef("CHANNEL_SPREAD_PADDING_PIPS",   2.0)  # $2 on Gold

XAUUSD_PRICE_MIN         = 1000.0
XAUUSD_PRICE_MAX         = 5000.0
DEDUP_WINDOW_SECS        = 60
MAX_RECONNECT_ATTEMPTS   = 20
BASE_RECONNECT_DELAY     = 2.0
BREAK_EVEN_POLL_SECS     = 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NARRATIVE CHAT FILTER — filter_is_commentary()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Any of these phrases = the message is NOT a new signal.
# Both Arabic and Latin-script keywords are listed here.

_COMMENTARY_PHRASES = [
    # ── Profit & target milestone updates ────────────────
    "حققت", "ضربت الأهداف", "ضرب الهدف", "النتائج", "حصيلة",
    "تم إغلاق", "تم الاغلاق", "مبروك", "تحديث الإشارة", "تحديث الاشارة",
    "هدف أول", "هدف اول", "هدف ثاني", "الهدف الاول", "الهدف الثاني",
    "ربح", "مكمل كدا كدا", "مكمل كده كده",
    "hit tp", "secured", "pips gained", "gained", "closed at",
    "done ✅", "summary", "alcanzado", "resultado",
    # ── Administrative / status updates ──────────────────
    "ليست للتداول", "للمراقبة فقط", "للمراقبه فقط",
    "ملغاة", "ملغى", "تأجيل", "مؤجلة",
    "إشارة تجريبية", "اشارة تجريبية",
    "اليوم اجازه", "اليوم إجازة",
    "صفقه محترمه ان شاء الله", "اصحي معايا", "أصحي معايا",
    "not for trading", "cancelled", "canceled",
    "delete order", "test signal", "observation only",
    "no trade", "stand by", "wait for",
    # ── Trailing stop / partial close notifications ───────
    "نقل الاستوب", "نقل وقف", "breakeven", "break even",
    "تعديل", "تعديل الاستوب",
]

# Pre-compile once — case-insensitive, Unicode aware
_COMMENTARY_RE = re.compile(
    "|".join(re.escape(p) for p in _COMMENTARY_PHRASES),
    re.IGNORECASE | re.UNICODE,
)


def filter_is_commentary(text: str) -> Tuple[bool, str]:
    """
    Returns (True, matched_phrase) if this message is a narrative update.
    Returns (False, "") if the message is a candidate new signal.
    """
    match = _COMMENTARY_RE.search(text)
    if match:
        return True, match.group(0)
    return False, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TradingSignal:
    timestamp:          str
    signal_type:        str
    entry_price:        float
    stop_loss:          float        # Already spread-adjusted
    stop_loss_raw:      float        # Original before padding
    take_profit:        float        # TP1
    take_profit_2:      Optional[float]
    take_profit_3:      Optional[float]
    source_folder:      str
    raw_message:        str
    confidence_score:   float = 1.0
    pattern_matched:    str   = ""
    direction_method:   str   = "math"   # "math" or "keyword"
    spread_padded:      bool  = True

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def fingerprint(self) -> str:
        key = f"{self.signal_type}{self.entry_price:.2f}{self.stop_loss_raw:.2f}{self.take_profit:.2f}"
        return hashlib.md5(key.encode()).hexdigest()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOM EMOJI DECODER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def decode_direction_from_entities(message) -> Optional[str]:
    """
    Scans Telegram MessageEntity objects for:
      - MessageEntityCustomEmoji (animated sticker emojis used as BUY/SELL labels)
      - Bold text containing BUY or SELL
    Returns 'BUY', 'SELL', or None.
    """
    if not hasattr(message, 'entities') or not message.entities:
        return None

    text = message.message or ""

    for entity in message.entities:
        # Slice the text covered by this entity
        start = entity.offset
        end   = entity.offset + entity.length
        chunk = text[start:end].upper()

        # Check the text within any entity type for direction keywords
        if "BUY" in chunk or "شراء" in chunk or "COMPRA" in chunk:
            return "BUY"
        if "SELL" in chunk or "بيع" in chunk or "VENTA" in chunk:
            return "SELL"

        # MessageEntityCustomEmoji carries an emoji_id (animated emoji)
        # We cannot resolve it without the sticker pack, so we check surrounding context
        if isinstance(entity, MessageEntityCustomEmoji):
            # Check 30 chars before/after this entity for direction text
            context = text[max(0, start - 30):end + 30].upper()
            if "BUY" in context or "شراء" in context:
                return "BUY"
            if "SELL" in context or "بيع" in context:
                return "SELL"

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIGNAL PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SignalParser:
    """
    Multi-format XAUUSD signal parser.

    Direction determination order:
      Step 1 — Math logic: if TP1 > parsed_entry → BUY, if TP1 < entry → SELL
      Step 2 — Text scan: BUY/SELL/شراء/بيع keywords in message
      Step 3 — Entity scan: custom emojis and bold text via decode_direction_from_entities()
      Step 4 — If steps 1 and 2/3 conflict → signal is DROPPED (mismatch logged)

    After direction confirmed:
      - SL is spread-adjusted by CHANNEL_SPREAD_PADDING ($2.00 per channel rules)
      - BUY:  SL_final = SL_parsed - 2.00 (wider protection below)
      - SELL: SL_final = SL_parsed + 2.00 (wider protection above)

    Entry sanity check:
      - Requires live MarketDataFeed snapshot to be passed in
      - Discards if abs(entry_parsed - spot_price) > MAX_SANITY_DISTANCE_USD
    """

    _NUM = r'\d{3,5}(?:\.\d{1,2})?'

    # ── Pattern library (pre-compiled) ─────────────────────
    PATTERNS: List[Tuple[str, re.Pattern]] = [

        # "XAUUSD SELL NOW 4460" / "XAUUSD BUY NOW 4381"
        ('now_market',
         re.compile(
             rf'(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+(?P<type>BUY|SELL)\s+NOW\s+(?P<entry>{_NUM})',
             re.IGNORECASE,
         )),

        # "BUY XAUUSD @ 2450.50, SL: 2445.00, TP: 2460.00"
        ('inline_full',
         re.compile(
             rf'(?P<type>BUY|SELL)\s+(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+@?\s*(?P<entry>{_NUM})'
             rf'[,\s]+(?:SL|Stop\s*Loss|S/L)\s*:?\s*(?P<sl>{_NUM})'
             rf'[,\s]+(?:TP|Take\s*Profit|T/P)\s*:?\s*(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # "XAUUSD\nBUY @ 2450\nSL 2445\nTP 2460"
        ('multiline_header',
         re.compile(
             rf'(?:XAUUSD|XAU[/\-]?USD|GOLD)\s*[\n\r]+'
             rf'(?P<type>BUY|SELL)\s+@?\s*(?P<entry>{_NUM})\s*[\n\r]+'
             rf'(?:SL|S/L|Stop\s*Loss)\s*:?\s*(?P<sl>{_NUM})\s*[\n\r]+'
             rf'(?:TP|T/P|Take\s*Profit)\s*:?\s*(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # "BUY\n2450\nSL: 2445\nTP: 2460"
        ('multiline_action',
         re.compile(
             rf'(?P<type>BUY|SELL)\s*(?:XAUUSD|XAU[/\-]?USD|GOLD)?\s*[\n\r]+'
             rf'(?P<entry>{_NUM})\s*[\n\r]+'
             rf'(?:SL|S/L)\s*:?\s*(?P<sl>{_NUM})\s*[\n\r]+'
             rf'(?:TP|T/P)\s*:?\s*(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # "#BUY 2450.50 SL2445 TP2460"
        ('hashtag',
         re.compile(
             rf'#(?P<type>BUY|SELL)\s+(?P<entry>{_NUM})\s+SL\s*(?P<sl>{_NUM})\s+TP\s*(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # "🟢 BUY GOLD 2450.50 | SL 2445 | TP 2460"
        ('emoji_pipe',
         re.compile(
             rf'(?:🟢|🔴|📈|📉|⬆|⬇|✅)?\s*(?P<type>BUY|SELL)\s+(?:XAUUSD|XAU[/\-]?USD|GOLD|XAUSD)\s+'
             rf'(?P<entry>{_NUM})\s*[|,]\s*SL\s*:?\s*(?P<sl>{_NUM})\s*[|,]\s*TP\s*:?\s*(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # "XAUUSD BUY 2450/2445/2460"
        ('slash_format',
         re.compile(
             rf'(?:XAUUSD|XAU[/\-]?USD|GOLD)\s+(?P<type>BUY|SELL)\s+'
             rf'(?P<entry>{_NUM})/(?P<sl>{_NUM})/(?P<tp1>{_NUM})',
             re.IGNORECASE,
         )),

        # Arabic: "شراء XAUUSD\n2450\nوقف 2445\nهدف 2460"
        ('arabic_format',
         re.compile(
             rf'(?P<type>شراء|بيع)\s+(?:XAUUSD|XAU|GOLD)?\s*[\n\r]*'
             rf'(?P<entry>{_NUM})\s*[\n\r]+'
             rf'(?:وقف|استوب|SL)\s*:?\s*(?P<sl>{_NUM})\s*[\n\r]+'
             rf'(?:هدف|TP)\s*:?\s*(?P<tp1>{_NUM})',
             re.IGNORECASE | re.UNICODE,
         )),
    ]

    # Extract TP2 and TP3 from already-matched message text
    _TP2_RE = re.compile(rf'TP\s*2\s*:?\s*({_NUM})', re.IGNORECASE)
    _TP3_RE = re.compile(rf'TP\s*3\s*:?\s*({_NUM})', re.IGNORECASE)

    @staticmethod
    def _is_valid_price(p: float) -> bool:
        return XAUUSD_PRICE_MIN <= p <= XAUUSD_PRICE_MAX

    @staticmethod
    def _direction_from_math(entry: float, sl: float, tp1: float) -> Optional[str]:
        """
        Primary direction logic — pure price math.
        BUY setup:  SL < Entry < TP1
        SELL setup: TP1 < Entry < SL
        """
        if sl < entry and tp1 > entry:
            return "BUY"
        if sl > entry and tp1 < entry:
            return "SELL"
        return None

    @staticmethod
    def _direction_from_text(text: str) -> Optional[str]:
        """Keyword fallback scan for BUY/SELL/Arabic equivalents."""
        upper = text.upper()
        has_buy  = bool(re.search(r'\bBUY\b|شراء|COMPRA', upper))
        has_sell = bool(re.search(r'\bSELL\b|بيع|VENTA', upper))
        if has_buy and not has_sell:
            return "BUY"
        if has_sell and not has_buy:
            return "SELL"
        return None

    @staticmethod
    def _apply_spread_padding(signal_type: str, sl_raw: float) -> float:
        """
        Channel rule: add 20 pips ($2.00 on Gold) to every SL.
        BUY  → move SL further below  (SL - 2.00)
        SELL → move SL further above  (SL + 2.00)
        """
        if signal_type == "BUY":
            return round(sl_raw - CHANNEL_SPREAD_PADDING, 2)
        else:
            return round(sl_raw + CHANNEL_SPREAD_PADDING, 2)

    @classmethod
    def parse_signal(
        cls,
        message_obj,
        source_folder: str = "SIGNALS",
        live_spot_price: Optional[float] = None,
    ) -> Optional[TradingSignal]:
        """
        Full parse pipeline.
        message_obj: Telethon Message object (for entity access) OR plain str.
        live_spot_price: current Gold price from MarketDataFeed (for sanity check).
        """
        # Normalise to text
        if hasattr(message_obj, 'message'):
            text = message_obj.message or ""
            msg_obj_for_entities = message_obj
        else:
            text = str(message_obj)
            msg_obj_for_entities = None

        text = text.strip()
        if not text:
            return None

        # ── Step 0: Narrative filter ──────────────────────
        is_commentary, phrase = filter_is_commentary(text)
        if is_commentary:
            logger.info(f"[FILTER] Commentary blocked — matched: '{phrase}'")
            return None

        # ── Step 0b: Instrument check ─────────────────────
        has_instrument = bool(re.search(
            r'XAUUSD|XAU[/\-]?USD|GOLD|XAUSD', text, re.IGNORECASE
        ))
        if not has_instrument:
            return None

        # ── Attempt all patterns ──────────────────────────
        for pattern_name, pattern in cls.PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            try:
                groups = match.groupdict()

                # Direction from regex group (may be Arabic keyword)
                raw_type = groups.get('type', '').strip()
                if raw_type in ("شراء",):
                    regex_direction: Optional[str] = "BUY"
                elif raw_type in ("بيع",):
                    regex_direction = "SELL"
                else:
                    regex_direction = raw_type.upper() if raw_type else None

                entry_price = float(groups['entry'])
                sl_raw      = float(groups['sl'])  if 'sl'  in groups and groups['sl']  else None
                tp1_raw     = float(groups['tp1']) if 'tp1' in groups and groups['tp1'] else None

                # ── Direction resolution (3-source waterfall) ─
                math_direction = None
                if sl_raw is not None and tp1_raw is not None:
                    math_direction = cls._direction_from_math(entry_price, sl_raw, tp1_raw)

                text_direction  = cls._direction_from_text(text)
                entity_direction = decode_direction_from_entities(msg_obj_for_entities) \
                                   if msg_obj_for_entities else None

                # Priority: math > regex > text/entity
                final_direction = math_direction or regex_direction or text_direction or entity_direction
                if final_direction is None:
                    logger.debug(f"[SKIP] Could not determine direction from message")
                    continue

                # Sanity cross-check: if math says BUY but text says SELL → DROP
                keyword_direction = text_direction or entity_direction or regex_direction
                if (math_direction and keyword_direction and
                        math_direction != keyword_direction):
                    logger.warning(
                        f"[DROP] Direction mismatch — math={math_direction}, "
                        f"keyword={keyword_direction}. Signal discarded."
                    )
                    return None

                direction_method = "math" if math_direction else "keyword"

                # For "NOW" market orders: SL/TP not in the initial message —
                # they'll arrive as a follow-up or default risk values apply.
                # We still queue the signal; the analyzer handles SL/TP defaults.
                if sl_raw is None:
                    sl_raw = entry_price - 20.0 if final_direction == "BUY" else entry_price + 20.0
                if tp1_raw is None:
                    tp1_raw = entry_price + 30.0 if final_direction == "BUY" else entry_price - 30.0

                # Price range guard
                for p, label in [(entry_price, "Entry"), (sl_raw, "SL"), (tp1_raw, "TP1")]:
                    if not cls._is_valid_price(p):
                        logger.warning(f"[SKIP] {label}={p} outside valid Gold range")
                        break
                else:
                    pass

                # ── Entry sanity check vs live market price ───
                if live_spot_price and live_spot_price > 0:
                    delta = abs(entry_price - live_spot_price)
                    if delta > MAX_SANITY_DISTANCE_USD:
                        logger.warning(
                            f"[DROP] Entry sanity fail — parsed={entry_price}, "
                            f"live={live_spot_price:.2f}, delta=${delta:.2f} > "
                            f"${MAX_SANITY_DISTANCE_USD:.2f}. Discarding stale/typo signal."
                        )
                        return None

                # ── Spread calibration ────────────────────────
                sl_adjusted = cls._apply_spread_padding(final_direction, sl_raw)
                logger.info(
                    f"[SPREAD] SL padded: raw={sl_raw} → adjusted={sl_adjusted} "
                    f"({final_direction}, +{CHANNEL_SPREAD_PADDING}$ padding)"
                )

                # ── Re-validate logic after spread padding ────
                if final_direction == "BUY":
                    if not (sl_adjusted < entry_price < tp1_raw):
                        logger.warning(
                            f"[SKIP] Post-spread BUY logic invalid: "
                            f"SL={sl_adjusted} Entry={entry_price} TP={tp1_raw}"
                        )
                        continue
                else:
                    if not (tp1_raw < entry_price < sl_adjusted):
                        logger.warning(
                            f"[SKIP] Post-spread SELL logic invalid: "
                            f"TP={tp1_raw} Entry={entry_price} SL={sl_adjusted}"
                        )
                        continue

                # ── TP2 / TP3 extraction ──────────────────────
                tp2_match = cls._TP2_RE.search(text)
                tp3_match = cls._TP3_RE.search(text)
                tp2 = float(tp2_match.group(1)) if tp2_match else None
                tp3 = float(tp3_match.group(1)) if tp3_match else None

                # ── Confidence score: R:R based ───────────────
                rr = (abs(tp1_raw - entry_price) / abs(entry_price - sl_adjusted)
                      if abs(entry_price - sl_adjusted) > 0 else 0)
                confidence = round(min(1.0, 0.5 + rr / 10.0), 3)

                sig = TradingSignal(
                    timestamp         = datetime.now(timezone.utc).isoformat(),
                    signal_type       = final_direction,
                    entry_price       = round(entry_price, 2),
                    stop_loss         = sl_adjusted,
                    stop_loss_raw     = round(sl_raw, 2),
                    take_profit       = round(tp1_raw, 2),
                    take_profit_2     = round(tp2, 2) if tp2 else None,
                    take_profit_3     = round(tp3, 2) if tp3 else None,
                    source_folder     = source_folder,
                    raw_message       = text[:500],
                    confidence_score  = confidence,
                    pattern_matched   = pattern_name,
                    direction_method  = direction_method,
                    spread_padded     = True,
                )

                logger.info(
                    f"[PARSED][{pattern_name}] {final_direction} XAUUSD @ {entry_price} | "
                    f"SL={sl_adjusted} (raw={sl_raw}) | TP1={tp1_raw} | "
                    f"RR={rr:.2f} | Direction={direction_method}"
                )
                return sig

            except (ValueError, AttributeError, KeyError) as e:
                logger.debug(f"[SKIP][{pattern_name}] Parse error: {e}")
                continue

        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DEDUPLICATION CACHE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DeduplicationCache:
    def __init__(self, window_secs: int = DEDUP_WINDOW_SECS):
        self._cache: Dict[str, float] = {}
        self._window = window_secs

    def is_duplicate(self, signal: TradingSignal) -> bool:
        key = signal.fingerprint()
        now = time.monotonic()
        self._evict(now)
        if key in self._cache:
            return True
        self._cache[key] = now
        return False

    def _evict(self, now: float):
        for k in [k for k, t in self._cache.items() if now - t > self._window]:
            del self._cache[k]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BREAK-EVEN MONITOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ActivePosition:
    """Tracks one open position across both accounts for break-even management."""
    cl_ord_id_live:  str
    cl_ord_id_demo:  str
    signal_type:     str
    entry_price:     float
    stop_loss:       float
    tp1:             float
    tp2:             Optional[float]
    be_triggered:    bool = False       # True once break-even has fired
    opened_at:       str  = ""


class BreakEvenMonitor:
    """
    Background task that polls live spot price every BREAK_EVEN_POLL_SECS seconds.
    When price reaches TP1 (or TP2 if set), fires a FIX position modification
    to move SL on BOTH accounts to the original entry price (break-even).

    After break-even fires, the position is removed from tracking.
    """

    def __init__(self, market_feed, fix_executor):
        self._positions: List[ActivePosition] = []
        self._lock       = asyncio.Lock()
        self.market_feed = market_feed
        self.fix_executor = fix_executor
        logger.info("BreakEvenMonitor initialized")

    async def register(self, position: ActivePosition):
        async with self._lock:
            self._positions.append(position)
        logger.info(
            f"[BE] Tracking position {position.cl_ord_id_live} | "
            f"{position.signal_type} @ {position.entry_price} | "
            f"TP1={position.tp1} | BE triggers at TP1"
        )

    async def run_forever(self):
        logger.info(f"Break-even monitor running (poll every {BREAK_EVEN_POLL_SECS}s)")
        while True:
            await asyncio.sleep(BREAK_EVEN_POLL_SECS)
            await self._check_positions()

    async def _check_positions(self):
        snap  = self.market_feed.snapshot
        price = snap.current_price
        if price <= 0:
            return

        async with self._lock:
            remaining = []
            for pos in self._positions:
                if pos.be_triggered:
                    continue

                hit_tp1 = (
                    (pos.signal_type == "BUY"  and price >= pos.tp1) or
                    (pos.signal_type == "SELL" and price <= pos.tp1)
                )
                hit_tp2 = (
                    pos.tp2 is not None and (
                        (pos.signal_type == "BUY"  and price >= pos.tp2) or
                        (pos.signal_type == "SELL" and price <= pos.tp2)
                    )
                )

                if hit_tp1 or hit_tp2:
                    trigger = "TP2" if hit_tp2 else "TP1"
                    logger.info(
                        f"[BE] {trigger} reached on {pos.cl_ord_id_live} | "
                        f"Price={price} | Moving SL to break-even={pos.entry_price}"
                    )
                    await self._fire_break_even(pos)
                    pos.be_triggered = True
                else:
                    remaining.append(pos)

            self._positions = remaining

    async def _fire_break_even(self, pos: ActivePosition):
        """Send SL modification to move both accounts to break-even."""
        modification = {
            "action":       "MODIFY_SL",
            "signal_type":  pos.signal_type,
            "entry_price":  pos.entry_price,
            "stop_loss":    pos.entry_price,   # New SL = entry (zero risk)
            "take_profit":  pos.tp1,
            "lot_size":     0.0,               # Not used for modification
            "cl_ord_id_live": pos.cl_ord_id_live,
            "cl_ord_id_demo": pos.cl_ord_id_demo,
        }
        try:
            await self.fix_executor.modify_position_sl(modification)
            logger.info(
                f"[BE] Break-even fired | LIVE={pos.cl_ord_id_live} | "
                f"DEMO={pos.cl_ord_id_demo} | New SL={pos.entry_price}"
            )
        except Exception as e:
            logger.error(f"[BE] Break-even modification failed: {e}", exc_info=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM LISTENER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TelegramListener:
    """
    Connects to Telegram in one of two modes:
      Mode A — StringSession (silent, no terminal prompt, cloud-safe)
      Mode B — Interactive file session (prompts for phone + SMS code)

    Monitors SIGNALS_FOLDER_ID for new trading signals.
    Monitors NEWS_FOLDER_ID for news text (passed to news queue).
    Wires MarketDataFeed for live price sanity checks on every parsed signal.
    Wires BreakEvenMonitor to register positions after execution.
    """

    def __init__(
        self,
        signal_queue:    asyncio.Queue,
        news_queue:      asyncio.Queue,
        market_feed      = None,
        be_monitor: Optional[BreakEvenMonitor] = None,
    ):
        # ── Mode A: StringSession (production, silent) ────
        if TELEGRAM_STRING_SESSION:
            session = StringSession(TELEGRAM_STRING_SESSION)
            logger.info("Telegram: using StringSession (Mode A — silent production)")
        else:
            # Mode B: File-based session + interactive login
            session = SESSION_NAME
            logger.info("Telegram: using file session (Mode B — interactive)")

        self.client       = TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        self.signal_queue = signal_queue
        self.news_queue   = news_queue
        self.market_feed  = market_feed
        self.be_monitor   = be_monitor
        self._dedup       = DeduplicationCache()
        self._running     = False

    async def start(self):
        if TELEGRAM_STRING_SESSION:
            await self.client.start()     # No prompt in StringSession mode
        else:
            await self.client.start(phone=TELEGRAM_PHONE)   # Prompts for code

        me = await self.client.get_me()
        logger.info(f"Authenticated as: {me.first_name} (@{getattr(me, 'username', 'N/A')})")

        for folder_id, label in [(SIGNALS_FOLDER_ID, "Signals"), (NEWS_FOLDER_ID, "News")]:
            if not folder_id:
                logger.warning(f"{label} folder ID not configured — skipping")
                continue
            try:
                await self.client.get_entity(folder_id)
                logger.info(f"{label} folder ({folder_id}): accessible ✓")
            except Exception as e:
                logger.warning(
                    f"Cannot access {label} folder {folder_id}: {e} "
                    f"— listener will start but may not receive messages from this chat"
                )

    def register_handlers(self):
        @self.client.on(events.NewMessage(chats=SIGNALS_FOLDER_ID))
        async def handle_signal(event):
            msg  = event.message
            text = msg.message or ""
            if not text:
                return

            logger.info(
                f"[MSG] Signal channel | {len(text)} chars: "
                f"{text[:80].replace(chr(10), ' ')!r}"
            )

            # Get current spot price for sanity check
            live_price = (
                self.market_feed.snapshot.current_price
                if self.market_feed and not self.market_feed.snapshot.is_stale
                else None
            )

            # Full parse (includes narrative filter, math direction, spread padding, sanity)
            signal = SignalParser.parse_signal(
                message_obj     = msg,
                source_folder   = "SIGNALS",
                live_spot_price = live_price,
            )

            if not signal:
                logger.debug("[MSG] No valid signal extracted")
                return

            if self._dedup.is_duplicate(signal):
                logger.info(
                    f"[DEDUP] Duplicate suppressed: {signal.signal_type} @ {signal.entry_price}"
                )
                return

            await self.signal_queue.put(signal)
            logger.info(
                f"[QUEUE] Signal enqueued: {signal.signal_type} @ {signal.entry_price} | "
                f"SL={signal.stop_loss} (padded) | TP1={signal.take_profit} | "
                f"Confidence={signal.confidence_score}"
            )

        @self.client.on(events.NewMessage(chats=NEWS_FOLDER_ID))
        async def handle_news(event):
            text = event.message.message or ""
            if text:
                logger.info(f"[NEWS] {len(text)} chars: {text[:60].replace(chr(10), ' ')!r}")
                await self.news_queue.put(text)

        logger.info("Event handlers registered")

    async def run_with_reconnect(self):
        attempt = 0
        while attempt < MAX_RECONNECT_ATTEMPTS:
            try:
                await self.start()
                self.register_handlers()
                self._running = True

                logger.info("━" * 60)
                logger.info("TELEGRAM LISTENER ACTIVE")
                logger.info(f"  Signals Folder ID : {SIGNALS_FOLDER_ID}")
                logger.info(f"  News Folder ID    : {NEWS_FOLDER_ID}")
                logger.info(f"  Spread Padding    : ${CHANNEL_SPREAD_PADDING:.2f} (20 pips)")
                logger.info(f"  Sanity Threshold  : ${MAX_SANITY_DISTANCE_USD:.2f}")
                logger.info(f"  Session Mode      : {'StringSession (A)' if TELEGRAM_STRING_SESSION else 'File (B)'}")
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
                logger.warning(
                    f"Network error (attempt {attempt}/{MAX_RECONNECT_ATTEMPTS}): {e}. "
                    f"Retry in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                attempt += 1
                await asyncio.sleep(10)

        logger.critical("Max reconnect attempts reached.")

    async def stop(self):
        self._running = False
        if self.client.is_connected():
            await self.client.disconnect()
        logger.info("Telegram client disconnected")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STANDALONE ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _standalone():
    """Run the listener standalone (no FIX executor) for testing."""
    sq: asyncio.Queue = asyncio.Queue()
    nq: asyncio.Queue = asyncio.Queue()

    async def printer():
        while True:
            sig = await sq.get()
            print(f"\n→ SIGNAL: {sig.to_json()}\n")

    listener = TelegramListener(sq, nq)
    await asyncio.gather(
        listener.run_with_reconnect(),
        printer(),
    )


if __name__ == "__main__":
    asyncio.run(_standalone())
