import asyncio
from telethon import TelegramClient
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE

async def main():
    async with TelegramClient('session_finder', TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        dialogs = await client.get_dialogs()
        print("\n=== AVAILABLE CHATS & FOLDERS ===")
        for dialog in dialogs:
            # Filters out standard direct messages for scanning efficiency
            if dialog.is_channel or dialog.is_group:
                print(f"ID: {dialog.id} | Title: {dialog.title}")

asyncio.run(main())