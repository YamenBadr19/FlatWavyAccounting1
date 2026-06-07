"""
config.py — Configuration loaded from environment variables
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All secrets must be set as Replit Secrets (Environment Variables).
Never hardcode values here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

def _req(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val

def _opt(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

# ── Telegram API Credentials ──────────────────────────
TELEGRAM_API_ID   = int(_req("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = _req("TELEGRAM_API_HASH")
TELEGRAM_PHONE    = _req("TELEGRAM_PHONE")

# ── Telegram String Session (silent / production mode) ─
# Generate with: python generate_session.py
# Then save output as TELEGRAM_STRING_SESSION secret
TELEGRAM_STRING_SESSION = _opt("TELEGRAM_STRING_SESSION")

# ── Telegram Folder / Channel IDs ────────────────────
# Run find_folders.py to discover these IDs
SIGNALS_FOLDER_ID = int(_opt("SIGNALS_FOLDER_ID", "0"))
NEWS_FOLDER_ID    = int(_opt("NEWS_FOLDER_ID", "0"))

# ── Session ───────────────────────────────────────────
SESSION_NAME = _opt("SESSION_NAME", "gold_blueprint_session")

# ── Telegram Bot Token (for ControlBot) ───────────────
TELEGRAM_BOT_TOKEN  = _opt("TELEGRAM_BOT_TOKEN")
CONTROL_CHAT_ID     = _opt("CONTROL_CHAT_ID")
REPORTER_CHAT_ID    = _opt("REPORTER_CHAT_ID")

# ── cTrader MCP Server ────────────────────────────────
MCP_URL = _opt("MCP_URL", "http://127.0.0.1:9876/mcp/")

# ── cBot HTTP Bridge (legacy fallback) ───────────────
CBOT_HTTP_URL = _opt("CBOT_HTTP_URL", "http://localhost:8765/execute")
