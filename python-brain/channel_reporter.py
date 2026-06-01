"""
ChannelReporter — Private Signal Logging & Position Tracking Channel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Forwards every processed signal to PRIVATE_CHANNEL_ID with:
  - Original signal source  - Entry / SL / TP  - Lot size
  - cTrader Position ID     - Confluence level  - Timestamp

Uses Telegram as the source of truth for open positions.
No local database — all state lives in the channel messages.

Message lifecycle:
  1. Signal validated    → 🟡 OPEN message sent to channel
  2. Break-even fires    → Message edited to 🔵 BE ACTIVE
  3. TP1 hit             → Message edited to ✅ TP1 HIT
  4. TP2 hit             → Message edited to ✅ TP2 HIT
  5. SL hit / closed     → Message edited to 🔴 STOPPED OUT / CLOSED
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict

logger = logging.getLogger('channel')

PRIVATE_CHANNEL_ID = int(os.environ.get("PRIVATE_CHANNEL_ID", "0"))
REPORTING_ENABLED  = PRIVATE_CHANNEL_ID != 0


class ChannelReporter:
    """
    Sends and edits signal cards in a private Telegram channel.
    Shares the TelegramClient from TelegramListener (single connection).
    """

    def __init__(self):
        self._client = None
        self._msg_map:       Dict[str, int]  = {}   # position_id  → message_id
        self._pending_map:   Dict[str, int]  = {}   # cl_ord_id    → message_id (before pos ID known)
        self._signal_map:    Dict[str, dict] = {}   # position_id  → signal dict
        if REPORTING_ENABLED:
            logger.info(f"ChannelReporter ready → PRIVATE_CHANNEL_ID={PRIVATE_CHANNEL_ID}")
        else:
            logger.warning(
                "ChannelReporter: PRIVATE_CHANNEL_ID not set — "
                "channel reporting disabled (set it in Replit Secrets)"
            )

    def set_client(self, client):
        """Inject the shared TelegramClient after it connects."""
        self._client = client

    # ── Signal reporting ───────────────────────────────────────────

    async def report_signal(
        self,
        signal_dict:  dict,
        cl_ord_id:    str  = "",
        source:       str  = "SIGNALS",
    ) -> Optional[int]:
        """
        Send a new 🟡 OPEN signal card to the private channel.
        Returns the Telegram message ID (used later for editing).
        """
        if not REPORTING_ENABLED or not self._client:
            return None
        try:
            text = self._format_card(signal_dict, position_id="", source=source, status="🟡 OPEN")
            msg  = await self._client.send_message(PRIVATE_CHANNEL_ID, text, parse_mode="md")
            if cl_ord_id:
                self._pending_map[cl_ord_id] = msg.id
                self._signal_map[cl_ord_id]  = signal_dict
            logger.info(f"[CHANNEL] Signal card sent | ClOrdID={cl_ord_id} | MsgID={msg.id}")
            return msg.id
        except Exception as e:
            logger.error(f"[CHANNEL] report_signal failed: {e}")
            return None

    def register_position_id(self, cl_ord_id: str, position_id: str):
        """
        Map a live cTrader Position ID to the pending message.
        Called by fix_executor._on_exec when execution report arrives.
        """
        if cl_ord_id in self._pending_map:
            msg_id = self._pending_map.pop(cl_ord_id)
            sig    = self._signal_map.pop(cl_ord_id, {})
            self._msg_map[position_id]   = msg_id
            self._signal_map[position_id] = sig
            logger.info(f"[CHANNEL] Registered PosID={position_id} → MsgID={msg_id}")
            asyncio.ensure_future(self._update_card_with_position_id(position_id, sig, msg_id))

    async def _update_card_with_position_id(self, position_id: str, sig: dict, msg_id: int):
        """Edit the signal card to include the official cTrader Position ID."""
        if not REPORTING_ENABLED or not self._client:
            return
        try:
            text = self._format_card(sig, position_id=position_id, source="SIGNALS", status="🟡 OPEN")
            await self._client.edit_message(PRIVATE_CHANNEL_ID, msg_id, text, parse_mode="md")
        except Exception as e:
            logger.debug(f"[CHANNEL] position_id card update: {e}")

    # ── Status updates (edit existing messages) ────────────────────

    async def update_break_even(self, position_id: str):
        """Edit the card: break-even SL move fired."""
        await self._edit_status(position_id, "🔵 BREAK-EVEN — SL moved to Entry")

    async def update_tp1(self, position_id: str, price: float):
        """Edit the card: TP1 hit, SL moved to entry."""
        await self._edit_status(position_id, f"✅ TP1 HIT @ {price:.2f} — SL → Entry")

    async def update_tp2(self, position_id: str, price: float):
        """Edit the card: TP2 hit, SL moved to TP1."""
        await self._edit_status(position_id, f"✅ TP2 HIT @ {price:.2f} — SL → TP1")

    async def update_trailing(self, position_id: str, new_sl: float):
        """Edit the card: trailing stop moved."""
        await self._edit_status(position_id, f"〰️ TRAILING — New SL @ {new_sl:.2f}")

    async def update_closed(self, position_id: str, reason: str, price: float):
        """Edit the card: position closed (SL hit, manual close, etc.)."""
        await self._edit_status(position_id, f"🔴 CLOSED — {reason} @ {price:.2f}")
        self._msg_map.pop(position_id, None)
        self._signal_map.pop(position_id, None)

    async def _edit_status(self, position_id: str, status: str):
        if not REPORTING_ENABLED or not self._client:
            return
        msg_id = self._msg_map.get(position_id)
        if not msg_id:
            return
        sig = self._signal_map.get(position_id, {})
        try:
            text = self._format_card(sig, position_id=position_id, source="SIGNALS", status=status)
            await self._client.edit_message(PRIVATE_CHANNEL_ID, msg_id, text, parse_mode="md")
            logger.info(f"[CHANNEL] Updated PosID={position_id} → {status}")
        except Exception as e:
            logger.debug(f"[CHANNEL] _edit_status failed ({position_id}): {e}")

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _format_card(sig: dict, position_id: str, source: str, status: str) -> str:
        direction = sig.get("signal_type", "?")
        entry     = float(sig.get("entry_price", 0))
        sl        = float(sig.get("stop_loss",   0))
        tp        = float(sig.get("take_profit", 0))
        lot       = sig.get("lot_size", "?")
        conf      = sig.get("confluence_level", 0)
        ts        = sig.get("timestamp", "")[:19].replace("T", " ")
        arrow     = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
        pid_line  = f"\n📌 Position ID: `{position_id}`" if position_id else ""

        return (
            f"**Gold Blueprint — {arrow} XAUUSD**\n"
            f"{'━' * 32}\n"
            f"📥 Source: {source}\n"
            f"💰 Entry:  `{entry:.2f}`\n"
            f"🛡  SL:    `{sl:.2f}`\n"
            f"🎯 TP:    `{tp:.2f}`\n"
            f"📦 Lot:   `{lot}`   Confluence: `{conf}/3`"
            f"{pid_line}\n"
            f"⏰ {ts} UTC\n"
            f"{'━' * 32}\n"
            f"{status}"
        )

    def get_open_positions(self) -> dict:
        """Return a snapshot of currently tracked open positions."""
        return {pid: dict(sig) for pid, sig in self._signal_map.items()
                if pid in self._msg_map}
