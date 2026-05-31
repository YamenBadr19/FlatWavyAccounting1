---
name: Gold Blueprint Architecture
description: Key design decisions for the Gold Blueprint Trading System (Python Brain)
---

# Gold Blueprint Architecture

## Core design rule
All 7 coroutines boot in a single `asyncio.gather()` in `main.py`. Never serialize them.

## Telegram auth
- Production: `TELEGRAM_STRING_SESSION` env var → `StringSession(string)`, fully silent
- Local dev: env var absent → file session + interactive phone/SMS prompt
- `generate_session.py` at repo root generates the string session on any machine

**Why:** Replit/cloud environments have no terminal for interactive auth. StringSession is the only reliable cloud-safe option for Telethon userbots.

## FIX execution
- Two `AsyncFIXSession` instances (LIVE + DEMO) inside `DualAccountFIXExecutor`
- `execute_signal()` fires both via `asyncio.gather()` — exact same millisecond
- `modify_position_sl()` fires SL modification on both — used by `BreakEvenMonitor`
- Raw asyncio TCP sockets + hand-built FIX 4.4 framing (no external FIX framework)
- Custom cTrader SL/TP tags: 9001 = SL, 9002 = TP

**Why:** No heavyweight quickfix library dependency; keeps the Docker/Nix footprint minimal.

## Signal direction logic (telegram_listener.py)
Priority waterfall: math logic → regex group → text keyword → entity scan
- Math: if TP1 > entry → BUY; if TP1 < entry → SELL
- Cross-check: if math direction ≠ keyword direction → DROP (mismatch logged)

**Why:** Channel uses custom animated emojis (not decodable) — math is the only reliable source.

## Spread padding
- Every parsed SL is padded by $2.00 (20 pips on Gold) per channel rule
- BUY: SL_final = SL_raw − 2.00; SELL: SL_final = SL_raw + 2.00
- Applied in `SignalParser._apply_spread_padding()` before logic re-validation

## Entry sanity check
- `abs(parsed_entry − live_spot_price) > $20.00` → signal discarded
- Live spot comes from `MarketDataFeed.snapshot.current_price` (GC=F via yfinance)
- Disabled automatically if feed is stale (`is_stale=True`)

## Break-even monitor
- `BreakEvenMonitor` polls every 5s via `MarketDataFeed.snapshot.current_price`
- Fires `DualAccountFIXExecutor.modify_position_sl()` when price hits TP1 or TP2
- Uses FIX 35=G (OrderCancelReplaceRequest) with new StopPx = entry price

## 5 analysis filters (market_analyzer.py)
1. News Mode (ForexFactory calendar ±30 min → 0.01 lot cap)
2. Pivot Point boundary rejection (R1/S1 ± $2.00 buffer)
3. RSI(14) Wilder — block if >75 BUY or <25 SELL
4. ATR(14) volatility guard — block if >$30 or <$2
5. EMA(50) trend alignment — WARN (reduces confluence) if counter-trend

Lot sizing: news_mode→0.01 always; confluence 0/1/2/3-4/5 → 0.01/0.02/0.03/0.04/0.05

## Files that must never be committed
`.gitignore` protects: `config.py`, `*.session`, `session_backup.txt`,
`signal_audit.jsonl`, `*.log`, `signal_latest.*`
