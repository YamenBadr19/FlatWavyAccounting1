/*
 * RiskManager.cs — Iron Rules Enforcement
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * All lot size decisions from the Python brain are re-validated
 * here as a final hardware-level safety gate.
 *
 * No signal reaches the broker without passing this class.
 *
 * RULES ENFORCED:
 *   1. Lot size is clamped to [MIN_LOT, MAX_LOT] — no exceptions
 *   2. Signal price logic is verified (BUY: SL < Entry < TP, etc.)
 *   3. XAUUSD price sanity check (1000 < price < 3500)
 */

using System;

namespace cAlgo.Robots
{
    public static class RiskManager
    {
        public const double MIN_LOT_SIZE         = 0.01;
        public const double MAX_LOT_SIZE         = 0.05;
        public const double PROFIT_LOCK_THRESHOLD = 10.0;
        public const double XAUUSD_PRICE_MIN     = 1000.0;
        public const double XAUUSD_PRICE_MAX     = 3500.0;

        // Lot sizing constants mirroring Python brain
        public const double LOT_NEWS_MODE       = 0.01;
        public const double LOT_PARTIAL         = 0.02;
        public const double LOT_STANDARD        = 0.03;
        public const double LOT_FULL_CONFLUENCE = 0.05;

        /// <summary>
        /// Clamp an incoming lot size to the valid Blueprint envelope.
        /// This is the last line of defence — even if Python brain sends
        /// a wrong value, the cBot will never trade outside [0.01, 0.05].
        /// </summary>
        public static double ClampLotSize(double requestedLot, double minLot, double maxLot)
        {
            double clamped = Math.Max(minLot, Math.Min(maxLot, requestedLot));
            if (Math.Abs(clamped - requestedLot) > 0.001)
            {
                Console.WriteLine(
                    $"[RISK] Lot clamped: {requestedLot:F2} → {clamped:F2} " +
                    $"(envelope [{minLot:F2}–{maxLot:F2}])"
                );
            }
            return clamped;
        }

        /// <summary>
        /// Determine lot size from confluence level (C# side mirror of Python logic).
        /// Used when the cBot calculates lot size independently.
        /// </summary>
        public static double LotFromConfluence(int confluenceLevel, bool newsMode)
        {
            if (newsMode) return LOT_NEWS_MODE;
            return confluenceLevel switch
            {
                3 => LOT_FULL_CONFLUENCE,
                2 => LOT_STANDARD,
                1 => LOT_PARTIAL,
                _ => LOT_NEWS_MODE,
            };
        }

        /// <summary>
        /// Full signal validation: type, prices, and logic constraints.
        /// Returns false (and prints reason) if the signal should be blocked.
        /// </summary>
        public static bool ValidateSignal(
            string signalType,
            double entryPrice,
            double stopLoss,
            double takeProfit)
        {
            if (signalType != "BUY" && signalType != "SELL")
            {
                Console.WriteLine($"[RISK] Invalid signal type: '{signalType}'");
                return false;
            }

            foreach (double price in new[] { entryPrice, stopLoss, takeProfit })
            {
                if (price < XAUUSD_PRICE_MIN || price > XAUUSD_PRICE_MAX)
                {
                    Console.WriteLine(
                        $"[RISK] Price {price} outside valid XAUUSD range " +
                        $"[{XAUUSD_PRICE_MIN}–{XAUUSD_PRICE_MAX}]"
                    );
                    return false;
                }
            }

            if (signalType == "BUY")
            {
                if (!(stopLoss < entryPrice && entryPrice < takeProfit))
                {
                    Console.WriteLine(
                        $"[RISK] BUY logic violation: SL({stopLoss}) < Entry({entryPrice}) < TP({takeProfit}) not satisfied."
                    );
                    return false;
                }
            }
            else // SELL
            {
                if (!(takeProfit < entryPrice && entryPrice < stopLoss))
                {
                    Console.WriteLine(
                        $"[RISK] SELL logic violation: TP({takeProfit}) < Entry({entryPrice}) < SL({stopLoss}) not satisfied."
                    );
                    return false;
                }
            }

            double slDistance = Math.Abs(entryPrice - stopLoss);
            if (slDistance < 0.5)
            {
                Console.WriteLine($"[RISK] SL distance too tight: {slDistance:F2} USD. Minimum 0.5 USD required.");
                return false;
            }

            return true;
        }
    }
}
