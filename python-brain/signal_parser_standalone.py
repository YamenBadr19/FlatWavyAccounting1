"""
signal_parser_standalone.py
Re-exports SignalParser and TradingSignal so validation tests can import
them without needing Telethon installed (which requires a running session).
"""
from telegram_listener import SignalParser, TradingSignal

__all__ = ["SignalParser", "TradingSignal"]
