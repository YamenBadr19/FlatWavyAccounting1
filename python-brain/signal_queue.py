"""
signal_queue.py — FIX Execution Bridge
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Drains validated signals from MarketAnalyzer and passes them to
DualAccountFIXExecutor for simultaneous Live + Demo execution.

Also maintains a JSONL audit trail and enforces signal TTL
(stale signals are discarded, never executed).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import asdict

from fix_executor import DualAccountFIXExecutor

logger = logging.getLogger('bridge')

AUDIT_LOG_FILE      = Path("signal_audit.jsonl")
MAX_SIGNAL_AGE_SECS = 30


class ExecutionBridge:
    """
    Reads validated signals from validated_queue, enforces TTL,
    writes audit record, then calls DualAccountFIXExecutor.execute_signal().
    """

    def __init__(self, validated_queue: asyncio.Queue, fix_executor: DualAccountFIXExecutor):
        self.validated_queue = validated_queue
        self.fix_executor    = fix_executor
        self._relay_count    = 0
        self._stale_count    = 0
        logger.info("ExecutionBridge ready")

    async def relay_loop(self):
        logger.info("Execution relay loop started")
        while True:
            validated = await self.validated_queue.get()
            try:
                signal_dict = validated.to_dict() if hasattr(validated, 'to_dict') else asdict(validated)

                # TTL check
                try:
                    ts = datetime.fromisoformat(signal_dict['timestamp'])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age > MAX_SIGNAL_AGE_SECS:
                        logger.warning(
                            f"[STALE] Signal {signal_dict['signal_type']} @ "
                            f"{signal_dict['entry_price']} is {age:.0f}s old — discarded"
                        )
                        self._stale_count += 1
                        self._append_audit(signal_dict, "stale_discarded", {})
                        continue
                except Exception as e:
                    logger.warning(f"TTL check failed: {e}")

                # Execute on both accounts simultaneously
                results = await self.fix_executor.execute_signal(validated)
                self._relay_count += 1
                self._append_audit(signal_dict, "executed", results)

            except Exception as e:
                logger.error(f"Relay error: {e}", exc_info=True)
            finally:
                self.validated_queue.task_done()

    def _append_audit(self, signal_dict: dict, status: str, results: dict):
        try:
            record = {
                **signal_dict,
                "relay_status": status,
                "relay_ts":     datetime.utcnow().isoformat(),
                "fix_results":  results,
            }
            with open(AUDIT_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')
        except OSError as e:
            logger.error(f"Audit log write failed: {e}")

    def stats(self) -> dict:
        return {
            "executed":  self._relay_count,
            "stale":     self._stale_count,
            **self.fix_executor.stats(),
        }
