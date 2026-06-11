"""
balance_manager.py — Account Balance & Equity Manager
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors account balance, equity, and margin.
Prevents opening trades if:
  ✗ Balance is 0 or negative
  ✗ Free margin is insufficient
  ✗ Used margin > max allowed

USAGE:
  from balance_manager import BalanceManager
  bm = BalanceManager()
  await bm.run_forever()
"""

import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger('balance_manager')

MIN_REQUIRED_BALANCE = 100.0  # USD
MAX_USED_MARGIN_PERCENT = 80.0  # %
UPDATE_INTERVAL = 30.0  # seconds


@dataclass
class BalanceState:
    """Current account balance state."""
    balance: float  # Account balance
    equity: float   # Balance + unrealized P&L
    free_margin: float  # Available to use
    used_margin: float  # Already used
    used_margin_percent: float  # 0-100%
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def is_healthy(self) -> bool:
        """Check if account is in good state."""
        return (
            self.balance > MIN_REQUIRED_BALANCE and
            self.used_margin_percent < MAX_USED_MARGIN_PERCENT and
            self.free_margin > 0
        )


class BalanceManager:
    """
    Manages account balance and determines if trading is allowed.
    """

    def __init__(self):
        self._state: Optional[BalanceState] = None
        self._running = False
        self._can_trade = False
        logger.info("BalanceManager initialized")

    async def update_from_api(self, balance: float, equity: float):
        """
        Update balance and equity from broker API.
        Called by CTraderOpenAPI.
        """
        used_margin = balance - (equity - balance)  # Rough approximation
        used_margin_percent = (
            (used_margin / balance * 100) if balance > 0 else 0
        )

        self._state = BalanceState(
            balance=balance,
            equity=equity,
            free_margin=balance - used_margin,
            used_margin=max(0, used_margin),
            used_margin_percent=max(0, used_margin_percent),
        )

        self._can_trade = self._state.is_healthy()
        logger.debug(
            f"Balance update: ${balance:.2f} | "
            f"Equity: ${equity:.2f} | "
            f"Used margin: {used_margin_percent:.1f}% | "
            f"Can trade: {self._can_trade}"
        )

    def can_trade(self) -> bool:
        """
        Check if trading is allowed.
        """
        return self._can_trade and self._state is not None

    def get_state(self) -> Optional[BalanceState]:
        """
        Get current balance state.
        """
        return self._state

    def get_max_lot_size(self, risk_percent: float = 1.0) -> float:
        """
        Calculate maximum allowed lot size based on risk management.
        
        Args:
            risk_percent: Maximum % of balance to risk per trade (default 1%)
        
        Returns:
            Maximum lot size
        """
        if not self._state or self._state.balance <= 0:
            return 0.0

        # Risk = Balance × risk_percent / 100
        risk_amount = self._state.balance * (risk_percent / 100.0)
        # Lot size = risk_amount / pips (simplified: 10 pips stop loss)
        max_lot = risk_amount / (10 * 0.01)  # 10 pips

        return max(0.01, min(max_lot, 10.0))  # Cap at 10.0 lots

    async def run_forever(self):
        """
        Periodically log balance status.
        """
        self._running = True
        logger.info("BalanceManager monitoring started")

        while self._running:
            try:
                if self._state:
                    status = "✓ HEALTHY" if self._can_trade else "✗ INSUFFICIENT"
                    logger.info(
                        f"Balance: ${self._state.balance:.2f} | "
                        f"Equity: ${self._state.equity:.2f} | "
                        f"Used margin: {self._state.used_margin_percent:.1f}% | "
                        f"Status: {status}"
                    )
                else:
                    logger.warning("No balance data available")

                await asyncio.sleep(UPDATE_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"BalanceManager error: {e}")
                await asyncio.sleep(10)

        logger.info("BalanceManager stopped")

    async def stop(self):
        """Stop monitoring."""
        self._running = False
