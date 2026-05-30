"""
fix_executor.py — Dual-Account FIX 4.4 Execution Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Manages two simultaneous FIX 4.4 Trade sessions:
  - Live account  (FIX_LIVE_*)
  - Demo account  (FIX_DEMO_*)

When a validated signal arrives, asyncio.gather() fires both accounts
at the EXACT same time — not sequentially.

Architecture:
  AsyncFIXSession  — manages one FIX TCP connection (logon, heartbeat,
                     order sending, message parsing, reconnection)
  DualAccountFIXExecutor — holds two AsyncFIXSession instances and
                           exposes execute_signal() for simultaneous dispatch

FIX Protocol: FIX 4.4 (cTrader standard)
Transport:    raw asyncio TCP streams (no external FIX framework required)

Credential env vars (set all 10 pairs):
  FIX_LIVE_HOST, FIX_LIVE_TRADE_PORT
  FIX_LIVE_SENDER_COMP_ID, FIX_LIVE_TARGET_COMP_ID
  FIX_LIVE_SENDER_SUB_ID, FIX_LIVE_PASSWORD

  FIX_DEMO_HOST, FIX_DEMO_TRADE_PORT
  FIX_DEMO_SENDER_COMP_ID, FIX_DEMO_TARGET_COMP_ID
  FIX_DEMO_SENDER_SUB_ID, FIX_DEMO_PASSWORD
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger('fix_executor')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIX PROTOCOL CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOH          = b'\x01'
FIX_VERSION  = "FIX.4.4"
HEARTBEAT_INT = 30          # seconds
RECONNECT_DELAY_BASE = 5.0  # seconds, doubles on each failure

# FIX MsgType tags
LOGON          = "A"
LOGOUT         = "5"
HEARTBEAT      = "0"
TEST_REQUEST   = "1"
RESEND_REQUEST = "2"
NEW_ORDER      = "D"
EXEC_REPORT    = "8"

# FIX Side
SIDE_BUY  = "1"
SIDE_SELL = "2"

# FIX OrdType
ORD_TYPE_MARKET = "1"
ORD_TYPE_LIMIT  = "2"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIX MESSAGE BUILDER (no external library required)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FIXMessage:
    """
    Builds FIX 4.4 messages as raw bytes.
    Tags are stored in an ordered list to preserve field ordering.
    BeginString (8), BodyLength (9), and CheckSum (10) are computed automatically.
    """

    def __init__(self, msg_type: str):
        self._fields: list = []
        self.append(35, msg_type)

    def append(self, tag: int, value) -> 'FIXMessage':
        self._fields.append((tag, str(value)))
        return self

    def _build_body(self) -> bytes:
        body = b""
        for tag, value in self._fields:
            body += f"{tag}={value}".encode() + SOH
        return body

    def encode(self, sender_comp_id: str, target_comp_id: str,
               seq_num: int, sender_sub_id: str = "") -> bytes:
        header_fields = [
            (49, sender_comp_id),
            (56, target_comp_id),
            (34, str(seq_num)),
            (52, datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]),
        ]
        if sender_sub_id:
            header_fields.append((50, sender_sub_id))

        header_bytes = b""
        for tag, value in header_fields:
            header_bytes += f"{tag}={value}".encode() + SOH

        body = header_bytes + self._build_body()
        body_len = len(body)

        prefix = f"8={FIX_VERSION}".encode() + SOH + f"9={body_len}".encode() + SOH
        full = prefix + body
        checksum = sum(full) % 256
        full += f"10={checksum:03d}".encode() + SOH
        return full

    @staticmethod
    def parse(raw: bytes) -> dict:
        """Parse a FIX message into a tag→value dict."""
        result = {}
        for pair in raw.split(SOH):
            if b'=' in pair:
                tag, _, value = pair.partition(b'=')
                try:
                    result[int(tag)] = value.decode(errors='replace')
                except ValueError:
                    pass
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC FIX SESSION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AsyncFIXSession:
    """
    Manages one persistent FIX 4.4 TCP connection.

    Handles: logon, logout, heartbeat, test request response,
             order submission, execution report callbacks,
             auto-reconnect with exponential backoff.
    """

    def __init__(
        self,
        account_name: str,
        host: str,
        port: int,
        sender_comp_id: str,
        target_comp_id: str,
        sender_sub_id: str,
        password: str,
        on_exec_report: Optional[Callable] = None,
    ):
        self.account_name    = account_name
        self.host            = host
        self.port            = port
        self.sender_comp_id  = sender_comp_id
        self.target_comp_id  = target_comp_id
        self.sender_sub_id   = sender_sub_id
        self.password        = password
        self.on_exec_report  = on_exec_report

        self._seq_num        = 1
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._logged_in      = False
        self._running        = False
        self._reconnect_delay = RECONNECT_DELAY_BASE
        self._pending_orders: dict = {}   # clOrdId → original signal dict

        logger.info(f"[{account_name}] FIX session configured ({host}:{port})")

    # ── Connection management ────────────────────────────

    async def connect(self):
        """Open TCP connection and send Logon."""
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        logger.info(f"[{self.account_name}] TCP connected to {self.host}:{self.port}")
        await self._send_logon()

    async def _send_logon(self):
        msg = FIXMessage(LOGON)
        msg.append(98, 0)                       # EncryptMethod = None
        msg.append(108, HEARTBEAT_INT)          # HeartBtInt
        msg.append(141, "Y")                    # ResetSeqNumFlag
        msg.append(554, self.password)          # Password
        await self._send(msg)
        logger.info(f"[{self.account_name}] Logon sent")

    async def _send_logout(self):
        msg = FIXMessage(LOGOUT)
        msg.append(58, "Normal logout")
        try:
            await self._send(msg)
        except Exception:
            pass

    async def disconnect(self):
        """Graceful logout and TCP close."""
        self._running = False
        self._logged_in = False
        if self._writer:
            await self._send_logout()
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        logger.info(f"[{self.account_name}] Disconnected")

    # ── Send helpers ─────────────────────────────────────

    async def _send(self, msg: FIXMessage):
        if self._writer is None or self._writer.is_closing():
            raise ConnectionError(f"[{self.account_name}] Writer not available")
        raw = msg.encode(
            self.sender_comp_id,
            self.target_comp_id,
            self._seq_num,
            self.sender_sub_id,
        )
        self._seq_num += 1
        self._writer.write(raw)
        await self._writer.drain()

    # ── Order execution ──────────────────────────────────

    async def send_order(self, signal_dict: dict) -> Optional[str]:
        """
        Send a NewOrderSingle (35=D) for a validated signal.
        Returns the ClOrdID if sent, None if failed.
        """
        if not self._logged_in:
            logger.error(f"[{self.account_name}] Cannot send order — not logged in")
            return None

        signal_type = signal_dict['signal_type']
        entry_price = float(signal_dict['entry_price'])
        stop_loss   = float(signal_dict['stop_loss'])
        take_profit = float(signal_dict['take_profit'])
        lot_size    = float(signal_dict['lot_size'])

        cl_ord_id = f"GB-{uuid.uuid4().hex[:12].upper()}"
        side      = SIDE_BUY if signal_type == "BUY" else SIDE_SELL
        transact_time = datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S")

        msg = FIXMessage(NEW_ORDER)
        msg.append(11, cl_ord_id)           # ClOrdID
        msg.append(55, "XAUUSD")            # Symbol
        msg.append(54, side)                # Side
        msg.append(60, transact_time)       # TransactTime
        msg.append(38, lot_size)            # OrderQty (in lots)
        msg.append(40, ORD_TYPE_MARKET)     # OrdType = Market
        msg.append(99, stop_loss)           # StopPx (broker SL)
        msg.append(44, take_profit)         # Price (used as TP for limit bracket)
        # cTrader custom tags for SL/TP on market orders:
        msg.append(9001, stop_loss)         # Custom SL tag (cTrader extension)
        msg.append(9002, take_profit)       # Custom TP tag (cTrader extension)

        try:
            await self._send(msg)
            self._pending_orders[cl_ord_id] = signal_dict
            logger.info(
                f"[{self.account_name}] Order sent | {signal_type} XAUUSD "
                f"@ {entry_price} | SL={stop_loss} | TP={take_profit} | "
                f"Lot={lot_size} | ClOrdID={cl_ord_id}"
            )
            return cl_ord_id
        except Exception as e:
            logger.error(f"[{self.account_name}] Order send failed: {e}")
            return None

    # ── Message reader loop ──────────────────────────────

    async def _read_messages(self):
        """Continuously read and dispatch incoming FIX messages."""
        buffer = b""
        while self._running and self._reader:
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=HEARTBEAT_INT + 5)
                if not chunk:
                    logger.warning(f"[{self.account_name}] Connection closed by server")
                    break
                buffer += chunk

                while SOH in buffer:
                    # Find complete FIX message ending with 10=xxx\x01
                    end = buffer.find(b"10=")
                    if end == -1:
                        break
                    checksum_end = buffer.find(SOH, end)
                    if checksum_end == -1:
                        break
                    raw_msg = buffer[:checksum_end + 1]
                    buffer = buffer[checksum_end + 1:]
                    await self._dispatch(raw_msg)

            except asyncio.TimeoutError:
                await self._send_heartbeat()
            except Exception as e:
                logger.error(f"[{self.account_name}] Read error: {e}")
                break

    async def _dispatch(self, raw: bytes):
        """Route an incoming FIX message to the correct handler."""
        tags = FIXMessage.parse(raw)
        msg_type = tags.get(35, "")

        if msg_type == LOGON:
            self._logged_in = True
            self._reconnect_delay = RECONNECT_DELAY_BASE
            logger.info(f"[{self.account_name}] Logon confirmed — session active")

        elif msg_type == LOGOUT:
            reason = tags.get(58, "No reason")
            logger.warning(f"[{self.account_name}] Logout received: {reason}")
            self._logged_in = False

        elif msg_type == HEARTBEAT:
            pass  # Normal keepalive

        elif msg_type == TEST_REQUEST:
            # Must respond with Heartbeat echoing the TestReqID
            test_req_id = tags.get(112, "")
            hb = FIXMessage(HEARTBEAT)
            hb.append(112, test_req_id)
            await self._send(hb)

        elif msg_type == EXEC_REPORT:
            await self._handle_exec_report(tags)

        else:
            logger.debug(f"[{self.account_name}] Unhandled MsgType={msg_type}")

    async def _handle_exec_report(self, tags: dict):
        """Process execution report (order fill confirmation)."""
        cl_ord_id  = tags.get(11, "")
        ord_status = tags.get(39, "")    # 0=New, 1=Partial, 2=Filled, 8=Rejected
        exec_type  = tags.get(150, "")
        avg_px     = tags.get(31, "N/A")
        reject_rsn = tags.get(103, "")
        text       = tags.get(58, "")

        status_map = {"0": "New", "1": "Partial Fill", "2": "Filled", "8": "Rejected", "4": "Cancelled"}
        status_str = status_map.get(ord_status, ord_status)

        if ord_status == "8":
            logger.error(
                f"[{self.account_name}] Order REJECTED | ClOrdID={cl_ord_id} | "
                f"Reason={reject_rsn} | Text={text}"
            )
        elif ord_status in ("1", "2"):
            logger.info(
                f"[{self.account_name}] Order {status_str} | ClOrdID={cl_ord_id} | "
                f"AvgPx={avg_px}"
            )

        if self.on_exec_report:
            try:
                await self.on_exec_report(self.account_name, tags)
            except Exception as e:
                logger.error(f"[{self.account_name}] exec_report callback error: {e}")

        self._pending_orders.pop(cl_ord_id, None)

    async def _send_heartbeat(self):
        msg = FIXMessage(HEARTBEAT)
        try:
            await self._send(msg)
        except Exception:
            pass

    # ── Reconnect loop ───────────────────────────────────

    async def run_forever(self):
        """Main session loop with automatic reconnect on disconnect."""
        self._running = True
        while self._running:
            try:
                await self.connect()
                await self._read_messages()
            except ConnectionRefusedError:
                logger.error(f"[{self.account_name}] Connection refused — check host/port")
            except OSError as e:
                logger.error(f"[{self.account_name}] Network error: {e}")
            except Exception as e:
                logger.error(f"[{self.account_name}] Session error: {e}", exc_info=True)

            if self._running:
                self._logged_in = False
                logger.info(
                    f"[{self.account_name}] Reconnecting in {self._reconnect_delay:.0f}s..."
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 120)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DUAL-ACCOUNT EXECUTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DualAccountFIXExecutor:
    """
    Holds two AsyncFIXSession instances (Live + Demo).
    execute_signal() fires both simultaneously via asyncio.gather().

    Both accounts receive the exact same order at the same millisecond.
    If one account's session is not yet logged in, it logs a warning
    and skips that account rather than blocking the other.
    """

    def __init__(self):
        def _env(key: str, default: str = "") -> str:
            return os.environ.get(key, default).strip()

        live_host     = _env("FIX_LIVE_HOST")
        live_port     = int(_env("FIX_LIVE_TRADE_PORT", "5202"))
        live_sender   = _env("FIX_LIVE_SENDER_COMP_ID")
        live_target   = _env("FIX_LIVE_TARGET_COMP_ID")
        live_sub_id   = _env("FIX_LIVE_SENDER_SUB_ID")
        live_password = _env("FIX_LIVE_PASSWORD")

        demo_host     = _env("FIX_DEMO_HOST")
        demo_port     = int(_env("FIX_DEMO_TRADE_PORT", "5212"))
        demo_sender   = _env("FIX_DEMO_SENDER_COMP_ID")
        demo_target   = _env("FIX_DEMO_TARGET_COMP_ID")
        demo_sub_id   = _env("FIX_DEMO_SENDER_SUB_ID")
        demo_password = _env("FIX_DEMO_PASSWORD")

        self.live_session = AsyncFIXSession(
            account_name   = "LIVE",
            host           = live_host,
            port           = live_port,
            sender_comp_id = live_sender,
            target_comp_id = live_target,
            sender_sub_id  = live_sub_id,
            password       = live_password,
            on_exec_report = self._on_execution,
        )

        self.demo_session = AsyncFIXSession(
            account_name   = "DEMO",
            host           = demo_host,
            port           = demo_port,
            sender_comp_id = demo_sender,
            target_comp_id = demo_target,
            sender_sub_id  = demo_sub_id,
            password       = demo_password,
            on_exec_report = self._on_execution,
        )

        self._executed_count = 0
        self._rejected_count = 0
        logger.info("DualAccountFIXExecutor initialized (LIVE + DEMO)")

    async def _on_execution(self, account_name: str, tags: dict):
        ord_status = tags.get(39, "")
        if ord_status == "8":
            self._rejected_count += 1
        elif ord_status in ("1", "2"):
            self._executed_count += 1

    async def execute_signal(self, validated_signal) -> dict:
        """
        Fire execution on both accounts simultaneously.
        Returns a dict with results for each account.
        """
        signal_dict = validated_signal.to_dict() if hasattr(validated_signal, 'to_dict') else validated_signal

        logger.info(
            f"[EXECUTE] Firing {signal_dict['signal_type']} XAUUSD @ {signal_dict['entry_price']} "
            f"| Lot={signal_dict['lot_size']} | Confluence={signal_dict.get('confluence_level', '?')}/5 "
            f"| Sending to LIVE + DEMO simultaneously"
        )

        live_task = asyncio.create_task(self.live_session.send_order(signal_dict))
        demo_task = asyncio.create_task(self.demo_session.send_order(signal_dict))

        live_id, demo_id = await asyncio.gather(live_task, demo_task, return_exceptions=True)

        results = {
            "live": str(live_id) if live_id and not isinstance(live_id, Exception) else f"FAILED: {live_id}",
            "demo": str(demo_id) if demo_id and not isinstance(demo_id, Exception) else f"FAILED: {demo_id}",
        }

        logger.info(f"[EXECUTE] Results → LIVE={results['live']} | DEMO={results['demo']}")
        return results

    async def run_forever(self):
        """Keep both FIX sessions alive simultaneously."""
        logger.info("Starting both FIX sessions (LIVE + DEMO)")
        await asyncio.gather(
            self.live_session.run_forever(),
            self.demo_session.run_forever(),
        )

    def stats(self) -> dict:
        return {
            "executed": self._executed_count,
            "rejected": self._rejected_count,
            "live_logged_in": self.live_session._logged_in,
            "demo_logged_in": self.demo_session._logged_in,
        }
