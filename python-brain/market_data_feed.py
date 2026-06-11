"""
market_data_feed.py — Real-time Market Data for XAUUSD & BTCUSD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches live price data and calculates:
  ✓ Price action (OHLC)
  ✓ RSI(14), ATR(14), EMA(50), EMA(200)
  ✓ MACD, Bollinger Bands
  ✓ Volume analysis
  ✓ Trend direction

USAGE:
  from market_data_feed import MarketDataFeed
  feed = MarketDataFeed()
  await feed.run_forever()
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

logger = logging.getLogger('market_data')


@dataclass
class MarketSnapshot:
    """Current market data snapshot."""
    symbol: str
    timestamp: datetime
    current_price: float
    
    # OHLC
    open: float
    high: float
    low: float
    close: float
    prev_open: float
    prev_high: float
    prev_low: float
    prev_close: float
    
    # Indicators
    rsi_14: float
    atr_14: float
    ema_50: float
    ema_200: float
    macd: float
    macd_signal: float
    macd_histogram: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    
    # Volume
    volume: float
    sma_volume: float
    
    # Trend
    trend: str  # "UPTREND", "DOWNTREND", "SIDEWAYS"
    volatility_level: str  # "LOW", "NORMAL", "HIGH"
    
    # History for further analysis
    close_history: List[float]
    high_history: List[float]
    low_history: List[float]


class MarketDataFeed:
    """
    Real-time market data feed for XAUUSD and BTCUSD.
    Updates every 60 seconds.
    """

    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or ["XAUUSD", "BTCUSD"]
        self.snapshots: Dict[str, MarketSnapshot] = {}
        self._running = False
        logger.info(f"MarketDataFeed initialized | Symbols: {self.symbols}")

    @property
    def snapshot(self) -> Optional[MarketSnapshot]:
        """Get latest XAUUSD snapshot (primary symbol)."""
        return self.snapshots.get("XAUUSD")

    async def run_forever(self):
        """
        Main loop: fetch and update market data every 60 seconds.
        """
        self._running = True
        logger.info("MarketDataFeed started")

        while self._running:
            try:
                await self._update_all_symbols()
                await asyncio.sleep(60)  # Update every 60 seconds
            except Exception as e:
                logger.error(f"MarketDataFeed error: {e}")
                await asyncio.sleep(10)

    async def _update_all_symbols(self):
        """
        Fetch and update data for all tracked symbols.
        """
        try:
            import yfinance as yf
            import pandas as pd
            import numpy as np
            from talib import RSI, ATR, EMA, MACD, BBANDS

            for symbol in self.symbols:
                try:
                    await self._fetch_and_calculate(symbol)
                except Exception as e:
                    logger.warning(f"Failed to update {symbol}: {e}")

        except ImportError as e:
            logger.error(f"Required library missing: {e}")
            await asyncio.sleep(30)

    async def _fetch_and_calculate(self, symbol: str):
        """
        Fetch OHLC data and calculate all technical indicators.
        """
        try:
            import yfinance as yf
            import numpy as np
            from talib import RSI, ATR, EMA, MACD, BBANDS

            loop = asyncio.get_event_loop()

            # Fetch 200 days of data for indicator calculation
            ticker = yf.Ticker(self._get_ticker(symbol))
            df = await loop.run_in_executor(
                None,
                lambda: ticker.history(period="200d", interval="1d", auto_adjust=True)
            )

            if df.empty:
                logger.warning(f"No data for {symbol}")
                return

            # Extract OHLCV
            opens = df['Open'].values
            highs = df['High'].values
            lows = df['Low'].values
            closes = df['Close'].values
            volumes = df['Volume'].values if 'Volume' in df else np.zeros(len(df))

            # Current values
            current_price = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) > 1 else current_price
            current_open = float(opens[-1])
            current_high = float(highs[-1])
            current_low = float(lows[-1])

            prev_open = float(opens[-2]) if len(opens) > 1 else current_open
            prev_high = float(highs[-2]) if len(highs) > 1 else current_high
            prev_low = float(lows[-2]) if len(lows) > 1 else current_low

            # Calculate indicators
            rsi = float(RSI(closes, timeperiod=14)[-1]) if len(closes) > 14 else 50.0
            atr = float(ATR(highs, lows, closes, timeperiod=14)[-1]) if len(closes) > 14 else 0.0
            ema50 = float(EMA(closes, timeperiod=50)[-1]) if len(closes) > 50 else current_price
            ema200 = float(EMA(closes, timeperiod=200)[-1]) if len(closes) > 200 else current_price

            # MACD
            if len(closes) > 26:
                macd, signal, histogram = MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)
                macd_val = float(macd[-1])
                macd_signal = float(signal[-1])
                macd_hist = float(histogram[-1])
            else:
                macd_val = macd_signal = macd_hist = 0.0

            # Bollinger Bands
            if len(closes) > 20:
                bb_upper, bb_middle, bb_lower = BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2)
                bb_upper = float(bb_upper[-1])
                bb_middle = float(bb_middle[-1])
                bb_lower = float(bb_lower[-1])
            else:
                bb_upper = current_price * 1.02
                bb_middle = current_price
                bb_lower = current_price * 0.98

            # Volume analysis
            volume = float(volumes[-1]) if len(volumes) > 0 else 0.0
            sma_volume = float(np.mean(volumes[-20:])) if len(volumes) > 20 else volume

            # Trend determination
            trend = "SIDEWAYS"
            if current_price > ema50 > ema200:
                trend = "UPTREND"
            elif current_price < ema50 < ema200:
                trend = "DOWNTREND"

            # Volatility assessment
            volatility_level = "NORMAL"
            if atr > np.mean(highs[-20:] - lows[-20:]) * 1.5:
                volatility_level = "HIGH"
            elif atr < np.mean(highs[-20:] - lows[-20:]) * 0.7:
                volatility_level = "LOW"

            # Create snapshot
            snapshot = MarketSnapshot(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                current_price=current_price,
                open=current_open,
                high=current_high,
                low=current_low,
                close=current_price,
                prev_open=prev_open,
                prev_high=prev_high,
                prev_low=prev_low,
                prev_close=prev_close,
                rsi_14=rsi,
                atr_14=atr,
                ema_50=ema50,
                ema_200=ema200,
                macd=macd_val,
                macd_signal=macd_signal,
                macd_histogram=macd_hist,
                bb_upper=bb_upper,
                bb_middle=bb_middle,
                bb_lower=bb_lower,
                volume=volume,
                sma_volume=sma_volume,
                trend=trend,
                volatility_level=volatility_level,
                close_history=list(closes[-50:]),
                high_history=list(highs[-50:]),
                low_history=list(lows[-50:]),
            )

            self.snapshots[symbol] = snapshot
            logger.info(
                f"📊 {symbol}: ${current_price:.5f} | "
                f"RSI={rsi:.1f} | ATR=${atr:.4f} | "
                f"Trend={trend} | Vol={volatility_level}"
            )

        except Exception as e:
            logger.error(f"Error fetching {symbol} data: {e}")

    def _get_ticker(self, symbol: str) -> str:
        """
        Convert trading symbol to yfinance ticker.
        """
        mapping = {
            "XAUUSD": "GC=F",      # Gold futures
            "BTCUSD": "BTC-USD",   # Bitcoin
        }
        return mapping.get(symbol, symbol)

    async def stop(self):
        """Stop the feed."""
        self._running = False
        logger.info("MarketDataFeed stopped")
