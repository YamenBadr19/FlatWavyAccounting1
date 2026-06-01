---
name: XAUUSD Lot Sizing Math
description: Exact formula for risk-based lot sizing on XAUUSD with BalanceManager.
---

## Formula
```
risk_usd  = equity × RISK_PER_TRADE_PCT / 100
lot_size  = risk_usd / (sl_distance_usd × 100)
```

## Why
XAUUSD: 1 standard lot = 100 troy oz.
A $1 price move = $100 P&L per standard lot.
A $1 price move = $1   P&L per 0.01 lot (micro).
Therefore: lot_size = risk_usd / (sl_distance × 100).

## Confluence ceiling map
- confluence=0 → cap at MIN_LOT (0.01)
- confluence=1 → cap at 0.02
- confluence=2 → cap at 0.03
- confluence=3 → cap at MAX_LOT (0.05)

## Example
equity=$10,000 | risk=1% | SL distance=$15
risk_usd = $100
lot_size = 100 / (15×100) = 0.067 → clamped to 0.05 at full confluence

## Balance source priority
1. MCP server at `http://127.0.0.1:9876/mcp/` — polled every 30s (only works locally)
2. FIX execution report balance tags (9003/9004, 9011/9012)
3. DEFAULT_ACCOUNT_EQUITY env var (default $10,000)
