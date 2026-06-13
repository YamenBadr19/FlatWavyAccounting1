"""
ctrader_api.py — cTrader Open API v2 (Protobuf over TCP, asyncio-native)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Protocol:
  TCP+TLS → demo.ctraderapi.com:5035
  Messages framed as: [4-byte big-endian length][ProtoMessage bytes]
  ProtoMessage.payload contains the actual request/response protobuf

Auth flow:
  1. ProtoOAApplicationAuthReq  (clientId + clientSecret)
  2. ProtoOAApplicationAuthRes  (expect payloadType 2101)
  3. ProtoOAAccountAuthReq      (ctidTraderAccountId + accessToken)
  4. ProtoOAAccountAuthRes      (expect payloadType 2103)
  → Ready to trade

OAuth token refresh:
  POST https://connect.spotware.com/apps/token
  (api.ctrader.com is not reachable from Replit; connect.spotware.com is)

Compatible interface with MCPExecutor (stats, execute_signal, run_forever).
"""

import asyncio
import json
import logging
import os
import struct
import time
import ssl
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

logger = logging.getLogger('ctrader_api')

# ── Lazy config (so env updates after startup are picked up) ─────────────────

def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

CTRADER_MODE      = _cfg("CTRADER_MODE", "demo").lower()
CTRADER_HOST      = "demo.ctraderapi.com" if CTRADER_MODE == "demo" else "live.ctraderapi.com"
CTRADER_PORT      = 5035
CTRADER_ACCOUNT_ID_RAW = _cfg("CTRADER_ACCOUNT_ID")

OAUTH_TOKEN_URL   = "https://connect.spotware.com/apps/token"

# Reconnection
RECONNECT_BASE    = 5.0
RECONNECT_MAX     = 120.0
HEARTBEAT_EVERY   = 20.0   # seconds between heartbeats
TOKEN_CHECK_EVERY = 3600   # seconds between token checks
TOKEN_WARN_BEFORE = 3600   # renew if < 1h left on access token
REFRESH_WARN_DAYS = 7

# ── Protobuf imports (from the installed ctrader-open-api library) ────────────

try:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
            ProtoMessage,
            ProtoHeartbeatEvent,
        )
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthReq,
            ProtoOAApplicationAuthRes,
            ProtoOAAccountAuthReq,
            ProtoOAAccountAuthRes,
            ProtoOANewOrderReq,
            ProtoOASymbolsListReq,
            ProtoOASymbolsListRes,
            ProtoOATraderReq,
            ProtoOATraderRes,
            ProtoOAGetAccountListByAccessTokenReq,
        )
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAOrderType,
            ProtoOATradeSide,
        )
    _PROTO_AVAILABLE = True
    logger.debug("cTrader protobuf messages loaded OK")
except ImportError as _e:
    _PROTO_AVAILABLE = False
    logger.error(f"ctrader-open-api library not installed: {_e}. Run: pip install ctrader-open-api")

# Known payload types
PT_APP_AUTH_RES     = 2101
PT_ACCT_AUTH_RES    = 2103
PT_ERROR_RES        = 50
PT_HEARTBEAT        = 51
PT_SYMBOLS_LIST_RES = 2115
PT_TRADER_RES       = 2122
PT_ORDER_RES        = 2107  # ProtoOAExecutionEvent


# ── Token state ───────────────────────────────────────────────────────────────

class TokenState:
    def __init__(self, access_token: str, refresh_token: str,
                 access_expires_at: datetime, refresh_expires_at: datetime):
        self.access_token         = access_token
        self.refresh_token        = refresh_token
        self.access_expires_at    = access_expires_at
        self.refresh_expires_at   = refresh_expires_at

    def is_access_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.access_expires_at

    def is_access_expiring_soon(self) -> bool:
        secs = (self.access_expires_at - datetime.now(timezone.utc)).total_seconds()
        return 0 < secs < TOKEN_WARN_BEFORE

    def is_refresh_expiring_soon(self) -> bool:
        secs = (self.refresh_expires_at - datetime.now(timezone.utc)).total_seconds()
        return 0 < secs < REFRESH_WARN_DAYS * 86400

    def days_until_refresh_expires(self) -> float:
        return (self.refresh_expires_at - datetime.now(timezone.utc)).total_seconds() / 86400


# ── Wire protocol helpers ─────────────────────────────────────────────────────

def _encode_message(proto_msg) -> bytes:
    """Wrap a protobuf message in a ProtoMessage frame with 4-byte length prefix."""
    if isinstance(proto_msg, ProtoMessage):
        payload_bytes = proto_msg.SerializeToString()
    else:
        # It's a specific request (e.g. ProtoOAApplicationAuthReq)
        inner = proto_msg.SerializeToString()
        wrapper = ProtoMessage(
            payloadType=proto_msg.payloadType,
            payload=inner,
        )
        payload_bytes = wrapper.SerializeToString()
    length = struct.pack(">I", len(payload_bytes))
    return length + payload_bytes


def _decode_message(data: bytes) -> Optional[Any]:
    """Parse raw bytes into a ProtoMessage."""
    try:
        msg = ProtoMessage()
        msg.ParseFromString(data)
        return msg
    except Exception as e:
        logger.debug(f"Decode error: {e}")
        return None


async def _read_message(reader: asyncio.StreamReader) -> Optional[ProtoMessage]:
    """Read one framed message from the stream (4-byte length + body)."""
    try:
        length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=30)
        length = struct.unpack(">I", length_bytes)[0]
        if length > 15_000_000 or length == 0:
            logger.warning(f"Suspicious message length: {length}")
            return None
        body = await asyncio.wait_for(reader.readexactly(length), timeout=30)
        return _decode_message(body)
    except asyncio.IncompleteReadError:
        return None
    except asyncio.TimeoutError:
        return None


# ── Main client ───────────────────────────────────────────────────────────────

class CTraderOpenAPI:
    """
    Asyncio-native cTrader Open API v2 client.
    Provides the same public interface as MCPExecutor (used by main.py).
    """

    def __init__(self, balance_manager=None, channel_reporter=None):
        self.balance_manager  = balance_manager
        self.channel_reporter = channel_reporter

        self._reader:  Optional[asyncio.StreamReader]  = None
        self._writer:  Optional[asyncio.StreamWriter]  = None
        self._connected       = False
        self._app_authed      = False
        self._acct_authed     = False
        self._running         = False

        self._token:   Optional[TokenState] = None
        self._tasks:   List[asyncio.Task]   = []

        # Symbol cache: name → symbolId
        self._symbols: Dict[str, int] = {}

        # Position tracking
        self._positions: Dict[str, dict] = {}

        # Stats (mirrors MCPExecutor interface)
        self._executed = 0
        self._rejected = 0

        # Pending response futures: payloadType → Future
        self._pending: Dict[int, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None

        account_id = _cfg("CTRADER_ACCOUNT_ID")
        logger.info(
            f"CTraderOpenAPI initialized | Mode: {CTRADER_MODE} | "
            f"Host: {CTRADER_HOST}:{CTRADER_PORT} | Account: {account_id or '(not set)'}"
        )

    # ── Wire send/recv ────────────────────────────────────────────────────────

    async def _send(self, proto_msg) -> bool:
        if not self._writer or self._writer.is_closing():
            return False
        try:
            data = _encode_message(proto_msg)
            self._writer.write(data)
            await self._writer.drain()
            return True
        except Exception as e:
            logger.warning(f"Send error: {e}")
            return False

    async def _recv_loop(self):
        """Background loop that reads incoming messages and dispatches them."""
        while self._running and self._reader:
            try:
                msg = await _read_message(self._reader)
                if msg is None:
                    if self._running:
                        logger.warning("Connection closed by server")
                    break
                await self._dispatch(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Recv loop error: {e}")
                break
        # Signal disconnect
        self._connected   = False
        self._app_authed  = False
        self._acct_authed = False

    async def _dispatch(self, msg: ProtoMessage):
        """Dispatch an incoming ProtoMessage to any waiting futures."""
        pt = msg.payloadType
        logger.debug(f"← payloadType={pt} payload_len={len(msg.payload)}")

        # Heartbeat — reply in kind
        if pt == PT_HEARTBEAT:
            hb = ProtoHeartbeatEvent()
            await self._send(hb)
            return

        # Resolve any pending futures for this payloadType
        fut = self._pending.get(pt)
        if fut and not fut.done():
            fut.set_result(msg)

        # Error response — resolve the generic error key too
        if pt == PT_ERROR_RES:
            for key, fut in list(self._pending.items()):
                if not fut.done():
                    fut.set_result(msg)

    async def _wait_for(self, payload_type: int, timeout: float = 10.0) -> Optional[ProtoMessage]:
        """Register a future and wait for a specific payloadType response."""
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[payload_type] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for payloadType={payload_type}")
            return None
        finally:
            self._pending.pop(payload_type, None)

    # ── Connection & auth ─────────────────────────────────────────────────────

    async def _connect(self) -> bool:
        if not _PROTO_AVAILABLE:
            logger.error("Cannot connect — ctrader-open-api library not installed")
            return False

        try:
            ssl_ctx = ssl.create_default_context()
            logger.info(f"🔌 Connecting to {CTRADER_HOST}:{CTRADER_PORT}...")
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(CTRADER_HOST, CTRADER_PORT, ssl=ssl_ctx),
                timeout=15.0
            )
            logger.info("✅ TCP+TLS connected")
            self._connected = True

            # Start receiver
            self._recv_task = asyncio.create_task(self._recv_loop())

            # Step 1: Application auth
            if not await self._app_auth():
                return False

            # Step 2: Account auth
            if not await self._account_auth():
                return False

            return True

        except asyncio.TimeoutError:
            logger.error("Connection timeout (15s)")
            return False
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def _app_auth(self) -> bool:
        client_id     = _cfg("CTRADER_CLIENT_ID")
        client_secret = _cfg("CTRADER_CLIENT_SECRET")
        if not (client_id and client_secret):
            logger.error("CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET not set")
            return False

        req = ProtoOAApplicationAuthReq()
        req.clientId     = client_id
        req.clientSecret = client_secret

        logger.info("→ ProtoOAApplicationAuthReq")
        if not await self._send(req):
            return False

        res = await self._wait_for(PT_APP_AUTH_RES, timeout=10.0)
        if res is None:
            logger.error("No response to application auth")
            return False
        if res.payloadType == PT_ERROR_RES:
            logger.error(f"Application auth error: {res.payload!r}")
            return False

        logger.info("✅ Application authenticated")
        self._app_authed = True
        return True

    async def _account_auth(self) -> bool:
        access_token = (
            self._token.access_token if self._token
            else _cfg("CTRADER_ACCESS_TOKEN")
        )
        account_id_str = _cfg("CTRADER_ACCOUNT_ID")
        if not (access_token and account_id_str):
            logger.error("CTRADER_ACCESS_TOKEN / CTRADER_ACCOUNT_ID not set")
            return False

        try:
            account_id = int(account_id_str)
        except ValueError:
            logger.error(f"Invalid CTRADER_ACCOUNT_ID: {account_id_str!r}")
            return False

        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken         = access_token

        logger.info(f"→ ProtoOAAccountAuthReq (account={account_id})")
        if not await self._send(req):
            return False

        res = await self._wait_for(PT_ACCT_AUTH_RES, timeout=10.0)
        if res is None:
            logger.error("No response to account auth")
            return False
        if res.payloadType == PT_ERROR_RES:
            logger.error(f"Account auth error: {res.payload!r}")
            return False

        logger.info(f"✅ Account {account_id} authenticated")
        self._acct_authed = True
        return True

    async def _disconnect(self):
        self._connected   = False
        self._app_authed  = False
        self._acct_authed = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None

    # ── Symbol lookup ─────────────────────────────────────────────────────────

    async def _get_symbol_id(self, name: str = "XAUUSD") -> Optional[int]:
        """Look up symbolId by name, caching results."""
        if name in self._symbols:
            return self._symbols[name]

        if not self._acct_authed:
            return None

        account_id = int(_cfg("CTRADER_ACCOUNT_ID", "0"))
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = account_id
        req.includeArchivedSymbols = False

        if not await self._send(req):
            return None

        res = await self._wait_for(PT_SYMBOLS_LIST_RES, timeout=15.0)
        if res is None:
            return None

        try:
            sym_res = ProtoOASymbolsListRes()
            sym_res.ParseFromString(res.payload)
            for sym in sym_res.symbol:
                self._symbols[sym.symbolName] = sym.symbolId
                logger.debug(f"Symbol: {sym.symbolName} → {sym.symbolId}")
        except Exception as e:
            logger.warning(f"Symbol list parse error: {e}")

        found = self._symbols.get(name)
        if found:
            logger.info(f"Symbol {name} → ID {found}")
        else:
            logger.warning(f"Symbol {name!r} not found in account symbol list")
        return found

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        while self._running:
            await asyncio.sleep(HEARTBEAT_EVERY)
            if self._connected and self._writer and not self._writer.is_closing():
                try:
                    hb = ProtoHeartbeatEvent()
                    await self._send(hb)
                except Exception:
                    pass

    # ── Token renewal ─────────────────────────────────────────────────────────

    async def _token_renewal_loop(self):
        logger.info("🔐 Token renewal monitor started")
        while self._running:
            await asyncio.sleep(TOKEN_CHECK_EVERY)
            if self._token is None:
                continue
            try:
                if self._token.is_access_expiring_soon() or self._token.is_access_expired():
                    logger.warning("⚠️ Access Token expiring — refreshing")
                    ok = await self._refresh_token()
                    if ok and self._acct_authed:
                        await self._account_auth()  # re-auth with new token
                if self._token.is_refresh_expiring_soon():
                    days = self._token.days_until_refresh_expires()
                    logger.warning(f"⚠️ Refresh Token expiring in {days:.1f} days — renew manually!")
            except Exception as e:
                logger.error(f"Token renewal error: {e}")

    async def _refresh_token(self) -> bool:
        import aiohttp
        client_id     = _cfg("CTRADER_CLIENT_ID")
        client_secret = _cfg("CTRADER_CLIENT_SECRET")
        refresh_token = self._token.refresh_token if self._token else _cfg("CTRADER_REFRESH_TOKEN")

        if not (client_id and client_secret and refresh_token):
            logger.error("Cannot refresh: missing OAuth credentials")
            return False
        try:
            payload = {
                "grant_type":    "refresh_token",
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(OAUTH_TOKEN_URL, data=payload,
                                  timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        logger.error(f"Token refresh HTTP {r.status}: {await r.text()}")
                        return False
                    data = await r.json()

            new_access  = data.get("access_token", "")
            expires_in  = int(data.get("expires_in", 86400))
            new_refresh = data.get("refresh_token", refresh_token)
            if not new_access:
                logger.error("Token refresh returned empty access_token")
                return False

            expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            if self._token:
                self._token.access_token      = new_access
                self._token.access_expires_at = expiry
                self._token.refresh_token     = new_refresh
            os.environ["CTRADER_ACCESS_TOKEN"]  = new_access
            os.environ["CTRADER_REFRESH_TOKEN"] = new_refresh
            logger.info(f"✅ Access token refreshed | expires in {expires_in}s")
            return True
        except Exception as e:
            logger.error(f"Token refresh exception: {e}")
            return False

    # ── Reconnect loop ────────────────────────────────────────────────────────

    async def _connection_loop(self):
        delay = RECONNECT_BASE
        while self._running:
            await self._disconnect()
            ok = await self._connect()
            if ok:
                delay = RECONNECT_BASE
                # Load symbol IDs after connection
                asyncio.create_task(self._get_symbol_id("XAUUSD"))
                # Wait until disconnected
                while self._running and self._connected:
                    await asyncio.sleep(2)
                    # Check if recv_task died
                    if self._recv_task and self._recv_task.done():
                        logger.warning("Recv task ended — reconnecting")
                        break
            else:
                logger.warning(f"Connection failed — retry in {delay}s")

            if not self._running:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    # ── Order execution ───────────────────────────────────────────────────────

    async def execute_signal(self, signal) -> bool:
        """
        Place a market order from a validated signal.
        signal: dict or object with .to_dict()
        Returns True on success.
        """
        if hasattr(signal, 'to_dict'):
            sd = signal.to_dict()
        else:
            sd = dict(signal)

        direction   = str(sd.get("signal_type", "BUY")).upper()
        lot_size    = float(sd.get("lot_size",    0.01))
        stop_loss   = float(sd.get("stop_loss",   0.0))
        take_profit = float(sd.get("take_profit", 0.0))

        if not self._acct_authed:
            logger.error("❌ Cannot execute — account not authenticated")
            self._rejected += 1
            return False

        account_id = int(_cfg("CTRADER_ACCOUNT_ID", "0"))
        symbol_id  = await self._get_symbol_id("XAUUSD")

        if not symbol_id:
            logger.error("❌ Cannot execute — XAUUSD symbol ID unknown")
            self._rejected += 1
            return False

        volume = int(lot_size * 100_000)  # lots → units (1 lot = 100k units)

        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = account_id
        req.symbolId            = symbol_id
        req.orderType           = ProtoOAOrderType.Value("MARKET")
        req.tradeSide           = ProtoOATradeSide.Value(direction)
        req.volume              = volume
        if stop_loss:
            req.stopLoss  = round(stop_loss, 2)
        if take_profit:
            req.takeProfit = round(take_profit, 2)
        req.label = f"GB-{uuid.uuid4().hex[:8].upper()}"

        logger.info(
            f"📤 Order | {direction} {lot_size}L XAUUSD | "
            f"SL={stop_loss:.2f} TP={take_profit:.2f} | label={req.label}"
        )

        label = req.label
        if not await self._send(req):
            logger.error("❌ Order send failed")
            self._rejected += 1
            return {"mcp": "SEND_FAILED", "live": "SEND_FAILED", "demo": "OA"}

        # Wait for execution event (payloadType 2107)
        res = await self._wait_for(PT_ORDER_RES, timeout=10.0)
        if res is None:
            # Optimistic: the server may have accepted it without a quick response
            logger.warning("⚠️ No execution event received in 10s — order may still have gone through")
            self._executed += 1
            return {"mcp": label, "live": label, "demo": "OA"}

        if res.payloadType == PT_ERROR_RES:
            logger.error(f"❌ Order rejected by server (payloadType={res.payloadType})")
            self._rejected += 1
            return {"mcp": "REJECTED", "live": "REJECTED", "demo": "OA"}

        self._executed += 1
        logger.info(f"✅ Order executed | payloadType={res.payloadType}")
        return {"mcp": label, "live": label, "demo": "OA"}

    # ── MCPExecutor-compatible interface ──────────────────────────────────────

    def stats(self) -> dict:
        return {
            "mcp_connected":    self._acct_authed,
            "live_logged_in":   self._acct_authed,
            "demo_logged_in":   False,
            "executed":         self._executed,
            "rejected":         self._rejected,
            "open_positions":   len(self._positions),
        }

    def is_connected(self) -> bool:
        return self._acct_authed

    async def get_positions(self) -> list:
        return list(self._positions.values())

    async def modify_position_sl(self, modification: dict) -> dict:
        return {"mcp": "unsupported", "live": "unsupported", "demo": "OA"}

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run_forever(self):
        self._running = True
        logger.info("CTraderOpenAPI executor started")

        # Initialise token state from env
        access_token  = _cfg("CTRADER_ACCESS_TOKEN")
        refresh_token = _cfg("CTRADER_REFRESH_TOKEN")
        if access_token:
            self._token = TokenState(
                access_token       = access_token,
                refresh_token      = refresh_token,
                access_expires_at  = datetime.now(timezone.utc) + timedelta(hours=24),
                refresh_expires_at = datetime.now(timezone.utc) + timedelta(days=90),
            )

        self._tasks = [
            asyncio.create_task(self._connection_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._token_renewal_loop()),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("CTraderOpenAPI executor cancelled")
        except Exception as e:
            logger.error(f"CTraderOpenAPI executor error: {e}", exc_info=True)
        finally:
            self._running = False
            await self._disconnect()
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            logger.info("CTraderOpenAPI executor stopped")

    def get_token_status(self) -> dict:
        if not self._token:
            return {"status": "not_initialized"}
        return {
            "connected":                  self._acct_authed,
            "access_token_expires_at":    self._token.access_expires_at.isoformat(),
            "refresh_token_expires_at":   self._token.refresh_expires_at.isoformat(),
            "access_token_expired":       self._token.is_access_expired(),
            "access_token_expiring_soon": self._token.is_access_expiring_soon(),
            "refresh_token_expiring_soon": self._token.is_refresh_expiring_soon(),
            "days_until_refresh_expires": self._token.days_until_refresh_expires(),
        }
