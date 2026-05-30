name=csharp-body/PositionMonitor.cs
/*
 * PositionMonitor.cs - Tracks position lifecycle
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * Monitors profit lock metrics and handles trailing stop validation
 */

using System;
using cAlgo.API;

namespace cAlgo.Robots
{
    public class PositionMonitor
    {
        private readonly Robot _bot;
        private bool _isBreakEvenSet;
        private double _maxFloatingProfit;

        public PositionMonitor(Robot bot)
        {
            _bot = bot;
            Reset();
        }

        public void Reset()
        {
            _isBreakEvenSet = false;
            _maxFloatingProfit = 0;
        }

        /// <summary>
        /// Evaluates current metrics for open positions to log trail boundaries
        /// </summary>
        public void TrackMetrics(Position position)
        {
            if (position == null) return;

            double currentProfit = position.GrossProfitInAccountCurrency;
            if (currentProfit > _maxFloatingProfit)
            {
                _maxFloatingProfit = currentProfit;
            }
        }

        /// <summary>
        /// Business logic wrapper checking if trailing stop parameters match trade trends
        /// </summary>
        public bool ShouldModifyTrailing(Position position, double currentBid, double currentAsk, int offsetPips, double pipSize)
        {
            if (position == null || !_isBreakEvenSet) return false;

            if (position.TradeType == TradeType.Buy)
            {
                double targetStop = currentBid - (offsetPips * pipSize);
                return targetStop > position.StopLoss;
            }
            else
            {
                double targetStop = currentAsk + (offsetPips * pipSize);
                return targetStop < position.StopLoss;
            }
        }

        public void SetBreakEvenActive(bool active)
        {
            _isBreakEvenSet = active;
        }
    }
}