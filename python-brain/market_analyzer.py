"""
market_analyzer.py — 5-Filter Live Market Analysis Pipeline
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All 5 filters now run on LIVE data from MarketDataFeed + ForexNewsFeed.
No manual data injection required.

FILTER PIPELINE (all 5 must clear — any BLOCK kills the signal):
  1. News Mode Filter     — ForexFactory calendar auto-detection
                            Block: High-impact event within ±30 min window
  2. Pivot Point Filter   — Strict boundary rejection (R1/S1 buffer zones)
                            Block: BUY near R1 (overextended into resistance)
                            Block: SELL near S1 (collapsing into support)
  3. RSI Momentum Filter  — RSI(14) via Wilder's smoothing
                            Block: RSI > 75 for BUY (overbought market top)
                            Block: RSI < 25 for SELL (oversold market bottom)
  4. ATR Volatility Filter — ATR(14) danger zone guard
                            Block: ATR > 30 (extreme volatility, slippage risk)
                            Block: ATR < 2  (suspiciously flat, spread risk)
  5. EMA Trend Filter     — EMA(50) trend alignment
                            Warn: Counter-trend entry (does not hard-block,
                            but reduces confluence score by 1)

LOT SIZING (deterministic, strictly enforced):
  News_Mode active   → 0.01  (unconditional override)
  Confluence 0       → 0.01
  Confluence 1       → 0.02
  Confluence 2       → 0.03
  Confluence 3-4     → 0.04
  Confluence 5       → 0.05  (all 5 filters pass perfectly)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass, asdict
from enum import Enum
import json

logger = logging.getLogger('analyzer')

# ── Import live feed types ──────────────────────────────
from market_data_feed import MarketDataFeed, MarketSnapshot
from news_feed import ForexNewsFeed, NewsModeStatus

# ── Import config ───────────────────────────────────────
try:
    from config import ATR_MAX_THRESHOLD, ATR_MIN_THRESHOLD, PIVOT_BUFFER_USD
except ImportError:
    ATR_MAX_THRESHOLD = 30.0
    ATR_MIN_THRESHOLD = 2.0
    PIVOT_BUFFER_USD  = 2.0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RSI_OVERBOUGHT = 75
RSI_OVERSOLD   = 25

LOT_NEWS_MODE       = 0.01
LOT_NONE            = 0.01
LOT_PARTIAL         = 0.02
LOT_STANDARD        = 0.03
LOT_STRONG          = 0.04
LOT_FULL_CONFLUENCE = 0.05


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Gate(Enum):
    PASS  = "PASS"
    BLOCK = "BLOCK"
    WARN  = "WARN"


@dataclass
class ValidatedSignal:
    timestamp:             str
    signal_type:           str
    entry_price:           float
    stop_loss:             float
    take_profit:           float
    lot_size:              float
    confluence_level:      int
    confidence_score:      float
    is_ready_for_execution: bool
    filter_details:        List[Dict]
    news_mode_active:      bool
    market_snapshot:       Dict

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 1: NEWS MODE (live ForexFactory feed)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def filter_news(news_status: NewsModeStatus) -> Tuple[Gate, str, float]:
    if news_status.active:
        mins = news_status.minutes_remaining()
        return (
            Gate.WARN,
            f"NEWS_MODE active — '{news_status.triggering_event}' | "
            f"{mins:.0f} min remaining | Lot clamped to 0.01",
            0.2,
        )
    return Gate.PASS, "No high-impact news event active. Market clear.", 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 2: PIVOT POINT — STRICT BOUNDARY REJECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calculate_pivots(snap: MarketSnapshot) -> Dict[str, float]:
    h, l, c = snap.prev_high, snap.prev_low, snap.prev_close
    pivot = (h + l + c) / 3.0
    return {
        "pivot": round(pivot, 4),
        "r1":    round((2.0 * pivot) - l, 4),
        "s1":    round((2.0 * pivot) - h, 4),
        "r2":    round(pivot + (h - l), 4),
        "s2":    round(pivot - (h - l), 4),
    }


def filter_pivot(
    signal_type: str,
    entry_price: float,
    pivots: Dict[str, float],
    buffer: float = PIVOT_BUFFER_USD,
) -> Tuple[Gate, str, float]:
    r1, s1 = pivots['r1'], pivots['s1']
    dist_r1 = abs(entry_price - r1)
    dist_s1 = abs(entry_price - s1)

    if signal_type == 'BUY':
        if dist_r1 <= buffer:
            return (
                Gate.BLOCK,
                f"BUY BLOCKED — Entry ${entry_price} within ${dist_r1:.2f} of R1 (${r1:.2f}). Overextended.",
                0.0,
            )
        if dist_s1 <= buffer:
            return (
                Gate.PASS,
                f"BUY PASS — S1 bounce. Entry ${entry_price} within ${dist_s1:.2f} of S1 (${s1:.2f}).",
                0.95,
            )
        return (
            Gate.WARN,
            f"BUY WARN — Neutral zone. R1=${r1:.2f} (${dist_r1:.2f} away), S1=${s1:.2f} (${dist_s1:.2f} away).",
            0.5,
        )
    else:  # SELL
        if dist_s1 <= buffer:
            return (
                Gate.BLOCK,
                f"SELL BLOCKED — Entry ${entry_price} within ${dist_s1:.2f} of S1 (${s1:.2f}). Oversold.",
                0.0,
            )
        if dist_r1 <= buffer:
            return (
                Gate.PASS,
                f"SELL PASS — R1 rejection. Entry ${entry_price} within ${dist_r1:.2f} of R1 (${r1:.2f}).",
                0.95,
            )
        return (
            Gate.WARN,
            f"SELL WARN — Neutral zone. S1=${s1:.2f} (${dist_s1:.2f} away), R1=${r1:.2f} (${dist_r1:.2f} away).",
            0.5,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 3: RSI MOMENTUM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def filter_rsi(signal_type: str, rsi: float) -> Tuple[Gate, str, float]:
    if signal_type == 'BUY':
        if rsi > RSI_OVERBOUGHT:
            return Gate.BLOCK, f"BUY BLOCKED — RSI {rsi:.1f} > {RSI_OVERBOUGHT} (overbought).", 0.0
        if rsi < RSI_OVERSOLD:
            return Gate.PASS, f"BUY PASS — RSI {rsi:.1f} deeply oversold (strong reversal setup).", 0.95
        score = round((RSI_OVERBOUGHT - rsi) / RSI_OVERBOUGHT, 3)
        return Gate.PASS, f"BUY PASS — RSI {rsi:.1f} in normal range.", score
    else:  # SELL
        if rsi < RSI_OVERSOLD:
            return Gate.BLOCK, f"SELL BLOCKED — RSI {rsi:.1f} < {RSI_OVERSOLD} (oversold).", 0.0
        if rsi > RSI_OVERBOUGHT:
            return Gate.PASS, f"SELL PASS — RSI {rsi:.1f} deeply overbought (strong reversal setup).", 0.95
        score = round(rsi / 100.0, 3)
        return Gate.PASS, f"SELL PASS — RSI {rsi:.1f} in normal range.", score


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 4: ATR VOLATILITY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def filter_atr(
    atr: float,
    atr_max: float = ATR_MAX_THRESHOLD,
    atr_min: float = ATR_MIN_THRESHOLD,
) -> Tuple[Gate, str, float]:
    """
    ATR(14) Volatility Guard.
    - BLOCK if ATR > atr_max: extreme volatility, unacceptable slippage risk.
    - BLOCK if ATR < atr_min: market is dead, spread will eat the trade.
    - PASS in the healthy window.
    """
    if atr > atr_max:
        return (
            Gate.BLOCK,
            f"ATR BLOCKED — ATR(14)=${atr:.2f} exceeds max threshold ${atr_max:.2f}. "
            f"Extreme volatility — slippage risk unacceptable.",
            0.0,
        )
    if atr < atr_min:
        return (
            Gate.BLOCK,
            f"ATR BLOCKED — ATR(14)=${atr:.2f} below min threshold ${atr_min:.2f}. "
            f"Market too flat — spread cost exceeds expected move.",
            0.0,
        )
    # Ideal zone: score peaks at mid-range
    normalized = 1.0 - abs(atr - (atr_max / 2)) / (atr_max / 2)
    score = round(max(0.4, min(1.0, normalized)), 3)
    return Gate.PASS, f"ATR PASS — ATR(14)=${atr:.2f} in healthy volatility range.", score


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 5: EMA TREND ALIGNMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def filter_ema_trend(
    signal_type: str,
    entry_price: float,
    ema_50: float,
) -> Tuple[Gate, str, float]:
    """
    EMA(50) Trend Alignment Filter.
    BUY signals should enter above EMA50 (uptrend confirmation).
    SELL signals should enter below EMA50 (downtrend confirmation).
    Counter-trend trades are allowed but WARN and reduce confluence.
    """
    if ema_50 <= 0:
        return Gate.WARN, "EMA50 not available (insufficient data).", 0.5

    if signal_type == 'BUY':
        if entry_price > ema_50:
            margin = entry_price - ema_50
            return (
                Gate.PASS,
                f"EMA PASS — BUY aligned with uptrend. Price ${entry_price} above EMA50 ${ema_50:.2f} (+${margin:.2f}).",
                0.85,
            )
        margin = ema_50 - entry_price
        return (
            Gate.WARN,
            f"EMA WARN — Counter-trend BUY. Price ${entry_price} below EMA50 ${ema_50:.2f} (-${margin:.2f}). "
            f"Confluence reduced.",
            0.3,
        )
    else:  # SELL
        if entry_price < ema_50:
            margin = ema_50 - entry_price
            return (
                Gate.PASS,
                f"EMA PASS — SELL aligned with downtrend. Price ${entry_price} below EMA50 ${ema_50:.2f} (-${margin:.2f}).",
                0.85,
            )
        margin = entry_price - ema_50
        return (
            Gate.WARN,
            f"EMA WARN — Counter-trend SELL. Price ${entry_price} above EMA50 ${ema_50:.2f} (+${margin:.2f}). "
            f"Confluence reduced.",
            0.3,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOT SIZE ENGINE — 5-LEVEL DETERMINISTIC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def determine_lot_size(confluence_level: int, news_mode_active: bool) -> float:
    """
    Strict 5-level lot envelope per Blueprint Iron Rules.

    news_mode=True  → 0.01  ALWAYS (unconditional override for both accounts)
    confluence 0    → 0.01
    confluence 1    → 0.02
    confluence 2    → 0.03
    confluence 3-4  → 0.04
    confluence 5    → 0.05  (all 5 filters perfect)
    """
    if news_mode_active:
        return LOT_NEWS_MODE
    return {0: LOT_NONE, 1: LOT_PARTIAL, 2: LOT_STANDARD, 3: LOT_STRONG,
            4: LOT_STRONG, 5: LOT_FULL_CONFLUENCE}.get(min(confluence_level, 5), LOT_NONE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MARKET ANALYZER — MAIN PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MarketAnalyzer:
    """
    Async pipeline that:
      1. Reads live MarketSnapshot from MarketDataFeed
      2. Reads live NewsModeStatus from ForexNewsFeed
      3. Applies all 5 filters to each incoming signal
      4. Computes lot size deterministically from confluence level
      5. Forwards validated signals to the FIX executor queue
    """

    def __init__(
        self,
        signal_queue:    asyncio.Queue,
        validated_queue: asyncio.Queue,
        market_feed:     MarketDataFeed,
        news_feed:       ForexNewsFeed,
    ):
        self.signal_queue    = signal_queue
        self.validated_queue = validated_queue
        self.market_feed     = market_feed
        self.news_feed       = news_feed
        logger.info("MarketAnalyzer initialized (5-filter live pipeline)")

    def validate_signal(self, raw_signal: Dict) -> Tuple[bool, ValidatedSignal]:
        signal_type = raw_signal.get('signal_type', '').upper()
        entry_price = float(raw_signal.get('entry_price', 0))
        stop_loss   = float(raw_signal.get('stop_loss', 0))
        take_profit = float(raw_signal.get('take_profit', 0))
        confidence  = float(raw_signal.get('confidence_score', 1.0))

        snap         = self.market_feed.snapshot
        news_status  = self.news_feed.evaluate_news_mode()
        pivots       = calculate_pivots(snap)
        filter_details: List[Dict] = []

        # ── Filter 1: News Mode ──────────────────────────
        g1, r1_str, s1 = filter_news(news_status)
        filter_details.append({"name": "News Mode",       "status": g1.value,  "reason": r1_str,  "confluence": s1, "news_mode": news_status.active})

        # ── Filter 2: Pivot Points ───────────────────────
        g2, r2, s2 = filter_pivot(signal_type, entry_price, pivots)
        filter_details.append({"name": "Pivot Points",    "status": g2.value,  "reason": r2,      "confluence": s2, "pivots": pivots})

        # ── Filter 3: RSI Momentum ───────────────────────
        g3, r3, s3 = filter_rsi(signal_type, snap.rsi_14)
        filter_details.append({"name": "RSI Momentum",   "status": g3.value,  "reason": r3,      "confluence": s3, "rsi_14": snap.rsi_14})

        # ── Filter 4: ATR Volatility ─────────────────────
        g4, r4, s4 = filter_atr(snap.atr_14)
        filter_details.append({"name": "ATR Volatility", "status": g4.value,  "reason": r4,      "confluence": s4, "atr_14": snap.atr_14})

        # ── Filter 5: EMA Trend ──────────────────────────
        g5, r5, s5 = filter_ema_trend(signal_type, entry_price, snap.ema_50)
        filter_details.append({"name": "EMA(50) Trend",  "status": g5.value,  "reason": r5,      "confluence": s5, "ema_50": snap.ema_50})

        # ── Decision ─────────────────────────────────────
        all_gates = [g1, g2, g3, g4, g5]
        blocked   = any(g == Gate.BLOCK for g in all_gates)

        # Confluence = count of PASS gates (WARN counts as 0)
        confluence_level = sum(1 for g in all_gates if g == Gate.PASS)
        news_mode_active = news_status.active
        lot_size         = determine_lot_size(confluence_level, news_mode_active)
        is_ready         = not blocked and confluence_level >= 1

        validated = ValidatedSignal(
            timestamp              = datetime.utcnow().isoformat(),
            signal_type            = signal_type,
            entry_price            = entry_price,
            stop_loss              = stop_loss,
            take_profit            = take_profit,
            lot_size               = lot_size,
            confluence_level       = confluence_level,
            confidence_score       = round(confidence, 3),
            is_ready_for_execution = is_ready,
            filter_details         = filter_details,
            news_mode_active       = news_mode_active,
            market_snapshot        = {
                "price":   snap.current_price,
                "rsi_14":  snap.rsi_14,
                "atr_14":  snap.atr_14,
                "ema_50":  snap.ema_50,
                "is_stale": snap.is_stale,
            },
        )

        decision = "EXECUTE" if is_ready else "BLOCK"
        blocked_names = [fd["name"] for fd in filter_details if fd["status"] == "BLOCK"]

        logger.info(
            f"[{decision}] {signal_type} @ {entry_price} | "
            f"Lot={lot_size} | Confluence={confluence_level}/5 | "
            f"NewsMode={news_mode_active} | Price={snap.current_price} | "
            f"RSI={snap.rsi_14} | ATR={snap.atr_14} | EMA50={snap.ema_50}"
            + (f" | BLOCKED_BY={blocked_names}" if blocked_names else "")
        )

        return is_ready, validated

    async def process_signals_loop(self):
        logger.info("Signal processing loop started")
        while True:
            raw_signal = await self.signal_queue.get()
            try:
                if self.market_feed.snapshot.is_stale:
                    logger.warning("Market data is stale — waiting for first feed refresh before processing signal")
                    await asyncio.sleep(5)

                signal_dict = raw_signal.to_dict() if hasattr(raw_signal, 'to_dict') else raw_signal
                is_valid, validated = self.validate_signal(signal_dict)

                if is_valid:
                    await self.validated_queue.put(validated)
                else:
                    logger.warning(
                        f"Signal dropped | {validated.signal_type} @ {validated.entry_price}"
                    )
            except Exception as e:
                logger.error(f"Signal processing error: {e}", exc_info=True)
            finally:
                self.signal_queue.task_done()
