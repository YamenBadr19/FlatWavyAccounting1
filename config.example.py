"""
config.example.py — Configuration Template
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Copy this file to config.py and fill in your real values.
NEVER commit config.py to version control — it is in .gitignore.

How to get credentials:
  Telegram API ID & Hash → https://my.telegram.org/apps
  Folder IDs            → run: python find_folders.py
"""

# ── Telegram API Credentials ──────────────────────────
TELEGRAM_API_ID   = 12345678            # Integer from my.telegram.org/apps
TELEGRAM_API_HASH = "your_api_hash"     # String from my.telegram.org/apps
TELEGRAM_PHONE    = "+1234567890"       # Your phone number with country code

# ── Telegram Folder / Channel IDs ────────────────────
# Run find_folders.py to discover these IDs.
# These can be:
#   - A Telegram folder ID (from Dialog Filters)
#   - A channel/group username: "@mychannel"
#   - A channel/group numeric ID: -1001234567890
SIGNALS_FOLDER_ID = 123456789
NEWS_FOLDER_ID    = 987654321

# ── Session ───────────────────────────────────────────
SESSION_NAME = "gold_blueprint_session"

# ── cBot Bridge (optional — file relay works without this) ──
CBOT_HTTP_URL = "http://localhost:8765/execute"
