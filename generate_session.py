#!/usr/bin/env python3
"""
generate_session.py — Generate Telegram String Session

Interactively creates a Telegram session string for use in the bot.
Run this once to generate credentials, then add to .env
"""

import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession


async def generate_session():
    # Get credentials from user
    api_id = int(input("Enter your API ID (from my.telegram.org/apps): "))
    api_hash = input("Enter your API Hash (from my.telegram.org/apps): ")
    phone = input("Enter your phone number (with country code, e.g., +1234567890): ")

    # Create client
    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        device_model="FlatWavyAccounting",
    )

    # Connect and authenticate
    await client.start(phone=phone)

    # Get string session
    session_string = client.session.save()

    print("\n" + "=" * 60)
    print("✅ Session created successfully!")
    print("=" * 60)
    print("\nAdd this to your .env file:")
    print(f"\nTELEGRAM_STRING_SESSION={session_string}")
    print(f"TELEGRAM_API_ID={api_id}")
    print(f"TELEGRAM_API_HASH={api_hash}")
    print("\n" + "=" * 60)

    await client.disconnect()


if __name__ == "__main__":
    print("Telegram Session Generator\n")
    asyncio.run(generate_session())
