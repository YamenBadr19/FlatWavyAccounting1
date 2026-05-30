# Trading Logic Reference

## Three-Gate Validation Pipeline

Every signal from Telegram passes through 3 gates **in sequence** before being forwarded to the cBot. A single BLOCK from any gate kills the signal.

```
Telegram Signal
      │
      ▼
┌─────────────────────────────┐
│  Gate 1: News Sentiment     │  BLOCK if News_Mode active with volatile
│  (NewsSentimentFilter)      │  conditions. News_Mode = 30min window.
└─────────────┬───────────────┘
              │ PASS / WARN
              ▼
┌─────────────────────────────┐
│  Gate 2: Pivot Points       │  STRICT REJECTION:
│  (PivotPointFilter)         │  BUY near R1  → BLOCK (overextended)
│                             │  SELL near S1 → BLOCK (oversold)
└─────────────┬───────────────┘
              │ PASS / WARN
              ▼
┌─────────────────────────────┐
│  Gate 3: RSI Momentum       │  BLOCK if RSI > 75 for BUY (overbought)
│  (RSIFilter)                │  BLOCK if RSI < 25 for SELL (oversold)
└─────────────┬───────────────┘
              │ PASS
              ▼
        Lot Size Engine
              │
              ▼
       C# cBot Execution
```

---

## Pivot Point Boundary Rejection (Strict)

**Pivot Formula** (previous day OHLC):
```
Pivot = (High + Low + Close) / 3
R1    = (2 × Pivot) - Low
S1    = (2 × Pivot) - High
R2    = Pivot + (High - Low)
S2    = Pivot - (High - Low)
```

**Buffer Zone**: `PIVOT_BUFFER_USD = $2.00`

| Signal | Entry Location | Decision | Reason |
|--------|---------------|----------|--------|
| BUY    | Within $2 of R1 | **BLOCK** | Overextended into resistance |
| BUY    | Within $2 of S1 | **PASS** (0.9 score) | S1 support bounce — ideal entry |
| BUY    | Neutral zone | **WARN** (0.5 score) | No strong confluece |
| SELL   | Within $2 of S1 | **BLOCK** | Oversold, collapsing into support |
| SELL   | Within $2 of R1 | **PASS** (0.9 score) | R1 rejection — ideal entry |
| SELL   | Neutral zone | **WARN** (0.5 score) | No strong confluence |

---

## RSI Thresholds

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Overbought | RSI > 75 | BLOCK all BUY orders |
| Oversold   | RSI < 25 | BLOCK all SELL orders |
| Strong BUY setup | RSI < 25 | PASS with score 0.95 (mean-reversion) |
| Strong SELL setup | RSI > 75 | PASS with score 0.95 (mean-reversion) |

---

## Lot Size Envelope (Strict Blueprint Rules)

| Condition | Lot Size | Description |
|-----------|----------|-------------|
| News_Mode active | **0.01** | Capital Preservation — ALWAYS, no exceptions |
| No confluence (0 gates) | **0.01** | No alignment — minimum risk |
| 1 technical gate passing | **0.02** | Partial confluence |
| 2 technical gates passing | **0.03** | Standard confluence — typical execution |
| All 3 gates passing (full) | **0.05** | Full Confluence — signal + pivot + clean news |

**The cBot enforces these bounds as a hardware-level guard.** Even if Python sends a wrong value, `RiskManager.ClampLotSize()` will snap it back to the valid envelope.

---

## $10 Balance Lock Protocol

Triggered the moment floating profit reaches **+$10.00**:

1. **Action A — Partial Close**: Instantly closes 50% of the open volume.
   - Locks **~$5.00** cash straight into account balance.

2. **Action B — Break-Even**: Moves the Stop Loss on the remaining 50% to the **exact Entry Price**.
   - The trade is now 100% risk-free. Worst case: breaks even.

3. **Trailing Stop Activates**: Post break-even, the remaining position trails the price at `TrailingStopPips` distance.
   - The SL only moves in the favourable direction (ratchet logic — never backward).

---

## Confluence Level Explanation

```
Confluence = count of PASS results across:
  - Pivot filter PASS   (+1)
  - RSI filter PASS     (+1)
  - No News_Mode active (+1)

Total range: 0 – 3
```

| Level | Meaning | Lot |
|-------|---------|-----|
| 0 | No technical alignment | 0.01 |
| 1 | 1 technical filter agrees | 0.02 |
| 2 | 2 technical filters agree | 0.03 |
| 3 | All 3 conditions met | 0.05 |

---

## News Sentiment Scoring

The News filter uses **weighted keyword scoring** (improved over binary keyword counting):

- Each keyword carries a weight (0.5–1.0) reflecting its Gold price impact magnitude
- `bull_score` and `bear_score` are computed as weighted sums
- Sentiment = BULLISH if `bull_score / total ≥ 0.6`, BEARISH if `bear_score / total ≥ 0.6`
- Any score below 0.6 dominance = VOLATILE

**High-impact keywords** (FOMC, CPI, NFP, War, Crisis, etc.) trigger `News_Mode` for 30 minutes regardless of sentiment direction.
