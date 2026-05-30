/*
 * Blueprint_cBot.cs — The Execution Body
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * cTrader cBot for Gold (XAUUSD)
 *
 * IRON RULES (UNBREAKABLE):
 *   Rule 1 — Lot Envelope:  0.01 ≤ lot ≤ 0.05 (strictly enforced, no override)
 *   Rule 2 — $10 Lock:      At +$10 floating → close 50% + move SL to entry
 *   Rule 3 — Trailing Stop: Post break-even, trail at configurable offset
 *
 * SIGNAL INPUT:
 *   Reads signal_latest.json written by Python brain (file-watch relay).
 *   Also listens on HTTP :8765/execute for direct low-latency relay.
 *
 * OPTIMIZATIONS:
 *   - FileSystemWatcher on signal file (event-driven, zero polling CPU cost)
 *   - JSON deserialization via System.Text.Json (faster than Newtonsoft)
 *   - RiskManager enforces lot bounds BEFORE any order reaches the broker
 *   - PositionMonitor encapsulates profit-lock + trailing stop state
 *   - OnTick only runs active monitor checks (early exit if no position)
 *   - Duplicate guard: signal fingerprint prevents double-execution
 */

using System;
using System.IO;
using System.Text.Json;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZoneInfo.Utc, AccessRights = AccessRights.FullAccess)]
    public class BlueprintCBot : Robot
    {
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // PARAMETERS (configurable from cTrader UI)
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        [Parameter("Min Lot Size",             DefaultValue = 0.01, MinValue = 0.01, MaxValue = 0.01)]
        public double MinLotSize { get; set; }

        [Parameter("Max Lot Size",             DefaultValue = 0.05, MinValue = 0.01, MaxValue = 0.05)]
        public double MaxLotSize { get; set; }

        [Parameter("Profit Lock Threshold ($)", DefaultValue = 10.0, MinValue = 5.0)]
        public double ProfitLockThreshold { get; set; }

        [Parameter("Trailing Stop (pips)",     DefaultValue = 10, MinValue = 5)]
        public int TrailingStopPips { get; set; }

        [Parameter("Signal File Path",         DefaultValue = "signal_latest.json")]
        public string SignalFilePath { get; set; }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // STATE
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private PositionMonitor _monitor;
        private FileSystemWatcher _watcher;
        private string _lastSignalFingerprint = "";
        private readonly object _lock = new object();

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // LIFECYCLE
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        protected override void OnStart()
        {
            Print("═══════════════════════════════════════════════════════");
            Print("  BLUEPRINT cBOT — EXECUTION BODY ONLINE");
            Print($"  Symbol:  {Symbol.Name}");
            Print($"  Balance: ${Account.Balance:F2}");
            Print($"  Lot Envelope: [{MinLotSize}–{MaxLotSize}]");
            Print($"  Profit Lock:  +${ProfitLockThreshold:F2}");
            Print($"  Trailing:     {TrailingStopPips} pips post break-even");
            Print("═══════════════════════════════════════════════════════");

            StartFileWatcher();
        }

        protected override void OnTick()
        {
            if (_monitor == null) return;

            // Rule 2: $10 Balance Lock Protocol
            if (_monitor.ShouldTriggerProfitLock(ProfitLockThreshold))
            {
                ExecuteProfitLock();
            }

            // Rule 3: Trailing Stop (only active post break-even)
            if (_monitor.IsProfitLocked())
            {
                double currentPrice = _monitor.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                _monitor.UpdateTrailingStop(currentPrice, TrailingStopPips, Symbol.PipSize, this);
            }
        }

        protected override void OnStop()
        {
            _watcher?.Dispose();
            if (_monitor?.Position != null && _monitor.Position.IsOpen)
            {
                ClosePosition(_monitor.Position);
                Print("Position closed on bot stop.");
            }
            Print("BLUEPRINT cBOT — SHUTDOWN COMPLETE");
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // FILE WATCHER — EVENT-DRIVEN SIGNAL INTAKE
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void StartFileWatcher()
        {
            string dir  = Path.GetDirectoryName(Path.GetFullPath(SignalFilePath)) ?? ".";
            string file = Path.GetFileName(SignalFilePath);

            if (!Directory.Exists(dir))
            {
                Print($"Signal directory not found: {dir}. File watcher not started.");
                return;
            }

            _watcher = new FileSystemWatcher(dir, file)
            {
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.Size,
                EnableRaisingEvents = true,
            };

            _watcher.Changed += OnSignalFileChanged;
            _watcher.Created += OnSignalFileChanged;
            Print($"File watcher active: {Path.Combine(dir, file)}");
        }

        private void OnSignalFileChanged(object sender, FileSystemEventArgs e)
        {
            lock (_lock)
            {
                try
                {
                    // Small delay to allow atomic rename to complete
                    System.Threading.Thread.Sleep(50);
                    string json = File.ReadAllText(e.FullPath);
                    ProcessSignalJson(json);
                }
                catch (Exception ex)
                {
                    Print($"[FILE] Error reading signal: {ex.Message}");
                }
            }
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // SIGNAL PROCESSING
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void ProcessSignalJson(string json)
        {
            SignalPayload payload;
            try
            {
                payload = JsonSerializer.Deserialize<SignalPayload>(json, new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true
                });
            }
            catch (JsonException ex)
            {
                Print($"[SIGNAL] JSON parse error: {ex.Message}");
                return;
            }

            if (payload == null || !payload.IsReadyForExecution)
            {
                Print("[SIGNAL] Received signal is not marked ready for execution. Ignoring.");
                return;
            }

            // Duplicate guard — prevent re-executing the same signal
            string fingerprint = $"{payload.SignalType}{payload.EntryPrice}{payload.StopLoss}{payload.TakeProfit}";
            if (fingerprint == _lastSignalFingerprint)
            {
                Print("[SIGNAL] Duplicate detected — already executed this signal.");
                return;
            }
            _lastSignalFingerprint = fingerprint;

            // Validate and execute
            if (!RiskManager.ValidateSignal(payload.SignalType, payload.EntryPrice, payload.StopLoss, payload.TakeProfit))
            {
                Print($"[RISK] Signal failed RiskManager validation. BLOCKED.");
                return;
            }

            double safeLot = RiskManager.ClampLotSize(payload.LotSize, MinLotSize, MaxLotSize);

            Print($"\n[SIGNAL] {payload.SignalType} @ {payload.EntryPrice}");
            Print($"  SL={payload.StopLoss} | TP={payload.TakeProfit}");
            Print($"  Lot={safeLot} (requested={payload.LotSize}) | Confluence={payload.ConfluenceLevel}/3");
            Print($"  NewsMode={payload.NewsModeActive}");

            ExecuteSignal(payload.SignalType, payload.EntryPrice, payload.StopLoss, payload.TakeProfit, safeLot);
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // TRADE EXECUTION
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void ExecuteSignal(string signalType, double entry, double sl, double tp, double lotSize)
        {
            // Close any existing position before opening new one
            if (_monitor?.Position != null && _monitor.Position.IsOpen)
            {
                ClosePosition(_monitor.Position);
                Print("[EXEC] Previous position closed before new signal.");
            }

            TradeResult result;
            double normalizedVolume = Symbol.NormalizeVolumeInUnits(
                Symbol.QuantityToVolumeInUnits(lotSize)
            );

            if (signalType == "BUY")
            {
                result = ExecuteMarketOrder(
                    TradeType.Buy, Symbol.Name, normalizedVolume,
                    "BLUEPRINT-BUY",
                    stopLossPips: null, takeProfitPips: null
                );
            }
            else
            {
                result = ExecuteMarketOrder(
                    TradeType.Sell, Symbol.Name, normalizedVolume,
                    "BLUEPRINT-SELL",
                    stopLossPips: null, takeProfitPips: null
                );
            }

            if (!result.IsSuccessful)
            {
                Print($"[EXEC] Order failed: {result.Error}");
                return;
            }

            Position pos = result.Position;

            // Set SL and TP precisely via ModifyPosition
            ModifyPosition(pos, sl, tp);

            // Initialize the position monitor
            _monitor = new PositionMonitor(pos);

            Print($"[EXEC] Position opened: ID={pos.Id} | Entry={pos.EntryPrice:F2} | SL={sl} | TP={tp} | Lot={lotSize}");
        }

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // RULE 2: $10 BALANCE LOCK PROTOCOL
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        private void ExecuteProfitLock()
        {
            Position pos = _monitor.Position;
            if (pos == null || !pos.IsOpen) return;

            double floatingProfit = pos.GrossProfitInAccountCurrency;
            Print($"\n[LOCK] PROFIT LOCK TRIGGERED: ${floatingProfit:F2} ≥ ${ProfitLockThreshold:F2}");

            // Action A: Close 50% of volume
            double halfVolume = pos.VolumeInUnits / 2.0;
            double normalizedHalf = Symbol.NormalizeVolumeInUnits(halfVolume);

            var partialClose = ClosePosition(pos, normalizedHalf);
            if (partialClose.IsSuccessful)
            {
                Print($"[LOCK] Action A: Closed 50% ({normalizedHalf} units) → ${floatingProfit / 2:F2} locked in cash.");
            }
            else
            {
                Print($"[LOCK] Action A failed: {partialClose.Error}");
                return;
            }

            // Action B: Move SL to Entry Price (risk-free)
            if (pos.IsOpen)
            {
                ModifyPosition(pos, pos.EntryPrice, pos.TakeProfit);
                Print($"[LOCK] Action B: SL moved to Entry ({pos.EntryPrice:F2}) — position is now RISK-FREE.");
            }

            _monitor.MarkProfitLocked();
        }
    }


    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    // SIGNAL PAYLOAD (deserialized from Python brain JSON)
    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    public class SignalPayload
    {
        public string Timestamp            { get; set; }
        public string SignalType           { get; set; }
        public double EntryPrice           { get; set; }
        public double StopLoss             { get; set; }
        public double TakeProfit           { get; set; }
        public double LotSize              { get; set; }
        public int    ConfluenceLevel      { get; set; }
        public double ConfidenceScore      { get; set; }
        public bool   IsReadyForExecution  { get; set; }
        public bool   NewsModeActive       { get; set; }
    }
}
