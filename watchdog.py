"""
watchdog.py — Gold Blueprint Brain Supervisor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors python-brain/main.py and restarts it automatically on crash
or hang. Sends a Telegram message to your Saved Messages whenever
a restart occurs, or when the crash loop limit is reached.

HOW IT WORKS:
  1. Launches main.py as a subprocess
  2. Polls every POLL_INTERVAL_SECS seconds:
       • Is the process still alive?
       • Is the heartbeat file fresh (updated within HEARTBEAT_STALE_SECS)?
  3. On crash or hang → waits, then restarts
  4. Exponential backoff between restarts (5s → 10s → 20s → max 120s)
  5. Crash loop guard: if >MAX_CRASHES_BEFORE_PAUSE crashes within
     CRASH_WINDOW_SECS, pauses for PAUSE_AFTER_CRASH_LOOP_MINS minutes
     and sends a final "brain paused" alert before trying again
  6. All restarts and alerts are sent to your Telegram Saved Messages

USAGE:
  python watchdog.py

  Run this instead of main.py directly in production.
  The watchdog never exits unless you send SIGINT (Ctrl+C).
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger('watchdog')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] watchdog: %(message)s',
    handlers=[
        logging.FileHandler('watchdog.log'),
        logging.StreamHandler(sys.stdout),
    ],
)

# ── Configuration ─────────────────────────────────────────────
BRAIN_SCRIPT           = str(Path(__file__).parent / "python-brain" / "main.py")
HEARTBEAT_FILE         = Path("/tmp/gold_blueprint_heartbeat")
POLL_INTERVAL_SECS     = 30        # How often to check health
HEARTBEAT_STALE_SECS   = 120       # Heartbeat older than this = hung brain
RESTART_BACKOFF_BASE   = 5.0       # First restart delay
RESTART_BACKOFF_MAX    = 120.0     # Max restart delay
MAX_CRASHES_BEFORE_PAUSE = 5       # Crash loop threshold
CRASH_WINDOW_SECS      = 600       # 10-minute window for crash count
PAUSE_AFTER_CRASH_LOOP_MINS = 10   # Pause before resuming after crash loop

# ── Telegram alert config ─────────────────────────────────────
TELEGRAM_API_ID          = os.environ.get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH        = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_STRING_SESSION  = os.environ.get("TELEGRAM_STRING_SESSION", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM NOTIFIER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def send_telegram_alert(text: str):
    """
    Send a message to your Telegram Saved Messages using StringSession.
    Uses the same credentials as the brain — no extra API keys needed.
    Fails silently if credentials are missing or Telegram is unreachable.
    """
    if not (TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_STRING_SESSION):
        logger.warning("Telegram credentials not set — alert not sent")
        return

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        async with TelegramClient(
            StringSession(TELEGRAM_STRING_SESSION),
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        ) as client:
            await client.send_message("me", text)
            logger.info("Telegram alert sent to Saved Messages")

    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")


def _alert_text(event: str, restart_count: int, reason: str, uptime_secs: float) -> str:
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)
    return (
        f"🤖 Gold Blueprint Watchdog\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Event:    {event}\n"
        f"Reason:   {reason}\n"
        f"Restarts: #{restart_count}\n"
        f"Uptime:   {uptime_h}h {uptime_m}m\n"
        f"Time:     {now}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROCESS MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BrainProcess:
    """Wraps one running instance of python-brain/main.py."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._started_at: float = 0.0

    def start(self):
        if HEARTBEAT_FILE.exists():
            HEARTBEAT_FILE.unlink()

        self._proc = subprocess.Popen(
            [sys.executable, BRAIN_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        self._started_at = time.monotonic()
        logger.info(f"Brain started (PID {self._proc.pid})")

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def exit_code(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.poll()

    def uptime_secs(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def is_heartbeat_stale(self) -> bool:
        """Returns True if the heartbeat file hasn't been updated recently."""
        if not HEARTBEAT_FILE.exists():
            # Give it grace time on first boot (2× stale threshold)
            return self.uptime_secs() > HEARTBEAT_STALE_SECS * 2
        age = time.time() - HEARTBEAT_FILE.stat().st_mtime
        return age > HEARTBEAT_STALE_SECS

    def terminate(self):
        if self._proc and self.is_alive():
            logger.info(f"Sending SIGTERM to brain (PID {self._proc.pid})")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Brain did not exit cleanly — sending SIGKILL")
                self._proc.kill()
                self._proc.wait()
        self._proc = None

    def drain_output(self, max_lines: int = 20) -> str:
        """Read last N lines of stdout for the alert message."""
        lines = []
        if self._proc and self._proc.stdout:
            try:
                for line in self._proc.stdout:
                    lines.append(line.rstrip())
                    if len(lines) > max_lines:
                        lines.pop(0)
            except Exception:
                pass
        return "\n".join(lines[-5:]) if lines else "(no output captured)"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WATCHDOG LOOP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Watchdog:
    def __init__(self):
        self._brain         = BrainProcess()
        self._restart_count = 0
        self._backoff       = RESTART_BACKOFF_BASE
        self._crash_times:  deque = deque()   # timestamps of recent crashes
        self._running       = True
        self._total_start   = time.monotonic()

        signal.signal(signal.SIGINT,  self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Watchdog received signal {signum} — shutting down")
        self._running = False
        self._brain.terminate()
        sys.exit(0)

    def _record_crash(self):
        now = time.monotonic()
        self._crash_times.append(now)
        # Evict crashes outside the window
        while self._crash_times and now - self._crash_times[0] > CRASH_WINDOW_SECS:
            self._crash_times.popleft()

    def _is_crash_loop(self) -> bool:
        return len(self._crash_times) >= MAX_CRASHES_BEFORE_PAUSE

    async def _restart(self, reason: str):
        self._record_crash()
        self._restart_count += 1
        uptime = self._brain.uptime_secs()

        logger.warning(
            f"Brain restart #{self._restart_count} | Reason: {reason} | "
            f"Uptime was {uptime:.0f}s | Backoff: {self._backoff:.0f}s"
        )

        alert = _alert_text(
            event          = f"⚠️ RESTARTED (#{self._restart_count})",
            restart_count  = self._restart_count,
            reason         = reason,
            uptime_secs    = uptime,
        )
        asyncio.create_task(send_telegram_alert(alert))

        # Crash loop guard
        if self._is_crash_loop():
            pause_secs = PAUSE_AFTER_CRASH_LOOP_MINS * 60
            logger.error(
                f"CRASH LOOP DETECTED — {len(self._crash_times)} crashes in "
                f"{CRASH_WINDOW_SECS}s. Pausing {PAUSE_AFTER_CRASH_LOOP_MINS} min."
            )
            loop_alert = _alert_text(
                event         = "🚨 CRASH LOOP — Brain paused",
                restart_count = self._restart_count,
                reason        = f"{len(self._crash_times)} crashes in {CRASH_WINDOW_SECS}s",
                uptime_secs   = uptime,
            ) + f"\n\nResuming in {PAUSE_AFTER_CRASH_LOOP_MINS} minutes."
            await send_telegram_alert(loop_alert)
            await asyncio.sleep(pause_secs)
            self._crash_times.clear()
            self._backoff = RESTART_BACKOFF_BASE

        self._brain.terminate()
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, RESTART_BACKOFF_MAX)

        self._brain.start()

    async def run(self):
        logger.info("=" * 58)
        logger.info("  GOLD BLUEPRINT — WATCHDOG SUPERVISOR")
        logger.info(f"  Brain: {BRAIN_SCRIPT}")
        logger.info(f"  Poll:  every {POLL_INTERVAL_SECS}s")
        logger.info(f"  Heartbeat stale threshold: {HEARTBEAT_STALE_SECS}s")
        logger.info(f"  Crash loop: >{MAX_CRASHES_BEFORE_PAUSE} in {CRASH_WINDOW_SECS}s")
        logger.info("=" * 58)

        # Initial start + startup alert
        self._brain.start()
        asyncio.create_task(send_telegram_alert(
            "✅ Gold Blueprint Brain started\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Watchdog: active\n"
            f"Mode: StringSession (silent)"
        ))

        while self._running:
            await asyncio.sleep(POLL_INTERVAL_SECS)

            if not self._brain.is_alive():
                code = self._brain.exit_code()
                await self._restart(f"Process exited (code={code})")
                continue

            if self._brain.is_heartbeat_stale():
                await self._restart(
                    f"Heartbeat stale >{HEARTBEAT_STALE_SECS}s — brain appears hung"
                )
                continue

            # Healthy — reset backoff gradually
            if self._backoff > RESTART_BACKOFF_BASE:
                self._backoff = max(RESTART_BACKOFF_BASE, self._backoff / 1.5)

            uptime = self._brain.uptime_secs()
            logger.info(
                f"Brain healthy | PID={self._brain._proc.pid} | "
                f"Uptime={uptime:.0f}s | Restarts={self._restart_count} | "
                f"Backoff={self._backoff:.0f}s"
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _main():
    watchdog = Watchdog()
    await watchdog.run()


if __name__ == "__main__":
    asyncio.run(_main())
