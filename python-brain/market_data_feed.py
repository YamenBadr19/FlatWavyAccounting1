"""
market_data_feed.py — Live XAUUSD Market Data Feed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches live Gold (XAUUSD=X) data via yfinance every DATA_REFRESH_SECS seconds.
Exposes a MarketSnapshot dataclass consumed by MarketAnalyzer.

Computed indicators (all calculated here, not injected manually):
  - Previous day OHLC     → Pivot Point filter inputs
  - RSI(14) via Wilder    → RSI Momentum filter
  - ATR(14)               → Volatility filter
  - EMA(50)               → Trend alignment filter
  - Current spot price    → Real-time entry comparison

Primary source: yfinance (Yahoo Finance) — free, no API key required.
Fallback:       Cached last-known snapshot if the fetch fails.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone
import yfinance as yf
import pandas as pd

logger = logging.getLogger('market_data')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

XAUUSD_TICKER       = "GC=F"        # Gold Futures (most liquid Gold price)
DATA_REFRESH_SECS   = 60            # Refresh every 60 seconds
HISTORY_DAYS        = 70            # Enough for EMA(50) + RSI(14) + ATR(14)
RSI_PERIOD          = 14
ATR_PERIOD          = 14
EMA_PERIOD          = 50


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class MarketSnapshot:
    """Complete market state snapshot for all 5 filter gates."""
    timestamp: str = ""

    # Previous day OHLC (for Pivot calculation)
    prev_high:  float = 0.0
    prev_low:   float = 0.0
    prev_close: float = 0.0

    # Technical indicators
    rsi_14:     float = 50.0    # Neutral default
    atr_14:     float = 10.0    # Default moderate volatility
    ema_50:     float = 0.0

    # Current spot price
    current_price: float = 0.0

    # Close price series (for RSI recomputation if needed)
    close_history: List[float] = field(default_factory=list)

    # Feed health
    is_stale: bool = True       # True until first successful fetch


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INDICATOR COMPUTATIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    """Wilder's RSI using pandas EWM (exponential weighted mean)."""
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """Average True Range using Wilder's smoothing."""
    if len(df) < period + 1:
        return 10.0
    high  = df['High']
    low   = df['Low']
    close = df['Close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    return round(float(atr), 4)


def _compute_ema(closes: pd.Series, period: int = EMA_PERIOD) -> float:
    """Exponential Moving Average."""
    if len(closes) < period:
        return float(closes.iloc[-1]) if len(closes) > 0 else 0.0
    ema = closes.ewm(span=period, min_periods=period, adjust=False).mean().iloc[-1]
    return round(float(ema), 4)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIVE DATA FEED
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MarketDataFeed:
    """
    Async feed that refreshes XAUUSD market data every DATA_REFRESH_SECS seconds.
    The latest MarketSnapshot is always available via .snapshot property.
    """

    def __init__(self):
        self._snapshot = MarketSnapshot()
        self._lock = asyncio.Lock()
        logger.info("MarketDataFeed initialized (source: yfinance / Gold Futures GC=F)")

    @property
    def snapshot(self) -> MarketSnapshot:
        return self._snapshot

    async def fetch_once(self) -> bool:
        """Fetch and compute one snapshot. Returns True on success."""
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, self._fetch_yfinance)

            if df is None or len(df) < 20:
                logger.warning("Insufficient data returned from yfinance")
                return False

            # Previous day candle (index -2 to avoid partial current day)
            prev = df.iloc[-2]

            # Indicators
            closes      = df['Close']
            rsi         = _compute_rsi(closes)
            atr         = _compute_atr(df)
            ema         = _compute_ema(closes)
            current     = float(df['Close'].iloc[-1])

            snap = MarketSnapshot(
                timestamp     = datetime.now(timezone.utc).isoformat(),
                prev_high     = round(float(prev['High']),  4),
                prev_low      = round(float(prev['Low']),   4),
                prev_close    = round(float(prev['Close']), 4),
                rsi_14        = rsi,
                atr_14        = atr,
                ema_50        = ema,
                current_price = round(current, 4),
                close_history = [round(float(c), 4) for c in closes.tolist()],
                is_stale      = False,
            )

            async with self._lock:
                self._snapshot = snap

            logger.info(
                f"Market data updated | Price={snap.current_price} | "
                f"RSI={snap.rsi_14} | ATR={snap.atr_14} | EMA50={snap.ema_50} | "
                f"PrevH={snap.prev_high} PrevL={snap.prev_low} PrevC={snap.prev_close}"
            )
            return True

        except Exception as e:
            logger.error(f"Market data fetch error: {e}", exc_info=True)
            return False

    def _fetch_yfinance(self) -> Optional[pd.DataFrame]:
        """Blocking yfinance call — runs in executor thread."""
        ticker = yf.Ticker(XAUUSD_TICKER)
        df = ticker.history(period=f"{HISTORY_DAYS}d", interval="1d", auto_adjust=True)
        if df.empty:
            return None
        return df.dropna()

    async def run_forever(self):
        """Continuously refresh market data at DATA_REFRESH_SECS interval."""
        logger.info(f"Market data feed starting (refresh every {DATA_REFRESH_SECS}s)")
        while True:
            await self.fetch_once()
            await asyncio.sleep(DATA_REFRESH_SECS)
