"""
generate_session.py — Telegram StringSession Generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run this ONCE on any machine to generate a reusable StringSession string.
Paste the output string into Replit Secrets as TELEGRAM_STRING_SESSION.

After that, the main system starts silently with no phone/SMS prompts —
perfect for cloud deployment (Replit, Railway, Render, VPS).

Usage:
  python generate_session.py

Requirements:
  pip install telethon python-dotenv

The script reads TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
from environment / .env file, or prompts you to enter them if missing.
"""

import asyncio
import os
import sys

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("Telethon not installed. Run:  pip install telethon")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional


def _prompt(env_key: str, label: str, hidden: bool = False) -> str:
    val = os.environ.get(env_key, "").strip()
    if val:
        print(f"  {label}: (loaded from environment)")
        return val
    if hidden:
        import getpass
        return getpass.getpass(f"  Enter {label}: ").strip()
    return input(f"  Enter {label}: ").strip()


async def generate():
    print()
    print("━" * 55)
    print("  Gold Blueprint — Telegram StringSession Generator")
    print("━" * 55)
    print()
    print("Reading credentials...")

    api_id   = int(_prompt("TELEGRAM_API_ID",   "TELEGRAM_API_ID  (integer)"))
    api_hash = _prompt("TELEGRAM_API_HASH",      "TELEGRAM_API_HASH")
    phone    = _prompt("TELEGRAM_PHONE",         "TELEGRAM_PHONE  (e.g. +12025551234)")

    print()
    print("Connecting to Telegram...")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start(phone=phone)

        me = await client.get_me()
        session_string = client.session.save()

    print()
    print("━" * 55)
    print(f"  Authenticated as: {me.first_name} (@{getattr(me, 'username', 'N/A')})")
    print("━" * 55)
    print()
    print("  YOUR STRING SESSION (copy the entire line below):")
    print()
    print(session_string)
    print()
    print("━" * 55)
    print("  NEXT STEPS:")
    print("  1. Copy the string above (the long line between the rules)")
    print("  2. Go to Replit → Secrets tab → TELEGRAM_STRING_SESSION")
    print("  3. Paste it as the value")
    print("  4. Run: python python-brain/main.py")
    print("  5. No more phone/SMS prompts — fully silent startup")
    print("━" * 55)
    print()

    # Optionally write to a local file for backup
    out_file = "session_backup.txt"
    try:
        with open(out_file, "w") as f:
            f.write(session_string + "\n")
        print(f"  Session also saved locally to: {out_file}")
        print("  (Keep this file private — treat it like a password)")
        print()
    except OSError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(generate())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
