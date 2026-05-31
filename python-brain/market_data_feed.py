"""
market_data_feed.py — Live XAUUSD Market Data Feed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches live Gold (GC=F Gold Futures) data via yfinance every 60 seconds.
Exposes MarketSnapshot consumed by MarketAnalyzer and TelegramListener.

Indicators computed here (fed into all 5 analysis filters):
  - Previous day OHLC → Pivot Point calculation
  - RSI(14) Wilder    → RSI Momentum filter
  - ATR(14)           → Volatility filter
  - EMA(50)           → Trend alignment filter
  - Current price     → Entry sanity check in TelegramListener

Free source: yfinance / Yahoo Finance — no API key required.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone
import yfinance as yf
import pandas as pd

logger = logging.getLogger('market_data')

XAUUSD_TICKER     = "GC=F"
DATA_REFRESH_SECS = 60
HISTORY_DAYS      = 70
RSI_PERIOD        = 14
ATR_PERIOD        = 14
EMA_PERIOD        = 50


@dataclass
class MarketSnapshot:
    timestamp:     str   = ""
    prev_high:     float = 0.0
    prev_low:      float = 0.0
    prev_close:    float = 0.0
    rsi_14:        float = 50.0
    atr_14:        float = 10.0
    ema_50:        float = 0.0
    current_price: float = 0.0
    close_history: List[float] = field(default_factory=list)
    is_stale:      bool  = True


def _compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)), 2)


def _compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    if len(df) < period + 1:
        return 10.0
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low']  - df['Close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return round(float(tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]), 4)


def _compute_ema(closes: pd.Series, period: int = EMA_PERIOD) -> float:
    if len(closes) < period:
        return float(closes.iloc[-1]) if len(closes) > 0 else 0.0
    return round(float(closes.ewm(span=period, min_periods=period, adjust=False).mean().iloc[-1]), 4)


class MarketDataFeed:
    def __init__(self):
        self._snapshot = MarketSnapshot()
        self._lock     = asyncio.Lock()
        logger.info("MarketDataFeed initialized (GC=F via yfinance)")

    @property
    def snapshot(self) -> MarketSnapshot:
        return self._snapshot

    async def fetch_once(self) -> bool:
        try:
            loop = asyncio.get_event_loop()
            df   = await loop.run_in_executor(None, self._fetch_yfinance)
            if df is None or len(df) < 20:
                logger.warning("Insufficient data from yfinance")
                return False

            prev   = df.iloc[-2]
            closes = df['Close']
            snap   = MarketSnapshot(
                timestamp     = datetime.now(timezone.utc).isoformat(),
                prev_high     = round(float(prev['High']),  4),
                prev_low      = round(float(prev['Low']),   4),
                prev_close    = round(float(prev['Close']), 4),
                rsi_14        = _compute_rsi(closes),
                atr_14        = _compute_atr(df),
                ema_50        = _compute_ema(closes),
                current_price = round(float(df['Close'].iloc[-1]), 4),
                close_history = [round(float(c), 4) for c in closes.tolist()],
                is_stale      = False,
            )
            async with self._lock:
                self._snapshot = snap
            logger.info(
                f"Market updated | Price={snap.current_price} | RSI={snap.rsi_14} | "
                f"ATR={snap.atr_14} | EMA50={snap.ema_50} | "
                f"PrevH={snap.prev_high} PrevL={snap.prev_low}"
            )
            return True
        except Exception as e:
            logger.error(f"Market data fetch error: {e}", exc_info=True)
            return False

    def _fetch_yfinance(self) -> Optional[pd.DataFrame]:
        df = yf.Ticker(XAUUSD_TICKER).history(
            period=f"{HISTORY_DAYS}d", interval="1d", auto_adjust=True
        )
        return df.dropna() if not df.empty else None

    async def run_forever(self):
        logger.info(f"Market data feed starting (refresh every {DATA_REFRESH_SECS}s)")
        while True:
            await self.fetch_once()
            await asyncio.sleep(DATA_REFRESH_SECS)
