"""
fix_executor.py — MCP Execution Engine (cTrader Local MCP Server)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Replaces the old FIX 4.4 TCP sessions entirely.
All broker communication now goes through the local cTrader MCP server
running at http://127.0.0.1:9876/mcp/

Public interface (unchanged — drop-in replacement for DualAccountFIXExecutor):
  execute_signal(validated_signal)   → dict with result
  modify_position_sl(modification)   → dict with result
  run_forever()                      → keepalive / health-check loop
  stats()                            → dict

MCP tool calls used:
  tools/list          — discover available tools on startup
  open_position       — enter a trade (symbolName, tradeType, volume, stopLoss, takeProfit)
  modify_position     — modify SL/TP on an open position
  close_position      — close by positionId
  get_positions       — list open positions
  get_account_info    — equity / free margin (also used by BalanceManager)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import aiohttp

logger = logging.getLogger('mcp_executor')

MCP_URL          = os.environ.get("MCP_URL", "http://127.0.0.1:9876/mcp/").strip()
MCP_TIMEOUT      = float(os.environ.get("MCP_TIMEOUT_SECS", "8"))
HEALTH_INTERVAL  = 30.0    # seconds between health pings
_JSON_RPC_ID     = 0


def _next_id() -> int:
    global _JSON_RPC_ID
    _JSON_RPC_ID += 1
    return _JSON_RPC_ID


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOW-LEVEL MCP CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MCPClient:
    """Thin async wrapper around the cTrader MCP JSON-RPC HTTP endpoint."""

    def __init__(self, url: str = MCP_URL):
        self.url = url
        self._connected   = False
        self._last_ok     = 0.0
        self._tools: list = []

    @property
    def connected(self) -> bool:
        return self._connected and (time.monotonic() - self._last_ok) < 90

    async def call(self, tool: str, arguments: dict = None) -> Any:
        """
        Call a tool via JSON-RPC tools/call.
        Returns the parsed result content (list of blocks or raw dict).
        Raises on HTTP error or JSON-RPC error.
        """
        payload = {
            "jsonrpc": "2.0",
            "method":  "tools/call",
            "params":  {"name": tool, "arguments": arguments or {}},
            "id":      _next_id(),
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                self.url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=MCP_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")

        result  = data.get("result", {})
        content = result.get("content", [])

        # Parse first text block if present
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except (json.JSONDecodeError, TypeError):
                    return block["text"]

        return result

    async def list_tools(self) -> list:
        """Discover available MCP tools and cache them."""
        payload = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": _next_id()}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                self.url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=MCP_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        self._tools = data.get("result", {}).get("tools", [])
        return self._tools

    async def ping(self) -> bool:
        """Quick liveness check via get_account_info."""
        try:
            await self.call("get_account_info")
            self._connected = True
            self._last_ok   = time.monotonic()
            return True
        except Exception:
            self._connected = False
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP EXECUTOR  (drop-in for DualAccountFIXExecutor)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MCPExecutor:
    """
    Executes trades via the cTrader Local MCP Server.
    Exposes the same public interface as the old DualAccountFIXExecutor
    so no other module needs to change its call sites.
    """

    def __init__(self, balance_manager=None, channel_reporter=None):
        self._client        = MCPClient(MCP_URL)
        self._balance_mgr   = balance_manager
        self._channel       = channel_reporter
        self._executed      = 0
        self._rejected      = 0
        self._running       = False

        # Maps label → positionId so BreakEvenMonitor can modify/close
        self._positions: Dict[str, str] = {}

        logger.info(f"MCPExecutor ready | MCP endpoint: {MCP_URL}")

    # ── Internal MCP helpers ────────────────────────────

    async def _open_position(
        self,
        symbol:    str,
        direction: str,
        volume:    float,
        sl:        float,
        tp:        float,
        label:     str,
    ) -> Optional[str]:
        """
        Call open_position MCP tool.
        Returns the positionId string on success, None on failure.
        """
        args = {
            "symbolName": symbol,
            "tradeType":  direction.upper(),
            "volume":     volume,
            "stopLoss":   sl,
            "takeProfit": tp,
            "label":      label,
        }
        try:
            result = await self._client.call("open_position", args)
            pos_id = None
            if isinstance(result, dict):
                pos_id = str(
                    result.get("positionId") or
                    result.get("position_id") or
                    result.get("id") or ""
                )
            elif isinstance(result, str):
                pos_id = result.strip()

            if pos_id:
                self._positions[label] = pos_id
                self._client._connected = True
                self._client._last_ok   = time.monotonic()
                logger.info(
                    f"[MCP] OPENED {direction} {volume}L XAUUSD @ market | "
                    f"SL={sl} TP={tp} | positionId={pos_id} | label={label}"
                )
            else:
                logger.warning(f"[MCP] open_position returned no positionId | raw={result!r}")
            return pos_id

        except Exception as e:
            logger.error(f"[MCP] open_position failed: {e}")
            return None

    async def _modify_position(
        self,
        position_id: str,
        new_sl:      float,
        new_tp:      float,
    ) -> bool:
        """Call modify_position MCP tool. Returns True on success."""
        args = {
            "positionId": position_id,
            "stopLoss":   new_sl,
            "takeProfit": new_tp,
        }
        try:
            await self._client.call("modify_position", args)
            logger.info(
                f"[MCP] MODIFIED positionId={position_id} | "
                f"newSL={new_sl} | newTP={new_tp}"
            )
            return True
        except Exception as e:
            logger.error(f"[MCP] modify_position failed: {e}")
            return False

    async def _close_position(self, position_id: str) -> bool:
        """Call close_position MCP tool. Returns True on success."""
        try:
            await self._client.call("close_position", {"positionId": position_id})
            logger.info(f"[MCP] CLOSED positionId={position_id}")
            return True
        except Exception as e:
            logger.error(f"[MCP] close_position failed: {e}")
            return False

    # ── Public interface (same as old DualAccountFIXExecutor) ───

    async def execute_signal(self, validated_signal) -> Dict:
        """
        Open a position via MCP. Returns a result dict.
        The 'live' key carries the positionId for BreakEvenMonitor.
        """
        sd = (
            validated_signal.to_dict()
            if hasattr(validated_signal, 'to_dict')
            else validated_signal
        )

        direction = sd['signal_type']
        entry     = float(sd['entry_price'])
        sl        = float(sd['stop_loss'])
        tp        = float(sd['take_profit'])
        lots      = float(sd['lot_size'])
        label     = f"GB-{uuid.uuid4().hex[:10].upper()}"

        logger.info(
            f"[MCP EXEC] {direction} XAUUSD @ {entry} | "
            f"Lot={lots} | SL={sl} | TP={tp} | label={label}"
        )

        pos_id = await self._open_position(
            symbol    = "XAUUSD",
            direction = direction,
            volume    = lots,
            sl        = sl,
            tp        = tp,
            label     = label,
        )

        if pos_id:
            self._executed += 1
            result = {"mcp": pos_id, "live": pos_id, "demo": "MCP"}
            if self._channel:
                asyncio.ensure_future(
                    self._channel.report_signal(
                        signal_dict = sd,
                        cl_ord_id   = pos_id,
                        source      = "SIGNALS",
                    )
                )
        else:
            self._rejected += 1
            result = {"mcp": "FAILED", "live": "FAILED", "demo": "MCP"}

        logger.info(f"[MCP EXEC] Result → {result}")
        return result

    async def modify_position_sl(self, modification: dict) -> Dict:
        """
        Modify the SL of an open position (used by BreakEvenMonitor).
        Accepts a modification dict with either:
          - positionId   — direct MCP position ID
          - cl_ord_id_live — label used at open (used to look up positionId)
        """
        new_sl = float(modification['stop_loss'])
        new_tp = float(modification.get('take_profit', 0))

        # Resolve position ID
        pos_id = modification.get('positionId', '')
        if not pos_id:
            label  = modification.get('cl_ord_id_live', '')
            pos_id = self._positions.get(label, '')

        if not pos_id:
            logger.warning(
                f"[MCP BE] Cannot modify — no positionId found. "
                f"modification={modification}"
            )
            return {"mcp": "NO_POSITION_ID", "live": "NO_POSITION_ID", "demo": "MCP"}

        logger.info(
            f"[MCP BE] Moving SL → {new_sl} | positionId={pos_id}"
        )

        ok = await self._modify_position(pos_id, new_sl=new_sl, new_tp=new_tp)
        status = pos_id if ok else "FAILED"
        return {"mcp": status, "live": status, "demo": "MCP"}

    async def close_position_by_label(self, label: str) -> bool:
        """Close a position by its label (used externally if needed)."""
        pos_id = self._positions.get(label, '')
        if not pos_id:
            logger.warning(f"[MCP] close_position_by_label: no positionId for label={label!r}")
            return False
        return await self._close_position(pos_id)

    def register_position_id(self, label: str, position_id: str):
        """Allow ChannelReporter or other components to register a position ID."""
        self._positions[label] = position_id
        logger.debug(f"[MCP] Registered label={label} → positionId={position_id}")

    async def run_forever(self):
        """
        Keepalive loop: pings the MCP server every HEALTH_INTERVAL seconds,
        logs the connection status, and discovers available tools on startup.
        """
        self._running = True

        # Startup: discover tools
        logger.info(f"[MCP] Connecting to cTrader MCP server at {MCP_URL}")
        try:
            tools = await self._client.list_tools()
            names = [t.get('name', '?') for t in tools]
            logger.info(f"[MCP] Available tools ({len(names)}): {names}")
        except Exception as e:
            logger.warning(f"[MCP] tools/list failed — cTrader may not be running yet: {e}")

        # Health ping loop
        while self._running:
            ok = await self._client.ping()
            if ok:
                logger.info(
                    f"[MCP] ✓ Connected | "
                    f"Executed={self._executed} | Rejected={self._rejected} | "
                    f"Open positions tracked={len(self._positions)}"
                )
            else:
                logger.warning(
                    f"[MCP] ✗ cTrader MCP server unreachable ({MCP_URL}) — "
                    f"is cTrader running with MCP enabled on port 9876?"
                )
            await asyncio.sleep(HEALTH_INTERVAL)

    def stats(self) -> dict:
        return {
            "executed":       self._executed,
            "rejected":       self._rejected,
            "mcp_connected":  self._client.connected,
            "live_logged_in": self._client.connected,
            "demo_logged_in": False,
            "open_positions": len(self._positions),
        }


# Backward-compat alias — code that imports DualAccountFIXExecutor still works
DualAccountFIXExecutor = MCPExecutor
