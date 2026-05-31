"""
fix_executor.py — Dual-Account FIX 4.4 Execution Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Two simultaneous FIX 4.4 Trade sessions: Live + Demo.

execute_signal()        — fires both accounts via asyncio.gather()
modify_position_sl()    — fires SL modification on both accounts
                          (used by BreakEvenMonitor)

Architecture:
  AsyncFIXSession         — one persistent FIX TCP connection
  DualAccountFIXExecutor  — two AsyncFIXSession instances

Credentials (set in Replit Secrets):
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
import uuid
from datetime import datetime, timezone
from typing import Optional, Callable, Dict

logger = logging.getLogger('fix_executor')

SOH              = b'\x01'
FIX_VERSION      = "FIX.4.4"
HEARTBEAT_INT    = 30
RECONNECT_BASE   = 5.0

# FIX MsgType values
MSG_LOGON        = "A"
MSG_LOGOUT       = "5"
MSG_HEARTBEAT    = "0"
MSG_TEST_REQUEST = "1"
MSG_NEW_ORDER    = "D"
MSG_CANCEL_REPLACE = "G"   # OrderCancelReplaceRequest — used for SL modification
MSG_EXEC_REPORT  = "8"

SIDE_BUY         = "1"
SIDE_SELL        = "2"
ORD_TYPE_MARKET  = "1"
ORD_TYPE_LIMIT   = "2"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIX MESSAGE BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FIXMessage:
    def __init__(self, msg_type: str):
        self._fields: list = []
        self.append(35, msg_type)

    def append(self, tag: int, value) -> 'FIXMessage':
        self._fields.append((tag, str(value)))
        return self

    def encode(self, sender: str, target: str, seq_num: int, sub_id: str = "") -> bytes:
        header_pairs = [
            (49, sender),
            (56, target),
            (34, str(seq_num)),
            (52, datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]),
        ]
        if sub_id:
            header_pairs.append((50, sub_id))

        header = b"".join(f"{t}={v}".encode() + SOH for t, v in header_pairs)
        body   = b"".join(f"{t}={v}".encode() + SOH for t, v in self._fields)
        full_body = header + body
        body_len  = len(full_body)

        prefix   = f"8={FIX_VERSION}".encode() + SOH + f"9={body_len}".encode() + SOH
        full     = prefix + full_body
        checksum = sum(full) % 256
        full    += f"10={checksum:03d}".encode() + SOH
        return full

    @staticmethod
    def parse(raw: bytes) -> Dict[int, str]:
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
    def __init__(
        self,
        account_name:   str,
        host:           str,
        port:           int,
        sender_comp_id: str,
        target_comp_id: str,
        sender_sub_id:  str,
        password:       str,
        on_exec_report: Optional[Callable] = None,
    ):
        self.account_name   = account_name
        self.host           = host
        self.port           = port
        self.sender         = sender_comp_id
        self.target         = target_comp_id
        self.sub_id         = sender_sub_id
        self.password       = password
        self.on_exec_report = on_exec_report

        self._seq           = 1
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._logged_in     = False
        self._running       = False
        self._reconnect_delay = RECONNECT_BASE
        self._orders: Dict[str, dict] = {}   # clOrdId → signal dict

        logger.info(f"[{account_name}] FIX session configured ({host}:{port})")

    # ── Connection ─────────────────────────────────────

    async def connect(self):
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        logger.info(f"[{self.account_name}] TCP connected → {self.host}:{self.port}")
        await self._send_logon()

    async def _send_logon(self):
        msg = FIXMessage(MSG_LOGON)
        msg.append(98,  0)
        msg.append(108, HEARTBEAT_INT)
        msg.append(141, "Y")
        msg.append(554, self.password)
        await self._send(msg)
        logger.info(f"[{self.account_name}] Logon sent")

    async def disconnect(self):
        self._running   = False
        self._logged_in = False
        if self._writer:
            try:
                msg = FIXMessage(MSG_LOGOUT)
                msg.append(58, "Normal")
                await self._send(msg)
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ── Send ───────────────────────────────────────────

    async def _send(self, msg: FIXMessage):
        if not self._writer or self._writer.is_closing():
            raise ConnectionError(f"[{self.account_name}] Writer unavailable")
        raw = msg.encode(self.sender, self.target, self._seq, self.sub_id)
        self._seq += 1
        self._writer.write(raw)
        await self._writer.drain()

    # ── Order execution ────────────────────────────────

    async def send_order(self, signal_dict: dict) -> Optional[str]:
        if not self._logged_in:
            logger.error(f"[{self.account_name}] Not logged in — order skipped")
            return None

        direction = signal_dict['signal_type']
        entry     = float(signal_dict['entry_price'])
        sl        = float(signal_dict['stop_loss'])
        tp        = float(signal_dict['take_profit'])
        lots      = float(signal_dict['lot_size'])
        cl_id     = f"GB-{uuid.uuid4().hex[:12].upper()}"
        side      = SIDE_BUY if direction == "BUY" else SIDE_SELL

        msg = FIXMessage(MSG_NEW_ORDER)
        msg.append(11, cl_id)
        msg.append(55, "XAUUSD")
        msg.append(54, side)
        msg.append(60, datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S"))
        msg.append(38, lots)
        msg.append(40, ORD_TYPE_MARKET)
        msg.append(44, entry)
        msg.append(99, sl)
        msg.append(9001, sl)     # cTrader SL tag
        msg.append(9002, tp)     # cTrader TP tag

        try:
            await self._send(msg)
            self._orders[cl_id] = signal_dict
            logger.info(
                f"[{self.account_name}] ORDER SENT | {direction} {lots}L @ {entry} "
                f"SL={sl} TP={tp} | ClOrdID={cl_id}"
            )
            return cl_id
        except Exception as e:
            logger.error(f"[{self.account_name}] Order send failed: {e}")
            return None

    async def modify_sl(self, orig_cl_ord_id: str, signal_dict: dict, new_sl: float) -> Optional[str]:
        """
        Send OrderCancelReplaceRequest (35=G) to move SL to new_sl.
        Used by BreakEvenMonitor to set SL = entry price.
        """
        if not self._logged_in:
            logger.error(f"[{self.account_name}] Not logged in — SL modification skipped")
            return None

        direction = signal_dict['signal_type']
        entry     = float(signal_dict['entry_price'])
        tp        = float(signal_dict['take_profit'])
        lots      = float(signal_dict.get('lot_size', 0.01))
        new_cl_id = f"BE-{uuid.uuid4().hex[:12].upper()}"
        side      = SIDE_BUY if direction == "BUY" else SIDE_SELL

        msg = FIXMessage(MSG_CANCEL_REPLACE)
        msg.append(11,  new_cl_id)        # New ClOrdID
        msg.append(41,  orig_cl_ord_id)   # OrigClOrdID
        msg.append(55,  "XAUUSD")
        msg.append(54,  side)
        msg.append(60,  datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S"))
        msg.append(38,  lots)
        msg.append(40,  ORD_TYPE_MARKET)
        msg.append(44,  entry)
        msg.append(99,  new_sl)
        msg.append(9001, new_sl)
        msg.append(9002, tp)

        try:
            await self._send(msg)
            logger.info(
                f"[{self.account_name}] SL MODIFIED | OrigID={orig_cl_ord_id} "
                f"NewSL={new_sl} (break-even) | NewID={new_cl_id}"
            )
            return new_cl_id
        except Exception as e:
            logger.error(f"[{self.account_name}] SL modification failed: {e}")
            return None

    # ── Reader loop ────────────────────────────────────

    async def _read_messages(self):
        buffer = b""
        while self._running and self._reader:
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=HEARTBEAT_INT + 5)
                if not chunk:
                    break
                buffer += chunk
                while b"10=" in buffer:
                    end = buffer.find(b"10=")
                    cs_end = buffer.find(SOH, end)
                    if cs_end == -1:
                        break
                    await self._dispatch(buffer[:cs_end + 1])
                    buffer = buffer[cs_end + 1:]
            except asyncio.TimeoutError:
                hb = FIXMessage(MSG_HEARTBEAT)
                try:
                    await self._send(hb)
                except Exception:
                    break
            except Exception as e:
                logger.error(f"[{self.account_name}] Read error: {e}")
                break

    async def _dispatch(self, raw: bytes):
        tags = FIXMessage.parse(raw)
        mt   = tags.get(35, "")

        if mt == MSG_LOGON:
            self._logged_in     = True
            self._reconnect_delay = RECONNECT_BASE
            logger.info(f"[{self.account_name}] Logged in ✓")

        elif mt == MSG_LOGOUT:
            self._logged_in = False
            logger.warning(f"[{self.account_name}] Logout: {tags.get(58, '')}")

        elif mt == MSG_TEST_REQUEST:
            hb = FIXMessage(MSG_HEARTBEAT)
            hb.append(112, tags.get(112, ""))
            await self._send(hb)

        elif mt == MSG_EXEC_REPORT:
            ord_status = tags.get(39, "")
            cl_id      = tags.get(11, "")
            avg_px     = tags.get(31, "N/A")
            status_map = {"0": "New", "1": "Partial", "2": "Filled", "8": "Rejected", "4": "Cancelled"}
            label = status_map.get(ord_status, ord_status)
            if ord_status == "8":
                logger.error(f"[{self.account_name}] REJECTED {cl_id} | {tags.get(58, '')}")
            else:
                logger.info(f"[{self.account_name}] ExecReport {label} | {cl_id} @ {avg_px}")
            if self.on_exec_report:
                await self.on_exec_report(self.account_name, tags)
            self._orders.pop(cl_id, None)

    # ── Reconnect loop ─────────────────────────────────

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                await self.connect()
                await self._read_messages()
            except ConnectionRefusedError:
                logger.error(f"[{self.account_name}] Connection refused ({self.host}:{self.port})")
            except OSError as e:
                logger.error(f"[{self.account_name}] Network: {e}")
            except Exception as e:
                logger.error(f"[{self.account_name}] Error: {e}", exc_info=True)

            if self._running:
                self._logged_in = False
                logger.info(f"[{self.account_name}] Reconnect in {self._reconnect_delay:.0f}s")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 120)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DUAL-ACCOUNT EXECUTOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DualAccountFIXExecutor:
    """
    Manages Live + Demo FIX sessions in parallel.
    execute_signal()    — simultaneous order on both accounts
    modify_position_sl() — simultaneous SL modification on both accounts
    """

    def __init__(self):
        def e(k, d=""): return os.environ.get(k, d).strip()

        self.live_session = AsyncFIXSession(
            "LIVE",
            e("FIX_LIVE_HOST"),
            int(e("FIX_LIVE_TRADE_PORT", "5202")),
            e("FIX_LIVE_SENDER_COMP_ID"),
            e("FIX_LIVE_TARGET_COMP_ID"),
            e("FIX_LIVE_SENDER_SUB_ID"),
            e("FIX_LIVE_PASSWORD"),
            on_exec_report=self._on_exec,
        )
        self.demo_session = AsyncFIXSession(
            "DEMO",
            e("FIX_DEMO_HOST"),
            int(e("FIX_DEMO_TRADE_PORT", "5212")),
            e("FIX_DEMO_SENDER_COMP_ID"),
            e("FIX_DEMO_TARGET_COMP_ID"),
            e("FIX_DEMO_SENDER_SUB_ID"),
            e("FIX_DEMO_PASSWORD"),
            on_exec_report=self._on_exec,
        )
        self._executed = 0
        self._rejected = 0
        logger.info("DualAccountFIXExecutor initialized (LIVE + DEMO)")

    async def _on_exec(self, account: str, tags: dict):
        if tags.get(39) == "8":
            self._rejected += 1
        elif tags.get(39) in ("1", "2"):
            self._executed += 1

    async def execute_signal(self, validated_signal) -> Dict:
        """Fire LIVE + DEMO simultaneously. Returns {live: clOrdId, demo: clOrdId}."""
        sd = validated_signal.to_dict() if hasattr(validated_signal, 'to_dict') else validated_signal

        logger.info(
            f"[DUAL EXEC] {sd['signal_type']} XAUUSD @ {sd['entry_price']} | "
            f"Lot={sd['lot_size']} | SL={sd['stop_loss']} | TP={sd['take_profit']}"
        )

        live_id, demo_id = await asyncio.gather(
            self.live_session.send_order(sd),
            self.demo_session.send_order(sd),
            return_exceptions=True,
        )

        results = {
            "live": str(live_id) if not isinstance(live_id, Exception) else f"ERR:{live_id}",
            "demo": str(demo_id) if not isinstance(demo_id, Exception) else f"ERR:{demo_id}",
        }
        logger.info(f"[DUAL EXEC] Results → LIVE={results['live']} | DEMO={results['demo']}")
        return results

    async def modify_position_sl(self, modification: dict) -> Dict:
        """
        Move SL to break-even on both accounts simultaneously.
        modification dict must contain:
          cl_ord_id_live, cl_ord_id_demo, signal_type, entry_price,
          stop_loss (new SL value), take_profit, lot_size
        """
        new_sl         = float(modification['stop_loss'])
        live_orig_id   = modification.get('cl_ord_id_live', '')
        demo_orig_id   = modification.get('cl_ord_id_demo', '')

        logger.info(
            f"[BE MODIFY] Moving SL to {new_sl} (break-even) | "
            f"LIVE={live_orig_id} | DEMO={demo_orig_id}"
        )

        live_result, demo_result = await asyncio.gather(
            self.live_session.modify_sl(live_orig_id, modification, new_sl),
            self.demo_session.modify_sl(demo_orig_id, modification, new_sl),
            return_exceptions=True,
        )

        results = {
            "live": str(live_result) if not isinstance(live_result, Exception) else f"ERR:{live_result}",
            "demo": str(demo_result) if not isinstance(demo_result, Exception) else f"ERR:{demo_result}",
        }
        logger.info(f"[BE MODIFY] Results → LIVE={results['live']} | DEMO={results['demo']}")
        return results

    async def run_forever(self):
        logger.info("Starting FIX sessions (LIVE + DEMO)")
        await asyncio.gather(
            self.live_session.run_forever(),
            self.demo_session.run_forever(),
        )

    def stats(self) -> dict:
        return {
            "executed":       self._executed,
            "rejected":       self._rejected,
            "live_logged_in": self.live_session._logged_in,
            "demo_logged_in": self.demo_session._logged_in,
        }
