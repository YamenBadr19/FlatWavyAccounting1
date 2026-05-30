/*
 * PositionMonitor.cs — Position Lifecycle Manager
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * Tracks a single open position through its lifecycle:
 *   Phase 1 — Active:      Position open, profit lock not yet triggered
 *   Phase 2 — Locked:      50% closed, SL moved to entry (risk-free)
 *   Phase 3 — Trailing:    Trailing stop active on remaining 50%
 *
 * OPTIMIZATIONS:
 *   - ProfitLocked is a one-way flag (can never revert to false)
 *   - Trailing only moves SL in favourable direction (ratchet logic)
 *   - ModifyPosition calls are rate-limited (max 1 per tick direction change)
 *     to avoid excessive broker API calls on volatile ticks
 */

using System;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public class PositionMonitor
    {
        public Position Position       { get; private set; }
        public TradeType TradeType     => Position.TradeType;

        private bool   _profitLocked     = false;
        private double _lastTrailingSL   = double.NaN;

        public PositionMonitor(Position position)
        {
            Position = position;
        }

        /// <summary>
        /// Check if $10 profit lock should fire.
        /// Once locked, this always returns false (one-time trigger).
        /// </summary>
        public bool ShouldTriggerProfitLock(double thresholdUSD)
        {
            if (_profitLocked || Position == null || !Position.IsOpen)
                return false;

            return Position.GrossProfitInAccountCurrency >= thresholdUSD;
        }

        /// <summary>Mark profit as locked (called after partial close + BE move).</summary>
        public void MarkProfitLocked()
        {
            _profitLocked = true;
            _lastTrailingSL = Position.StopLoss ?? Position.EntryPrice;
        }

        public bool IsProfitLocked() => _profitLocked;

        /// <summary>
        /// Trailing stop: moves SL in the favourable direction only (ratchet).
        /// Only active after profit lock. Skips if new SL would not improve on existing.
        /// </summary>
        public void UpdateTrailingStop(
            double currentPrice,
            int trailingPips,
            double pipSize,
            Robot robot)
        {
            if (!_profitLocked || Position == null || !Position.IsOpen)
                return;

            double trailingDistance = trailingPips * pipSize;
            double newSL;

            if (TradeType == TradeType.Buy)
            {
                newSL = currentPrice - trailingDistance;
                double existingSL = Position.StopLoss ?? double.NegativeInfinity;

                // Only move SL upward (never downward — ratchet rule)
                if (newSL <= existingSL || newSL <= Position.EntryPrice)
                    return;
            }
            else // Sell
            {
                newSL = currentPrice + trailingDistance;
                double existingSL = Position.StopLoss ?? double.PositiveInfinity;

                // Only move SL downward (never upward — ratchet rule)
                if (newSL >= existingSL || newSL >= Position.EntryPrice)
                    return;
            }

            // Avoid redundant API calls if SL hasn't meaningfully changed
            if (!double.IsNaN(_lastTrailingSL) && Math.Abs(newSL - _lastTrailingSL) < pipSize * 0.5)
                return;

            robot.ModifyPosition(Position, newSL, Position.TakeProfit);
            _lastTrailingSL = newSL;
            robot.Print($"[TRAIL] SL → {newSL:F2} | Current price={currentPrice:F2}");
        }
    }
}
