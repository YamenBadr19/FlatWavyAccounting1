"""
status.py — Gold Blueprint Live Status Dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads from shared state files to show a real-time health snapshot
of every system component — no imports from the brain required.

Data sources (all local — zero network calls except news check):
  /tmp/gold_blueprint_heartbeat  → brain liveness + age
  gold_blueprint.log             → FIX session status, last market data
  watchdog.log                   → restart count
  signal_audit.jsonl             → last 5 executed signals
  ForexFactory JSON endpoint     → current news mode (live check)
  yfinance GC=F                  → current Gold spot price (live check)

USAGE:
  python status.py           → one-shot snapshot
  python status.py --watch   → auto-refresh every 5 seconds
  python status.py -w 10     → auto-refresh every 10 seconds
"""

import asyncio
import json
import os
import re
import sys
import time
import argparse
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FILE PATHS  (all relative to project root)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROOT             = Path(__file__).parent
HEARTBEAT_FILE   = Path("/tmp/gold_blueprint_heartbeat")
BRAIN_LOG        = ROOT / "gold_blueprint.log"
WATCHDOG_LOG     = ROOT / "watchdog.log"
AUDIT_LOG        = ROOT / "signal_audit.jsonl"

FF_URL           = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
XAUUSD_TICKER    = "GC=F"
HEARTBEAT_STALE  = 120   # seconds before brain considered hung
LOG_SCAN_LINES   = 300   # how many tail lines to parse for FIX/market state

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ANSI COLOURS (no external deps)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

G  = "\033[92m"   # green
R  = "\033[91m"   # red
Y  = "\033[93m"   # yellow
C  = "\033[96m"   # cyan
B  = "\033[94m"   # blue
DIM= "\033[2m"
BD = "\033[1m"
RS = "\033[0m"    # reset

def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{RS}"

def _ok(text: str)   -> str: return _c(text, G)
def _warn(text: str) -> str: return _c(text, Y)
def _err(text: str)  -> str: return _c(text, R)
def _dim(text: str)  -> str: return _c(text, DIM)
def _bold(text: str) -> str: return _c(text, BD)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _age_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds//60)}m {int(seconds%60)}s ago"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m ago"


def _duration_str(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds//60)}m {int(seconds%60)}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


def _tail(path: Path, n: int) -> list[str]:
    """Read last n lines of a file efficiently."""
    if not path.exists():
        return []
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            chunk_size = min(size, n * 200)
            f.seek(max(0, size - chunk_size))
            raw = f.read().decode(errors='replace')
        lines = raw.splitlines()
        return lines[-n:]
    except OSError:
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA COLLECTORS  (each reads one source)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def collect_brain_health() -> dict:
    """Read heartbeat file for brain liveness."""
    if not HEARTBEAT_FILE.exists():
        return {"alive": False, "age_secs": None, "heartbeat_ts": None}
    try:
        ts = float(HEARTBEAT_FILE.read_text().strip())
        age = time.time() - ts
        return {
            "alive":        age < HEARTBEAT_STALE,
            "age_secs":     age,
            "heartbeat_ts": ts,
        }
    except (ValueError, OSError):
        return {"alive": False, "age_secs": None, "heartbeat_ts": None}


def collect_watchdog_info() -> dict:
    """Parse watchdog.log for restart count and watchdog liveness."""
    lines = _tail(WATCHDOG_LOG, 100)
    restarts = 0
    running  = False
    last_ts  = None

    for line in reversed(lines):
        if "Brain healthy" in line or "WATCHDOG SUPERVISOR" in line or "Brain started" in line:
            running = True
        m = re.search(r"restart #(\d+)", line, re.IGNORECASE)
        if m:
            restarts = max(restarts, int(m.group(1)))
        # Extract timestamp from last line
        if last_ts is None:
            m_ts = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m_ts:
                try:
                    last_ts = datetime.strptime(m_ts.group(1), "%Y-%m-%d %H:%M:%S")
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

    watchdog_age = None
    if last_ts:
        watchdog_age = (datetime.now(timezone.utc) - last_ts).total_seconds()
        running = watchdog_age < 120

    return {
        "running":   running,
        "restarts":  restarts,
        "log_age":   watchdog_age,
    }


def collect_fix_status() -> dict:
    """
    Parse gold_blueprint.log to find the most recent login/logout event
    for LIVE and DEMO FIX sessions.
    """
    lines = _tail(BRAIN_LOG, LOG_SCAN_LINES)
    result = {
        "live": {"logged_in": False, "last_event": None, "last_event_age": None},
        "demo": {"logged_in": False, "last_event": None, "last_event_age": None},
    }

    for line in reversed(lines):
        for acct in ("live", "demo"):
            if result[acct]["last_event"] is not None:
                continue
            tag = f"[{acct.upper()}]"
            if tag not in line.upper():
                continue

            if "Logged in ✓" in line or "Logon confirmed" in line:
                result[acct]["logged_in"] = True
            elif "Logout" in line or "Disconnected" in line:
                result[acct]["logged_in"] = False
            else:
                continue

            # Parse timestamp
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                try:
                    event_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    event_ts = event_ts.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - event_ts).total_seconds()
                    result[acct]["last_event"]     = event_ts
                    result[acct]["last_event_age"] = age
                except ValueError:
                    pass

    return result


def collect_market_data_from_log() -> dict:
    """
    Parse the last market data update line from gold_blueprint.log.
    The feed logs: Price=XXXX | RSI=XX | ATR=XX | EMA50=XX every 60s.
    """
    lines = _tail(BRAIN_LOG, LOG_SCAN_LINES)
    for line in reversed(lines):
        if "Market updated" not in line and "Market data updated" not in line:
            continue
        data: dict = {"source": "log"}
        for key, pattern in [
            ("price",  r"Price=([0-9.]+)"),
            ("rsi",    r"RSI=([0-9.]+)"),
            ("atr",    r"ATR=([0-9.]+)"),
            ("ema50",  r"EMA50?=([0-9.]+)"),
        ]:
            m = re.search(pattern, line)
            if m:
                data[key] = float(m.group(1))

        # Extract timestamp
        m_ts = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m_ts:
            try:
                ts = datetime.strptime(m_ts.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                data["age_secs"] = (datetime.now(timezone.utc) - ts).total_seconds()
            except ValueError:
                pass

        if "price" in data:
            return data

    return {}


async def fetch_live_gold_price() -> Optional[float]:
    """Fetch current Gold futures price from yfinance (runs in thread)."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        def _fetch():
            t = yf.Ticker(XAUUSD_TICKER)
            hist = t.history(period="2d", interval="1d", auto_adjust=True)
            if hist.empty:
                return None
            return round(float(hist["Close"].iloc[-1]), 2)
        return await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=8.0)
    except Exception:
        return None


async def fetch_news_status() -> dict:
    """Check ForexFactory for upcoming high-impact USD events."""
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as s:
            async with s.get(FF_URL) as resp:
                if resp.status != 200:
                    return {"error": f"HTTP {resp.status}"}
                raw = await resp.json(content_type=None)

        now = datetime.now(timezone.utc)
        upcoming = []
        news_mode_active = False
        news_event_name  = None
        news_mins_remaining = 0

        for item in raw:
            if item.get("country", "").upper() != "USD":
                continue
            if item.get("impact", "").capitalize() != "High":
                continue
            date_str = item.get("date", "")
            time_str = item.get("time", "")
            try:
                if time_str and time_str.lower() not in ("", "all day", "tentative"):
                    event_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M%p")
                else:
                    event_dt = datetime.strptime(date_str, "%Y-%m-%d")
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            mins_diff = (event_dt - now).total_seconds() / 60.0
            mins_since = -mins_diff

            # Check if we're in the ±30 min window
            if -30 <= mins_diff <= 30 or 0 <= mins_since <= 30:
                news_mode_active = True
                news_event_name  = item.get("title", "")
                if mins_diff > 0:
                    news_mins_remaining = 30 + mins_diff
                else:
                    news_mins_remaining = 30 - mins_since

            upcoming.append({
                "title":     item.get("title", ""),
                "mins_diff": mins_diff,
                "event_dt":  event_dt,
            })

        upcoming.sort(key=lambda x: abs(x["mins_diff"]))
        next_event = next((e for e in upcoming if e["mins_diff"] > 0), None)

        return {
            "active":      news_mode_active,
            "event_name":  news_event_name,
            "mins_left":   news_mins_remaining,
            "next_event":  next_event,
            "error":       None,
        }
    except Exception as e:
        return {"error": str(e), "active": False}


def collect_recent_signals(n: int = 5) -> list:
    """Read last n entries from the JSONL audit log."""
    if not AUDIT_LOG.exists():
        return []
    lines = _tail(AUDIT_LOG, n * 2)
    records = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            records.append(r)
            if len(records) >= n:
                break
        except json.JSONDecodeError:
            continue
    return records


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RENDERER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

W = 62  # dashboard width

def _rule(char="─") -> str:
    return DIM + char * W + RS

def _header(title: str, badge: str = "", badge_colour: str = G) -> str:
    badge_str = f"  [{_c(badge, badge_colour)}]" if badge else ""
    return f"\n  {_bold(title)}{badge_str}"

def _row(label: str, value: str, width: int = 16) -> str:
    return f"  {_dim(label.ljust(width))} {value}"


def render(
    brain:    dict,
    watchdog: dict,
    fix:      dict,
    market:   dict,
    price:    Optional[float],
    news:     dict,
    signals:  list,
) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")
    lines   = []

    # ── Title bar ────────────────────────────────────────
    lines += [
        "",
        _bold("╔" + "═" * (W - 2) + "╗"),
        _bold(f"║  GOLD BLUEPRINT — SYSTEM STATUS") +
        _dim(f"  {now_str}".rjust(W - 35)) + _bold("  ║"),
        _bold("╚" + "═" * (W - 2) + "╝"),
    ]

    # ── Section 1: Brain health ───────────────────────────
    alive      = brain.get("alive", False)
    age        = brain.get("age_secs")
    hb_str     = (_ok("● ALIVE") + _dim(f"  heartbeat {_age_str(age)}")) if (alive and age is not None) \
                 else _err("● OFFLINE")
    wd_running = watchdog.get("running", False)
    restarts   = watchdog.get("restarts", 0)
    wd_str     = (_ok("● RUNNING") + _dim(f"  {restarts} restart{'s' if restarts != 1 else ''}")) \
                 if wd_running else _warn("○ NOT DETECTED")

    lines += [
        _header("BRAIN HEALTH", "ALIVE" if alive else "OFFLINE", G if alive else R),
        _rule(),
        _row("Brain:",    hb_str),
        _row("Watchdog:", wd_str),
    ]

    # ── Section 2: Gold market ────────────────────────────
    display_price = price or market.get("price")
    rsi   = market.get("rsi")
    atr   = market.get("atr")
    ema50 = market.get("ema50")
    data_age = market.get("age_secs")

    price_str  = f"${display_price:,.2f}" if display_price else _warn("N/A")
    rsi_label  = (
        _warn(f"{rsi:.1f}  overbought") if rsi and rsi > 70 else
        _warn(f"{rsi:.1f}  oversold")   if rsi and rsi < 30 else
        _ok(f"{rsi:.1f}  neutral")      if rsi else _dim("N/A")
    )
    atr_label  = (
        _err(f"${atr:.2f}  HIGH — caution") if atr and atr > 25 else
        _warn(f"${atr:.2f}  low")            if atr and atr < 3  else
        _ok(f"${atr:.2f}  healthy")          if atr else _dim("N/A")
    )
    ema_label  = ""
    if display_price and ema50:
        diff = display_price - ema50
        trend = _ok(f"${ema50:,.2f}  price +${diff:.1f} above ↑") if diff > 0 \
                else _warn(f"${ema50:,.2f}  price ${abs(diff):.1f} below ↓")
        ema_label = trend
    else:
        ema_label = _dim("N/A")

    age_note = _dim(f"  (data {_age_str(data_age)})") if data_age else ""
    lines += [
        _header("GOLD MARKET  (GC=F — Gold Futures)", "LIVE" if display_price else "NO DATA",
                G if display_price else Y),
        _rule(),
        _row("Spot Price:", price_str + age_note),
        _row("RSI(14):",    rsi_label),
        _row("ATR(14):",    atr_label),
        _row("EMA(50):",    ema_label),
    ]

    # ── Section 3: FIX sessions ───────────────────────────
    def _fix_row(name: str, data: dict) -> str:
        logged = data.get("logged_in", False)
        evt_age = data.get("last_event_age")
        age_note = _dim(f"  (last event {_age_str(evt_age)})") if evt_age else ""
        status = (_ok("● LOGGED IN") + age_note) if logged else _err("○ OFFLINE / NOT YET CONNECTED")
        return _row(f"{name}:", status)

    lines += [
        _header("FIX SESSIONS"),
        _rule(),
        _fix_row("LIVE", fix.get("live", {})),
        _fix_row("DEMO", fix.get("demo", {})),
    ]

    # ── Section 4: News mode ──────────────────────────────
    n_active = news.get("active", False)
    n_error  = news.get("error")
    n_event  = news.get("event_name")
    n_mins   = news.get("mins_left", 0)
    n_next   = news.get("next_event")

    if n_error:
        mode_str = _warn(f"⚠  Calendar unavailable ({n_error[:40]})")
    elif n_active:
        mode_str = _err(f"⚠  ACTIVE — '{n_event}'  {n_mins:.0f} min remaining")
    else:
        mode_str = _ok("✓  CLEAR — no high-impact USD events within ±30 min")

    next_str = ""
    if n_next and not n_active:
        nd = n_next["mins_diff"]
        if nd < 60:
            when = f"in {nd:.0f} min"
        elif nd < 1440:
            when = f"in {nd/60:.1f}h"
        else:
            dt   = n_next["event_dt"]
            when = dt.strftime("%a %b %d  %H:%M UTC")
        next_str = _row("Next event:", f"{_dim(n_next['title'])}  {_dim(when)}")

    badge = "⚠ LOT CAP 0.01" if n_active else "CLEAR"
    bcol  = R if n_active else G
    lines += [
        _header("NEWS MODE", badge, bcol),
        _rule(),
        _row("Status:", mode_str),
    ]
    if next_str:
        lines.append(next_str)

    # ── Section 5: Recent signals ─────────────────────────
    lines += [
        _header("LAST 5 EXECUTED SIGNALS"),
        _rule(),
    ]

    if not signals:
        lines.append(_dim("  (no signals in audit log yet)"))
    else:
        for sig in signals:
            ts_raw = sig.get("relay_ts") or sig.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts_str = ts.strftime("%m-%d  %H:%M")
            except Exception:
                ts_str = ts_raw[:16]

            st    = sig.get("signal_type", "?")
            entry = sig.get("entry_price", "?")
            sl    = sig.get("stop_loss",   "?")
            tp    = sig.get("take_profit", "?")
            lot   = sig.get("lot_size",    "?")
            conf  = sig.get("confluence_level", "?")
            news_flag = " [news]" if sig.get("news_mode_active") else ""
            status_flag = sig.get("relay_status", "")
            status_icon = _ok("✓") if status_flag == "executed" else _warn("~")

            dir_col = G if st == "BUY" else Y
            lines.append(
                f"  {_dim(ts_str)}  {_c(f'{st:<4}', dir_col)}"
                f"  @{entry:<9.2f}"
                f"  SL={sl:<9.2f}"
                f"  TP={tp:<9.2f}"
                f"  {lot}L  C={conf}/5"
                f"  {status_icon}{_dim(news_flag)}"
            )

    # ── Footer ────────────────────────────────────────────
    lines += ["", _rule("─"), ""]
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def snapshot() -> str:
    """Collect all data and return rendered dashboard string."""
    # Local reads are instant — do them synchronously
    brain    = collect_brain_health()
    watchdog = collect_watchdog_info()
    fix      = collect_fix_status()
    market   = collect_market_data_from_log()
    signals  = collect_recent_signals(5)

    # Async network calls — run in parallel
    price, news = await asyncio.gather(
        fetch_live_gold_price(),
        fetch_news_status(),
    )

    return render(brain, watchdog, fix, market, price, news, signals)


async def watch_loop(interval: int):
    """Refresh the dashboard every `interval` seconds."""
    try:
        while True:
            dashboard = await snapshot()
            # Clear screen
            print("\033[2J\033[H", end="")
            print(dashboard)
            print(_dim(f"  Press Ctrl+C to exit  •  Refreshing in {interval}s..."))
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Exited status dashboard.\n")


async def _run():
    parser = argparse.ArgumentParser(
        description="Gold Blueprint status dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--watch", "-w",
        nargs="?",
        const=5,
        type=int,
        metavar="SECS",
        help="Auto-refresh every N seconds (default: 5)"
    )
    args = parser.parse_args()

    if args.watch is not None:
        await watch_loop(args.watch)
    else:
        print(await snapshot())


if __name__ == "__main__":
    asyncio.run(_run())
