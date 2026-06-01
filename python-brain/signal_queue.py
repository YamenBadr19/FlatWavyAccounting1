"""
Signal Queue: cBot Bridge
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Drains validated signals from MarketAnalyzer and relays them
to the C# cBot via HTTP WebSocket bridge (cTrader Open API).

The bridge writes signals to a local JSON file watched by the cBot
(file-watch relay) OR POSTs them to the cBot's local HTTP endpoint
if a WebSocket bridge is running.

OPTIMIZATIONS:
  - Non-blocking async relay with configurable timeout
  - Persistent audit log (JSONL — one JSON object per line)
  - Signal TTL: discard signals older than MAX_SIGNAL_AGE_SECONDS
    (prevents stale signals from executing after a backlog clears)
  - Atomic file write: write to .tmp then rename, preventing partial reads
  - Graceful degradation: if cBot HTTP endpoint is unavailable,
    falls back to file-watch relay automatically
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import asdict

logger = logging.getLogger('queue_bridge')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SIGNAL_OUTPUT_FILE  = Path("signal_latest.json")
AUDIT_LOG_FILE      = Path("signal_audit.jsonl")
MAX_SIGNAL_AGE_SECS = 30       # Signals older than 30s are discarded (stale)
CBOT_HTTP_URL       = "http://localhost:8765/execute"  # cBot local bridge endpoint
HTTP_TIMEOUT_SECS   = 3.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIGNAL BRIDGE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SignalBridge:
    """
    Relays ValidatedSignal objects from the Python brain to the C# cBot.

    Relay methods (tried in order):
      1. HTTP POST to cBot local server (lowest latency, ~1ms)
      2. Atomic JSON file write (file-watch fallback, ~5ms disk latency)
    """

    def __init__(self, validated_queue: asyncio.Queue):
        self.validated_queue = validated_queue
        self._http_available = True
        self._relay_count = 0
        self._block_count = 0
        logger.info("SignalBridge ready")

    async def relay_loop(self):
        """Continuously drain validated signals and relay to cBot."""
        logger.info("Signal relay loop started")
        while True:
            validated = await self.validated_queue.get()
            try:
                signal_dict = validated.to_dict() if hasattr(validated, 'to_dict') else asdict(validated)

                # TTL check — discard stale signals
                try:
                    signal_ts = datetime.fromisoformat(signal_dict['timestamp'])
                    if signal_ts.tzinfo is None:
                        signal_ts = signal_ts.replace(tzinfo=timezone.utc)
                    age_secs = (datetime.now(timezone.utc) - signal_ts).total_seconds()
                    if age_secs > MAX_SIGNAL_AGE_SECS:
                        logger.warning(
                            f"[STALE] Discarding signal {signal_dict['signal_type']} @ "
                            f"{signal_dict['entry_price']} — {age_secs:.0f}s old (max {MAX_SIGNAL_AGE_SECS}s)"
                        )
                        self._block_count += 1
                        continue
                except Exception as e:
                    logger.warning(f"TTL check failed: {e}")

                relayed = await self._relay(signal_dict)
                if relayed:
                    self._relay_count += 1
                    self._append_audit(signal_dict, status="relayed")
                else:
                    self._block_count += 1
                    self._append_audit(signal_dict, status="relay_failed")

            except Exception as e:
                logger.error(f"Relay error: {e}", exc_info=True)
            finally:
                self.validated_queue.task_done()

    async def _relay(self, signal_dict: dict) -> bool:
        """Try HTTP first, fall back to file relay."""
        if self._http_available:
            success = await self._http_relay(signal_dict)
            if success:
                return True
            logger.warning("HTTP relay failed — switching to file relay")
            self._http_available = False

        return self._file_relay(signal_dict)

    async def _http_relay(self, signal_dict: dict) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    CBOT_HTTP_URL,
                    json=signal_dict,
                    timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECS)
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"[HTTP] Relayed {signal_dict['signal_type']} @ "
                            f"{signal_dict['entry_price']} | Lot={signal_dict['lot_size']}"
                        )
                        return True
                    logger.warning(f"[HTTP] cBot returned HTTP {resp.status}")
                    return False
        except Exception as e:
            logger.warning(f"[HTTP] Relay exception: {e}")
            return False

    def _file_relay(self, signal_dict: dict) -> bool:
        """Atomic file write: write to .tmp then rename to prevent partial reads."""
        try:
            tmp_path = SIGNAL_OUTPUT_FILE.with_suffix('.tmp')
            payload = json.dumps(signal_dict, indent=2)
            tmp_path.write_text(payload, encoding='utf-8')
            tmp_path.rename(SIGNAL_OUTPUT_FILE)
            logger.info(
                f"[FILE] Relayed {signal_dict['signal_type']} @ "
                f"{signal_dict['entry_price']} | Lot={signal_dict['lot_size']} → {SIGNAL_OUTPUT_FILE}"
            )
            return True
        except OSError as e:
            logger.error(f"[FILE] Write failed: {e}")
            return False

    def _append_audit(self, signal_dict: dict, status: str):
        """Append to JSONL audit log (one record per line, never truncated)."""
        try:
            record = {**signal_dict, "relay_status": status, "relay_ts": datetime.utcnow().isoformat()}
            with open(AUDIT_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')
        except OSError as e:
            logger.error(f"Audit log write failed: {e}")

    def stats(self) -> dict:
        return {
            "relayed": self._relay_count,
            "blocked_or_stale": self._block_count,
            "http_available": self._http_available,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXECUTION BRIDGE  (FIX direct — replaces cBot relay)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ExecutionBridge:
    """
    Drains ValidatedSignal objects from validated_queue and fires them
    directly through DualAccountFIXExecutor (LIVE + DEMO simultaneously).

    Replaces SignalBridge's cBot HTTP/file relay — no cBot required.
    """

    def __init__(self, validated_queue: asyncio.Queue, fix_executor, channel_reporter=None):
        self.validated_queue  = validated_queue
        self.fix_executor     = fix_executor
        self._channel         = channel_reporter
        self._exec_count      = 0
        self._block_count     = 0
        self._error_count     = 0
        logger.info("ExecutionBridge ready (FIX direct mode)")

    async def relay_loop(self):
        """Continuously drain validated signals and execute via FIX."""
        logger.info("ExecutionBridge relay loop started")
        while True:
            validated = await self.validated_queue.get()
            try:
                signal_dict = (
                    validated.to_dict()
                    if hasattr(validated, 'to_dict')
                    else (validated if isinstance(validated, dict) else vars(validated))
                )

                # TTL check — discard stale signals
                try:
                    signal_ts = datetime.fromisoformat(signal_dict['timestamp'])
                    if signal_ts.tzinfo is None:
                        signal_ts = signal_ts.replace(tzinfo=timezone.utc)
                    age_secs = (datetime.now(timezone.utc) - signal_ts).total_seconds()
                    if age_secs > MAX_SIGNAL_AGE_SECS:
                        logger.warning(
                            f"[STALE] Discarding {signal_dict.get('signal_type','?')} @ "
                            f"{signal_dict.get('entry_price','?')} — {age_secs:.0f}s old"
                        )
                        self._block_count += 1
                        self._append_audit(signal_dict, status="stale_discarded")
                        continue
                except Exception as e:
                    logger.warning(f"TTL check failed: {e}")

                # Fire through FIX executor (LIVE + DEMO simultaneously)
                try:
                    result = await self.fix_executor.execute_signal(validated)
                    self._exec_count += 1
                    self._append_audit(signal_dict, status="executed", extra=result)
                    logger.info(
                        f"[FIX] Executed {signal_dict.get('signal_type','?')} @ "
                        f"{signal_dict.get('entry_price','?')} | "
                        f"Lot={signal_dict.get('lot_size','?')} | Result={result}"
                    )
                    # Log signal to private channel (non-blocking)
                    if self._channel:
                        cl_ord_id = result.get("live", "") if isinstance(result, dict) else ""
                        asyncio.ensure_future(
                            self._channel.report_signal(
                                signal_dict = signal_dict,
                                cl_ord_id   = cl_ord_id,
                                source      = "SIGNALS",
                            )
                        )
                except Exception as e:
                    self._error_count += 1
                    self._append_audit(signal_dict, status="execution_error", extra={"error": str(e)})
                    logger.error(f"[FIX] Execution error: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"ExecutionBridge loop error: {e}", exc_info=True)
            finally:
                self.validated_queue.task_done()

    def _append_audit(self, signal_dict: dict, status: str, extra: dict = None):
        """Append to JSONL audit log (one record per line, never truncated)."""
        try:
            record = {
                **signal_dict,
                "relay_status": status,
                "relay_ts": datetime.utcnow().isoformat(),
            }
            if extra:
                record["execution_result"] = extra
            with open(AUDIT_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')
        except OSError as e:
            logger.error(f"Audit log write failed: {e}")

    def stats(self) -> dict:
        return {
            "executed":      self._exec_count,
            "stale_dropped": self._block_count,
            "errors":        self._error_count,
        }
