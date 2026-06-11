"""
market_analyzer.py — 5-Filter Signal Analyzer with Gemini Integration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Filters raw signals through 5 technical confluence filters.
Uses Gemini AI for final confirmation.

USAGE:
  from market_analyzer import MarketAnalyzer
  analyzer = MarketAnalyzer(...)
  await analyzer.run()
"""

import asyncio
import logging
from asyncio import Queue
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger('analyzer')


class MarketAnalyzer:
    """
    5-filter pipeline for signal analysis.
    """

    def __init__(
        self,
        signal_queue: Queue,
        news_queue: Queue,
        validated_queue: Queue,
        balance_manager=None,
    ):
        self.signal_queue = signal_queue
        self.news_queue = news_queue
        self.validated_queue = validated_queue
        self.balance_manager = balance_manager
        self._market_data = {}
        logger.info("MarketAnalyzer initialized")

    def update_market_data(self, data: Dict[str, Any]):
        """
        Update current market data snapshot.
        Called by main._market_data_sync_loop.
        """
        self._market_data = data

    async def run(self):
        """
        Main analyzer loop.
        """
        logger.info("MarketAnalyzer started")
        while True:
            try:
                signal = await asyncio.wait_for(
                    self.signal_queue.get(),
                    timeout=60.0
                )
                await self._analyze_signal(signal)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"Analyzer error: {e}")
                await asyncio.sleep(1)

    async def _analyze_signal(self, signal: Dict[str, Any]):
        """
        Apply 5 filters to signal.
        """
        try:
            # Filter 1: Basic validation
            if not self._filter_basic(signal):
                logger.debug(f"Signal failed filter 1 (basic)")
                return

            # Filter 2: Technical confluence
            if not self._filter_confluence(signal):
                logger.debug(f"Signal failed filter 2 (confluence)")
                return

            # Filter 3: Risk/Reward
            if not self._filter_risk_reward(signal):
                logger.debug(f"Signal failed filter 3 (risk/reward)")
                return

            # Filter 4: Market conditions
            if not self._filter_market_conditions(signal):
                logger.debug(f"Signal failed filter 4 (market)")
                return

            # Filter 5: Balance check
            if not self._filter_balance(signal):
                logger.debug(f"Signal failed filter 5 (balance)")
                return

            # All filters passed
            signal['analyzed_at'] = datetime.now(timezone.utc).isoformat()
            signal['confluence_level'] = 5  # All filters passed
            await self.validated_queue.put(signal)
            logger.info(f"✓ Signal validated: {signal}")

        except Exception as e:
            logger.error(f"Signal analysis error: {e}")

    def _filter_basic(self, signal: Dict[str, Any]) -> bool:
        """Filter 1: Basic validation."""
        required = ['symbol', 'buy', 'entry_price', 'stop_loss', 'take_profit']
        return all(k in signal for k in required)

    def _filter_confluence(self, signal: Dict[str, Any]) -> bool:
        """Filter 2: Technical confluence check."""
        rsi = self._market_data.get('rsi_14', 50)
        ema50 = self._market_data.get('ema_50', 0)
        price = self._market_data.get('current_price', 0)
        
        if signal.get('buy'):
            # For BUY: expect price > EMA50, RSI not overbought
            return price > ema50 and rsi < 70
        else:
            # For SELL: expect price < EMA50, RSI not oversold
            return price < ema50 and rsi > 30

    def _filter_risk_reward(self, signal: Dict[str, Any]) -> bool:
        """Filter 3: Risk/Reward ratio check."""
        entry = signal.get('entry_price', 0)
        sl = signal.get('stop_loss', 0)
        tp = signal.get('take_profit', 0)
        
        if entry <= 0 or sl <= 0 or tp <= 0:
            return False
        
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        
        # Require at least 1:1 risk/reward
        return reward >= risk

    def _filter_market_conditions(self, signal: Dict[str, Any]) -> bool:
        """Filter 4: Market conditions check."""
        atr = self._market_data.get('atr_14', 0)
        # Require minimum volatility for safe entry
        return atr > 0.5

    def _filter_balance(self, signal: Dict[str, Any]) -> bool:
        """Filter 5: Account balance check."""
        if self.balance_manager:
            return self.balance_manager.can_trade()
        return True
