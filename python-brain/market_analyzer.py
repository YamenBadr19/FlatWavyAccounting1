"""
The Brain: Market Analyzer — AI Sentiment & Technical Filters
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receives signals from telegram_listener via asyncio.Queue.
Applies three strict filtering gates before forwarding to cBot:
  1. AI News Sentiment   → News_Mode flag + sentiment classification
  2. Pivot Point Filter  → STRICT boundary rejection (R1/S1 buffer zones)
  3. RSI Momentum Filter → Blocks overbought BUYs and oversold SELLs

LOT SIZING RULES (Strict Blueprint Compliance):
  - News_Mode active       → 0.01 (Capital Preservation, always)
  - Partial confluence     → 0.02 (1 technical filter passing)
  - Standard confluence    → 0.03 (2 technical filters passing, no news risk)
  - Full confluence        → 0.05 (all 3 gates pass, signal + news + pivot bounce)

OPTIMIZATIONS:
  - Async pipeline: non-blocking news + signal processing in parallel coroutines
  - Weighted sentiment scoring (replaces binary keyword hits)
  - Configurable pivot boundary buffer via PIVOT_BUFFER_PIPS constant
  - RSI computed via Wilder's smoothing (accurate 14-period EMA method)
  - Lot decision is a pure deterministic function — no ambiguity
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass, asdict
from enum import Enum
import json

logger = logging.getLogger('brain')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEWS_MODE_DURATION_MINUTES = 30
RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25
RSI_PERIOD = 14

# Pivot boundary: price must be this many USD away from R1/S1 to not trigger rejection
# Gold pip ≈ $0.10, so 5 pips ≈ $0.50. We use a wider $2.00 buffer for XAUUSD safety.
PIVOT_BUFFER_USD = 2.0

# Lot size envelope — strictly enforced
LOT_NEWS_MODE        = 0.01   # Capital Preservation: News_Mode active
LOT_PARTIAL          = 0.02   # Partial confluence (1 technical filter)
LOT_STANDARD         = 0.03   # Standard confluence (2 technical filters)
LOT_FULL_CONFLUENCE  = 0.05   # Full confluence (all 3 gates pass)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Sentiment(Enum):
    BULLISH  = "BULLISH"
    BEARISH  = "BEARISH"
    VOLATILE = "VOLATILE"
    NEUTRAL  = "NEUTRAL"


class Gate(Enum):
    PASS  = "PASS"
    BLOCK = "BLOCK"
    WARN  = "WARN"


@dataclass
class FilterResult:
    gate_name: str
    status: Gate
    reason: str
    confluence_score: float   # 0.0 – 1.0


@dataclass
class ValidatedSignal:
    timestamp: str
    signal_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    confluence_level: int
    confidence_score: float
    is_ready_for_execution: bool
    filter_details: List[Dict]
    news_mode_active: bool

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 1: AI NEWS SENTIMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NewsSentimentFilter:
    """
    Weighted keyword sentiment analysis for Gold (XAUUSD).

    Improvement over v1: instead of a binary keyword count,
    each keyword carries a weight reflecting its historical
    impact magnitude on Gold prices.

    News_Mode activates when a high-impact event is detected,
    clamping risk to the minimum lot (0.01) for 30 minutes.
    """

    # (keyword_lowercase, weight)  — higher weight = stronger signal
    BULLISH_GOLD: List[Tuple[str, float]] = [
        ("rate cut", 1.0), ("fed cut", 1.0), ("dollar weakness", 0.9),
        ("usd weak", 0.9), ("inflation rising", 0.8), ("cpi high", 0.8),
        ("safe haven", 0.7), ("geopolitical", 0.8), ("war", 0.9),
        ("tensions", 0.6), ("economic slowdown", 0.7), ("recession", 0.9),
        ("debt crisis", 0.9), ("currency crisis", 0.9), ("bank collapse", 1.0),
        ("yield drop", 0.7), ("sanctions", 0.6), ("uncertainty", 0.5),
    ]

    BEARISH_GOLD: List[Tuple[str, float]] = [
        ("rate hike", 1.0), ("hawkish", 0.9), ("dollar strong", 0.9),
        ("usd strong", 0.9), ("inflation down", 0.8), ("deflation", 0.7),
        ("risk-on", 0.6), ("strong economy", 0.7), ("gdp growth", 0.7),
        ("employment strong", 0.8), ("nfp beat", 0.8), ("taper", 0.8),
        ("quantitative tightening", 0.9), ("qt", 0.6), ("yields rise", 0.7),
    ]

    HIGH_IMPACT_KEYWORDS: List[str] = [
        "fomc", "federal reserve", "interest rate decision",
        "inflation", "cpi", "ppi", "nfp", "non-farm payroll",
        "gdp", "geopolitical", "war", "sanctions", "bank crisis",
        "currency crisis", "default", "emergency", "breaking",
    ]

    def __init__(self):
        self._news_mode_until: Optional[datetime] = None
        logger.info("NewsSentimentFilter ready")

    def analyze(self, news_text: str) -> Tuple[Sentiment, bool, str]:
        """
        Returns: (Sentiment, news_mode_activated_now, explanation)
        """
        text_lower = news_text.lower()

        high_impact = any(kw in text_lower for kw in self.HIGH_IMPACT_KEYWORDS)
        if high_impact:
            self._news_mode_until = datetime.now(timezone.utc) + timedelta(minutes=NEWS_MODE_DURATION_MINUTES)
            logger.warning(f"NEWS_MODE ACTIVATED until {self._news_mode_until.isoformat()}")

        bull_score = sum(w for kw, w in self.BULLISH_GOLD if kw in text_lower)
        bear_score = sum(w for kw, w in self.BEARISH_GOLD if kw in text_lower)
        total = bull_score + bear_score

        if total == 0:
            sentiment = Sentiment.VOLATILE if high_impact else Sentiment.NEUTRAL
            reason = "High-impact event, unclear direction" if high_impact else "No significant keywords"
        elif bull_score / total >= 0.6:
            sentiment = Sentiment.BULLISH
            reason = f"Bullish score {bull_score:.1f} vs Bearish {bear_score:.1f}"
        elif bear_score / total >= 0.6:
            sentiment = Sentiment.BEARISH
            reason = f"Bearish score {bear_score:.1f} vs Bullish {bull_score:.1f}"
        else:
            sentiment = Sentiment.VOLATILE
            reason = f"Mixed signals (Bull={bull_score:.1f}, Bear={bear_score:.1f})"

        return sentiment, high_impact, reason

    def is_news_mode_active(self) -> bool:
        if self._news_mode_until is None:
            return False
        if datetime.now(timezone.utc) >= self._news_mode_until:
            logger.info("NEWS_MODE DEACTIVATED — 30-minute window elapsed")
            self._news_mode_until = None
            return False
        return True

    def minutes_remaining_in_news_mode(self) -> float:
        if not self.is_news_mode_active():
            return 0.0
        delta = self._news_mode_until - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds() / 60.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 2: PIVOT POINT FILTER — STRICT BOUNDARY REJECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PivotPointFilter:
    """
    Standard Floor Pivot calculation using previous day's OHLC.
    Strict BLOCK logic:
      - BUY near R1 (within PIVOT_BUFFER_USD) → BLOCK (overextended)
      - SELL near S1 (within PIVOT_BUFFER_USD) → BLOCK (oversold collapse)

    Confluence bonus:
      - BUY bouncing off S1 → high confluence
      - SELL rejecting R1   → high confluence
      - Both in neutral zone → moderate confluence (WARN)
    """

    @staticmethod
    def calculate(high: float, low: float, close: float) -> Dict[str, float]:
        pivot = (high + low + close) / 3.0
        r1 = (2.0 * pivot) - low
        s1 = (2.0 * pivot) - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        return {"pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2}

    @staticmethod
    def validate(
        signal_type: str,
        entry_price: float,
        pivots: Dict[str, float],
        buffer: float = PIVOT_BUFFER_USD
    ) -> Tuple[Gate, str, float]:
        """
        Returns: (Gate, reason, confluence_score 0-1)

        BUY rules:
          BLOCK  — entry is within `buffer` USD of R1 (overextended into resistance)
          PASS   — entry is within `buffer` USD of S1 (support bounce — ideal)
          WARN   — entry is in neutral zone between S1+buffer and R1-buffer

        SELL rules:
          BLOCK  — entry is within `buffer` USD of S1 (oversold, collapsing into support)
          PASS   — entry is within `buffer` USD of R1 (resistance rejection — ideal)
          WARN   — entry is in neutral zone
        """
        r1 = pivots['r1']
        s1 = pivots['s1']
        dist_r1 = abs(entry_price - r1)
        dist_s1 = abs(entry_price - s1)

        if signal_type == 'BUY':
            # STRICT: Block if overextended near R1
            if dist_r1 <= buffer:
                return (
                    Gate.BLOCK,
                    f"BUY BLOCKED — Entry {entry_price} is within ${dist_r1:.2f} of R1 ({r1:.2f}). Overextended into resistance.",
                    0.0
                )
            # Ideal: S1 bounce
            if dist_s1 <= buffer:
                return (
                    Gate.PASS,
                    f"BUY PASS — Strong S1 bounce confluence. Entry {entry_price} within ${dist_s1:.2f} of S1 ({s1:.2f}).",
                    0.9
                )
            # Neutral zone
            return (
                Gate.WARN,
                f"BUY WARN — Neutral zone. R1={r1:.2f} (${dist_r1:.2f} away), S1={s1:.2f} (${dist_s1:.2f} away).",
                0.5
            )

        else:  # SELL
            # STRICT: Block if oversold near S1
            if dist_s1 <= buffer:
                return (
                    Gate.BLOCK,
                    f"SELL BLOCKED — Entry {entry_price} is within ${dist_s1:.2f} of S1 ({s1:.2f}). Collapsing into support.",
                    0.0
                )
            # Ideal: R1 rejection
            if dist_r1 <= buffer:
                return (
                    Gate.PASS,
                    f"SELL PASS — Strong R1 rejection confluence. Entry {entry_price} within ${dist_r1:.2f} of R1 ({r1:.2f}).",
                    0.9
                )
            # Neutral zone
            return (
                Gate.WARN,
                f"SELL WARN — Neutral zone. S1={s1:.2f} (${dist_s1:.2f} away), R1={r1:.2f} (${dist_r1:.2f} away).",
                0.5
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILTER 3: RSI MOMENTUM FILTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RSIFilter:
    """
    RSI momentum guard using Wilder's smoothing (proper EMA method).
    Thresholds: Overbought=75, Oversold=25 (tighter than default 70/30).

    BUY  → BLOCK if RSI > 75 (overbought market top)
    SELL → BLOCK if RSI < 25 (oversold market bottom)
    """

    @staticmethod
    def validate_buy(rsi: float) -> Tuple[Gate, str, float]:
        if rsi > RSI_OVERBOUGHT:
            return Gate.BLOCK, f"BUY BLOCKED — RSI {rsi:.1f} > {RSI_OVERBOUGHT} (overbought).", 0.0
        if rsi < RSI_OVERSOLD:
            return Gate.PASS, f"BUY PASS — RSI {rsi:.1f} < {RSI_OVERSOLD} (deeply oversold, strong reversal setup).", 0.95
        score = round((RSI_OVERBOUGHT - rsi) / RSI_OVERBOUGHT, 3)
        return Gate.PASS, f"BUY PASS — RSI {rsi:.1f} in normal range.", score

    @staticmethod
    def validate_sell(rsi: float) -> Tuple[Gate, str, float]:
        if rsi < RSI_OVERSOLD:
            return Gate.BLOCK, f"SELL BLOCKED — RSI {rsi:.1f} < {RSI_OVERSOLD} (oversold).", 0.0
        if rsi > RSI_OVERBOUGHT:
            return Gate.PASS, f"SELL PASS — RSI {rsi:.1f} > {RSI_OVERBOUGHT} (deeply overbought, strong reversal setup).", 0.95
        score = round(rsi / 100.0, 3)
        return Gate.PASS, f"SELL PASS — RSI {rsi:.1f} in normal range.", score

    @staticmethod
    def compute_rsi(closes: List[float], period: int = RSI_PERIOD) -> float:
        """
        Compute RSI using Wilder's smoothing (exponential moving average).
        Requires at least period+1 close prices. Returns 50.0 if insufficient data.
        """
        if len(closes) < period + 1:
            return 50.0

        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOT SIZE ENGINE — DETERMINISTIC BLUEPRINT COMPLIANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def determine_lot_size(confluence_level: int, news_mode_active: bool) -> float:
    """
    Strict lot size selection per Blueprint Iron Rules.

    Args:
        confluence_level: 0–3 (count of PASS filters from Pivot + RSI + Sentiment)
        news_mode_active: True if a high-impact news event is active

    Returns:
        lot_size: one of 0.01, 0.02, 0.03, 0.05 — never outside [0.01, 0.05]

    Rule table:
        news_mode=True        → 0.01  (unconditional, overrides all)
        confluence=0          → 0.01  (no alignment)
        confluence=1          → 0.02  (weak alignment)
        confluence=2          → 0.03  (standard — 2 technical gates pass)
        confluence=3          → 0.05  (full confluence — all gates + news clean)
    """
    if news_mode_active:
        return LOT_NEWS_MODE

    lot_map = {
        0: LOT_NEWS_MODE,
        1: LOT_PARTIAL,
        2: LOT_STANDARD,
        3: LOT_FULL_CONFLUENCE,
    }
    return lot_map.get(min(confluence_level, 3), LOT_NEWS_MODE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MARKET ANALYZER — MAIN PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MarketAnalyzer:
    """
    Async pipeline that:
      1. Consumes news from news_queue → updates NewsSentimentFilter state
      2. Consumes signals from signal_queue → runs all 3 gates
      3. Puts validated signals into validated_queue for the cBot bridge

    Both coroutines run concurrently so news processing never blocks
    signal processing.
    """

    def __init__(
        self,
        signal_queue:    asyncio.Queue,
        news_queue:      asyncio.Queue,
        validated_queue: asyncio.Queue,
        balance_manager  = None,
    ):
        self.signal_queue    = signal_queue
        self.news_queue      = news_queue
        self.validated_queue = validated_queue
        self._balance_mgr    = balance_manager

        self.sentiment_filter = NewsSentimentFilter()
        self.pivot_filter     = PivotPointFilter()
        self.rsi_filter       = RSIFilter()

        self._market_data: Dict = {}
        logger.info(
            "MarketAnalyzer initialized | "
            f"Lot sizing: {'dynamic (live balance)' if balance_manager else 'static schedule'}"
        )

    def update_market_data(self, data: Dict):
        """
        Inject live market data from an external feed (e.g. MetaAPI, CCXT, or cTrader REST).
        Expected keys: high, low, close, rsi_14 (optional if close_history provided),
                       close_history (list of floats for RSI computation).
        """
        self._market_data = data
        logger.debug(f"Market data updated: {data}")

    def _get_rsi(self) -> float:
        if 'rsi_14' in self._market_data:
            return float(self._market_data['rsi_14'])
        history = self._market_data.get('close_history', [])
        if history:
            return RSIFilter.compute_rsi(history)
        logger.warning("No RSI data available — defaulting to 50 (neutral)")
        return 50.0

    def validate_signal(self, raw_signal: Dict) -> Tuple[bool, ValidatedSignal]:
        signal_type = raw_signal.get('signal_type', '').upper()
        entry_price = float(raw_signal.get('entry_price', 0))
        stop_loss   = float(raw_signal.get('stop_loss', 0))
        take_profit = float(raw_signal.get('take_profit', 0))
        confidence  = float(raw_signal.get('confidence_score', 1.0))

        filter_details: List[Dict] = []
        news_mode_active = self.sentiment_filter.is_news_mode_active()

        # ── Gate 1: News Sentiment ──────────────────────────────
        if news_mode_active:
            mins_left = self.sentiment_filter.minutes_remaining_in_news_mode()
            news_gate = FilterResult(
                gate_name="News Sentiment",
                status=Gate.WARN,
                reason=f"NEWS_MODE active — {mins_left:.1f} min remaining. Risk clamped to 0.01 lot.",
                confluence_score=0.3,
            )
        else:
            news_gate = FilterResult(
                gate_name="News Sentiment",
                status=Gate.PASS,
                reason="No active news event. Market conditions normal.",
                confluence_score=1.0,
            )

        filter_details.append({
            "name": news_gate.gate_name,
            "status": news_gate.status.value,
            "reason": news_gate.reason,
            "confluence": news_gate.confluence_score,
            "news_mode": news_mode_active,
        })

        # ── Gate 2: Pivot Point — STRICT BOUNDARY REJECTION ────
        md = self._market_data
        pivots = {}
        if all(k in md for k in ('high', 'low', 'close')):
            pivots = PivotPointFilter.calculate(
                float(md['high']), float(md['low']), float(md['close'])
            )
            pivot_status, pivot_reason, pivot_conf = PivotPointFilter.validate(
                signal_type, entry_price, pivots
            )
        else:
            logger.warning("Missing OHLC data for pivot calculation — pivot gate bypassed (WARN)")
            pivot_status = Gate.WARN
            pivot_reason = "No OHLC data available for pivot calculation."
            pivot_conf   = 0.5

        filter_details.append({
            "name": "Pivot Points",
            "status": pivot_status.value,
            "reason": pivot_reason,
            "confluence": pivot_conf,
            "pivots": pivots,
        })

        # ── Gate 3: RSI Momentum ────────────────────────────────
        rsi = self._get_rsi()
        if signal_type == 'BUY':
            rsi_status, rsi_reason, rsi_conf = RSIFilter.validate_buy(rsi)
        else:
            rsi_status, rsi_reason, rsi_conf = RSIFilter.validate_sell(rsi)

        filter_details.append({
            "name": "RSI Momentum",
            "status": rsi_status.value,
            "reason": rsi_reason,
            "confluence": rsi_conf,
            "rsi_14": rsi,
        })

        # ── Final Decision ──────────────────────────────────────
        all_gates = [news_gate.status, pivot_status, rsi_status]
        blocked = any(g == Gate.BLOCK for g in all_gates)

        # Confluence counts only technical PASS gates (Pivot + RSI)
        tech_pass_count = sum(
            1 for g in [pivot_status, rsi_status] if g == Gate.PASS
        )
        # Full confluence requires all three including clean news
        if not news_mode_active:
            confluence_level = tech_pass_count + (1 if not news_mode_active else 0)
        else:
            confluence_level = tech_pass_count

        if self._balance_mgr is not None:
            lot_size = self._balance_mgr.calculate_lot_size(
                entry_price      = entry_price,
                stop_loss        = stop_loss,
                confluence_level = confluence_level,
                news_mode        = news_mode_active,
            )
        else:
            lot_size = determine_lot_size(confluence_level, news_mode_active)
        is_ready = not blocked and confluence_level >= 1

        validated = ValidatedSignal(
            timestamp=datetime.utcnow().isoformat(),
            signal_type=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            confluence_level=confluence_level,
            confidence_score=round(confidence, 3),
            is_ready_for_execution=is_ready,
            filter_details=filter_details,
            news_mode_active=news_mode_active,
        )

        decision = "EXECUTE" if is_ready else "BLOCK"
        logger.info(
            f"[DECISION] {decision} | {signal_type} @ {entry_price} | "
            f"Lot={lot_size} | Confluence={confluence_level}/3 | "
            f"NewsMode={news_mode_active}"
        )
        return is_ready, validated

    # ── Async coroutines ────────────────────────────────────────

    async def process_news_loop(self):
        """Continuously drain the news queue and update sentiment state."""
        logger.info("News processing loop started")
        while True:
            news_text = await self.news_queue.get()
            try:
                sentiment, triggered, reason = self.sentiment_filter.analyze(news_text)
                logger.info(f"[NEWS] Sentiment={sentiment.value} | Triggered={triggered} | {reason}")
            except Exception as e:
                logger.error(f"News processing error: {e}", exc_info=True)
            finally:
                self.news_queue.task_done()

    async def process_signals_loop(self):
        """Continuously drain the signal queue, validate, and forward."""
        logger.info("Signal processing loop started")
        while True:
            raw_signal = await self.signal_queue.get()
            try:
                signal_dict = raw_signal.to_dict() if hasattr(raw_signal, 'to_dict') else raw_signal
                is_valid, validated = self.validate_signal(signal_dict)
                if is_valid:
                    await self.validated_queue.put(validated)
                    logger.info(
                        f"[FORWARD] {validated.signal_type} @ {validated.entry_price} "
                        f"| Lot={validated.lot_size} → cBot bridge"
                    )
                else:
                    blocked_gates = [
                        fd['name'] for fd in validated.filter_details
                        if fd['status'] == 'BLOCK'
                    ]
                    logger.warning(f"[BLOCKED] {validated.signal_type} @ {validated.entry_price} | Reason: {blocked_gates}")
            except Exception as e:
                logger.error(f"Signal processing error: {e}", exc_info=True)
            finally:
                self.signal_queue.task_done()

    async def run(self):
        """Run news and signal processing concurrently."""
        await asyncio.gather(
            self.process_news_loop(),
            self.process_signals_loop(),
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STANDALONE TEST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    analyzer = MarketAnalyzer(
        signal_queue=asyncio.Queue(),
        news_queue=asyncio.Queue(),
        validated_queue=asyncio.Queue(),
    )
    analyzer.update_market_data({
        'high': 2460.00,
        'low': 2440.00,
        'close': 2450.00,
        'rsi_14': 38.0,
    })

    tests = [
        {'signal_type': 'BUY',  'entry_price': 2443.5, 'stop_loss': 2439.0, 'take_profit': 2460.0},  # S1 bounce
        {'signal_type': 'BUY',  'entry_price': 2458.5, 'stop_loss': 2454.0, 'take_profit': 2470.0},  # Near R1 → BLOCK
        {'signal_type': 'SELL', 'entry_price': 2450.0, 'stop_loss': 2455.0, 'take_profit': 2440.0},  # Neutral zone
    ]

    for t in tests:
        is_valid, result = analyzer.validate_signal(t)
        print(json.dumps(result.to_dict(), indent=2))
        print()
