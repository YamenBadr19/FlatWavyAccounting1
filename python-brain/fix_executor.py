"""
fix_executor.py — Legacy MCP Executor (kept for backward compatibility)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses local cTrader MCP server via HTTP.

USAGE:
  from fix_executor import MCPExecutor
  executor = MCPExecutor(balance_manager=bm, ...)
  await executor.run_forever()
"""

import asyncio
import logging

logger = logging.getLogger('fix_executor')


class MCPExecutor:
    """
    Placeholder for legacy MCP implementation.
    Can be replaced with actual implementation if needed.
    """

    def __init__(self, balance_manager=None, channel_reporter=None):
        self.balance_manager = balance_manager
        self.channel_reporter = channel_reporter
        logger.info("MCPExecutor (legacy) initialized")

    async def run_forever(self):
        """Placeholder keepalive."""
        while True:
            await asyncio.sleep(30)
