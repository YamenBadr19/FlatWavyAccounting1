"""
signal_queue.py — Execution Bridge & Signal Queue Management
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Manages signal validation and execution relay.

USAGE:
  from signal_queue import ExecutionBridge
  bridge = ExecutionBridge(validated_queue=q, ...)
  await bridge.relay_loop()
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger('signal_queue')


class ExecutionBridge:
    """
    Relay validated signals to execution engine.
    Maintains audit log of all executions.
    """

    def __init__(self, validated_queue=None, fix_executor=None, channel_reporter=None):
        self.validated_queue = validated_queue
        self.fix_executor = fix_executor
        self.channel_reporter = channel_reporter
        self._executed = 0
        self._rejected = 0
        self._audit_log = Path("signal_audit.jsonl")
        logger.info("ExecutionBridge initialized")

    async def relay_loop(self):
        """
        Relay signals from queue to executor.
        """
        logger.info("ExecutionBridge relay started")
        while True:
            try:
                signal = await asyncio.wait_for(
                    self.validated_queue.get(),
                    timeout=60.0
                )
                await self._execute_signal(signal)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"Relay error: {e}")
                await asyncio.sleep(1)

    async def _execute_signal(self, signal: dict):
        """
        Execute a validated trading signal.
        """
        try:
            if not self.fix_executor.get_account_state().is_healthy():
                logger.warning(f"Account unhealthy — rejected signal")
                self._rejected += 1
                return

            result = await self.fix_executor.open_position(
                symbol=signal.get('symbol'),
                buy=signal.get('buy'),
                volume=signal.get('volume'),
                entry_price=signal.get('entry_price'),
                stop_loss=signal.get('stop_loss'),
                take_profit=signal.get('take_profit'),
                trailing_stop=signal.get('trailing_stop', True),
            )

            if 'position_id' in result:
                self._executed += 1
                # Log to audit file
                signal['relay_ts'] = datetime.now(timezone.utc).isoformat()
                signal['relay_status'] = 'executed'
                self._audit_log.write_text(
                    json.dumps(signal) + "\n",
                    mode='a'
                )
                logger.info(f"✓ Signal executed: {result['position_id']}")
            else:
                self._rejected += 1
                logger.warning(f"Execution failed: {result}")
        except Exception as e:
            self._rejected += 1
            logger.error(f"Signal execution error: {e}")

    def stats(self) -> dict:
        """Get execution statistics."""
        return {
            'executed': self._executed,
            'rejected': self._rejected,
        }
