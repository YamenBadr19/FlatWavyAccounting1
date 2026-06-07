"""
BalanceManager — Fully Autonomous Balance & Risk Management
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches live Equity + Free Margin from cTrader via the local MCP server.
Falls back to DEFAULT_ACCOUNT_EQUITY env var when MCP is unreachable.
Calculates lot sizes automatically — zero manual configuration required.

Lot size formula (XAUUSD):
  risk_usd  = equity × RISK_PER_TRADE_PCT / 100
  lot_size  = risk_usd / (sl_distance_usd × 100)

XAUUSD math:
  1 standard lot = 100 troy oz
  $1 price move  = $100 P&L per standard lot
  $1 price move  = $1   P&L per 0.01 lot (micro)
  → lot_size = risk_usd / (sl_distance_usd × 100)

Example (equity=$10,000, risk=1%, SL=$15 away):
  risk_usd = 10,000 × 0.01 = $100
  lot_size = 100 / (15 × 100) = 0.067 → capped at 0.05

MCP server priority:
  1. http://127.0.0.1:9876/mcp/  (local cTrader, polled every 30s)
  2. DEFAULT_ACCOUNT_EQUITY env var (safe fallback, default $10,000)
"""

import asyncio
import logging
import math
import os
import time

logger = logging.getLogger('balance_mgr')


def _ef(k, d):
    try:
        return float(os.environ.get(k, str(d)))
    except (ValueError, TypeError):
        return float(d)


MCP_URL            = os.environ.get("MCP_URL", "http://127.0.0.1:9876/mcp/").strip()
DEFAULT_EQUITY     = _ef("DEFAULT_ACCOUNT_EQUITY", 10_000.0)
RISK_PER_TRADE_PCT = _ef("RISK_PER_TRADE_PCT", 1.0)
MAX_LOT            = _ef("MAX_LOT_SIZE", 0.05)
MIN_LOT            = _ef("MIN_LOT_SIZE", 0.01)
LOT_STEP           = 0.01
MCP_POLL_SECS      = 30.0
MCP_STALE_SECS     = 120.0


class BalanceManager:
    """
    Single source of truth for account balance and risk-based lot sizing.
    Data source: cTrader MCP server → DEFAULT_ACCOUNT_EQUITY fallback.
    """

    def __init__(self):
        self._equity      = DEFAULT_EQUITY
        self._free_margin = DEFAULT_EQUITY
        self._source      = "DEFAULT"
        self._mcp_last_ok = 0.0

        logger.info(
            f"BalanceManager ready | Default equity=${DEFAULT_EQUITY:,.2f} | "
            f"Risk={RISK_PER_TRADE_PCT}% per trade | Lot range=[{MIN_LOT},{MAX_LOT}]"
        )

    # ── Balance update APIs ────────────────────────────────────────

    async def fetch_from_mcp(self) -> bool:
        """
        POST to local cTrader MCP server and parse equity/freeMargin.
        Silent failure is safe — falls back to DEFAULT_ACCOUNT_EQUITY.
        """
        try:
            import aiohttp
            import json as _json
            payload = {
                "jsonrpc": "2.0",
                "method":  "tools/call",
                "params":  {"name": "get_account_info", "arguments": {}},
                "id":      1,
            }
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    MCP_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5.0),
                ) as resp:
                    if resp.status != 200:
                        return False
                    data    = await resp.json(content_type=None)
                    result  = data.get("result", {})
                    content = result.get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            info    = _json.loads(block["text"])
                            equity  = float(info.get("equity", 0))
                            margin  = float(
                                info.get("freeMargin",
                                info.get("free_margin",
                                info.get("freemargin", 0)))
                            )
                            if equity > 0:
                                self._equity      = equity
                                self._free_margin = margin
                                self._source      = "MCP"
                                self._mcp_last_ok = time.monotonic()
                                logger.info(
                                    f"[MCP] Balance → equity=${equity:,.2f}  "
                                    f"free_margin=${margin:,.2f}"
                                )
                                return True
        except Exception as e:
            logger.debug(f"[MCP] Balance unavailable ({type(e).__name__}): {e}")
        return False

    async def run_forever(self):
        """Background coroutine: poll MCP every 30s; gracefully degrades when offline."""
        logger.info(f"BalanceManager polling MCP every {MCP_POLL_SECS:.0f}s ({MCP_URL})")
        while True:
            await self.fetch_from_mcp()
            await asyncio.sleep(MCP_POLL_SECS)

    # ── Lot size calculation ───────────────────────────────────────

    def calculate_lot_size(
        self,
        entry_price:      float,
        stop_loss:        float,
        confluence_level: int  = 3,
        news_mode:        bool = False,
    ) -> float:
        """
        Autonomously calculate lot size from live balance + SL distance.

        news_mode = True  →  always MIN_LOT (capital preservation, non-negotiable)
        Otherwise         →  RISK_PER_TRADE_PCT of equity, bounded to [MIN_LOT, MAX_LOT]

        The confluence_level acts as a CEILING on the calculated lot:
            0  → MIN_LOT cap
            1  → 0.02 cap
            2  → 0.03 cap
            3  → MAX_LOT cap (full risk allowed)
        """
        if news_mode:
            return MIN_LOT

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance < 0.01:
            logger.warning(f"SL distance too small ({sl_distance:.4f}) — using MIN_LOT")
            return MIN_LOT

        risk_usd = self._equity * RISK_PER_TRADE_PCT / 100.0
        raw_lot  = risk_usd / (sl_distance * 100.0)

        conf_ceiling_map = {0: MIN_LOT, 1: 0.02, 2: 0.03, 3: MAX_LOT}
        ceiling = conf_ceiling_map.get(min(confluence_level, 3), MAX_LOT)

        lot = math.floor(raw_lot / LOT_STEP) * LOT_STEP
        lot = round(max(MIN_LOT, min(ceiling, lot)), 2)

        logger.debug(
            f"Lot calc: equity=${self._equity:,.0f}  risk={RISK_PER_TRADE_PCT}%  "
            f"sl_dist={sl_distance:.2f}  raw={raw_lot:.4f}  "
            f"conf_ceil={ceiling}  → {lot}"
        )
        return lot

    # ── Status ─────────────────────────────────────────────────────

    def _mcp_connected(self) -> bool:
        return (time.monotonic() - self._mcp_last_ok) < MCP_STALE_SECS

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def free_margin(self) -> float:
        return self._free_margin

    def status(self) -> dict:
        return {
            "equity":        round(self._equity, 2),
            "free_margin":   round(self._free_margin, 2),
            "risk_pct":      RISK_PER_TRADE_PCT,
            "max_lot":       MAX_LOT,
            "min_lot":       MIN_LOT,
            "mcp_connected": self._mcp_connected(),
            "source":        self._source,
        }
