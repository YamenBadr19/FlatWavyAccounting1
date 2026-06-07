---
name: MCP Executor Migration
description: Full replacement of FIX 4.4 TCP sessions with cTrader Local MCP Server HTTP calls; design decisions and compat notes
---

# MCP Executor Migration

## What changed
All broker connectivity replaced: FIX 4.4 SSL TCP → HTTP JSON-RPC to cTrader Local MCP Server.

**MCP endpoint:** `http://127.0.0.1:9876/mcp/` (local only — cTrader must be open)

**Config file:** `mcp.json` at project root (also `.mcp.json` for tools that expect the dot-prefixed version)

## Class design
`MCPClient` — low-level JSON-RPC wrapper (tools/list, tools/call, ping)
`MCPExecutor` — public interface matching old DualAccountFIXExecutor exactly:
  - `execute_signal(validated_signal)` → calls `open_position` MCP tool
  - `modify_position_sl(modification)` → calls `modify_position` MCP tool
  - `run_forever()` → keepalive loop pinging every 30s, logs health
  - `stats()` → returns `executed`, `rejected`, `mcp_connected`, `open_positions`

`DualAccountFIXExecutor = MCPExecutor` alias kept for zero-cascade backward compat.

## Backward compat decisions
- `stats()` returns both `mcp_connected` AND `live_logged_in` (=mcp_connected), `demo_logged_in` (=False) so any code checking old keys still works
- `BreakEvenMonitor.__init__` still accepts `fix_executor` kwarg — internally stored as `self.fix_executor`, calling `modify_position_sl()` which now routes to MCP
- `ControlBot.__init__` still accepts `fix_executor` kwarg internally stored as `self._executor`

## Position ID tracking
`MCPExecutor._positions: Dict[str, str]` maps trade label → cTrader positionId.
Label format: `GB-<10 hex chars>` set at open_position time.
`modify_position_sl()` resolves positionId via `cl_ord_id_live` label lookup.

## MCP tool names used
- `open_position` — params: symbolName, tradeType, volume, stopLoss, takeProfit, label
- `modify_position` — params: positionId, stopLoss, takeProfit
- `close_position` — params: positionId
- `get_positions` — no params (used by /positions Telegram command)
- `get_account_info` — no params (used by BalanceManager every 30s)

**Why:** cTrader FIX SSL handshake times out from cloud (Replit). MCP is localhost-only, zero SSL, always available when cTrader is open.

**How to apply:** Any future broker operation must go through MCPClient.call(). Never reintroduce FIX imports.

## status.py
`collect_fix_status()` is now a shim → delegates to `collect_mcp_status()`.
Status dashboard shows `MCP BROKER (cTrader Local MCP)` section instead of `FIX SESSIONS`.

## Telegram control bot
`/positions` command added: calls `get_positions` MCP tool, renders each position with direction, volume, entry price, live P&L (calculated from MarketDataFeed snapshot if MCP doesn't return it), SL, TP, positionId.
Field names normalised across MCP versions (tries positionId/id, symbolName/symbol, tradeType/direction/side, etc.)
