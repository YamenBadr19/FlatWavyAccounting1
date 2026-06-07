"""
setup_session.py — One-time Telegram String Session Generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run this ONCE from the Replit Shell to generate your session.
After this, the bot runs silently forever without asking for codes.

Usage:
  python setup_session.py

Or with code directly:
  python setup_session.py --code 12345
"""

import asyncio
import os
import sys
import argparse

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
PHONE    = os.environ.get("TELEGRAM_PHONE", "")

async def main(code_arg: str = None):
    if not API_ID or not API_HASH or not PHONE:
        print("❌ Missing TELEGRAM_API_ID, TELEGRAM_API_HASH, or TELEGRAM_PHONE in Secrets.")
        sys.exit(1)

    print()
    print("━" * 55)
    print("  Gold Blueprint — Telegram Session Setup")
    print("━" * 55)
    print(f"  Account: {PHONE}")
    print()

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("  Sending login code to Telegram...")
        await client.send_code_request(PHONE)

        if code_arg:
            code = code_arg.strip()
            print(f"  Using code: {code}")
        else:
            print()
            code = input("  Enter the code you received on Telegram: ").strip()

        try:
            await client.sign_in(PHONE, code)
        except Exception as e:
            print(f"\n❌ Login failed: {e}")
            print("   The code may have expired. Please run the script again.")
            await client.disconnect()
            sys.exit(1)

    me = await client.get_me()
    session_string = client.session.save()

    print()
    print("━" * 55)
    print(f"  ✅ Logged in as: {me.first_name} (@{getattr(me, 'username', 'N/A')})")
    print("━" * 55)
    print()
    print("  Copy the entire line below and add it to Replit Secrets")
    print("  as:  TELEGRAM_STRING_SESSION")
    print()
    print("=" * 70)
    print(session_string)
    print("=" * 70)
    print()

    # Save to file as backup
    with open("session_string.txt", "w") as f:
        f.write(session_string)
    print("  Also saved to: python-brain/session_string.txt")
    print("  ⚠️  Keep that file private — delete it after copying to Secrets!")
    print()

    await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", help="Telegram login code", default=None)
    args = parser.parse_args()
    asyncio.run(main(code_arg=args.code))
