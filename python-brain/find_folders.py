"""
find_folders.py — Discover Telegram Folder & Channel IDs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run this once to find the numeric IDs you need for config.py.

Usage:
    python find_folders.py

Output:
    Lists all dialogs (channels, groups, users) with their IDs and titles.
    Copy the IDs for your Signals and News folders into config.py.
"""

import asyncio
import sys
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest

try:
    from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, SESSION_NAME
except ImportError:
    print("config.py not found. Copy config.example.py to config.py first.")
    sys.exit(1)


async def main():
    client = TelegramClient(SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start(phone=TELEGRAM_PHONE)

    print("\n" + "=" * 60)
    print("  TELEGRAM FOLDER FINDER")
    print("=" * 60)

    # List Telegram folders (Dialog Filters)
    print("\n📁 YOUR TELEGRAM FOLDERS:")
    print("-" * 40)
    try:
        filters = await client(GetDialogFiltersRequest())
        for f in filters:
            if hasattr(f, 'id') and hasattr(f, 'title'):
                print(f"  Folder ID: {f.id:>12}  |  Title: {f.title}")
    except Exception as e:
        print(f"  Could not fetch folders: {e}")

    # List all dialogs (channels, groups, users)
    print("\n📡 YOUR DIALOGS (channels, groups, users):")
    print("-" * 60)
    count = 0
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        entity_id = getattr(entity, 'id', 'N/A')
        title     = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'N/A')
        username  = getattr(entity, 'username', None)
        kind = type(entity).__name__

        username_str = f" (@{username})" if username else ""
        print(f"  ID: {entity_id:>15}  |  {kind:<20}  |  {title}{username_str}")
        count += 1
        if count >= 100:
            print("  ... (showing first 100 dialogs)")
            break

    print("\n" + "=" * 60)
    print("  Copy the IDs above into config.py:")
    print("    SIGNALS_FOLDER_ID = <id of your signals channel/folder>")
    print("    NEWS_FOLDER_ID    = <id of your news channel/folder>")
    print("=" * 60)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
