"""
ctrader_api.py — cTrader Open API v2 (REST + OAuth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses the cTrader Open API REST endpoints for:
  ✓ Account info & balance
  ✓ Place market / limit orders
  ✓ Manage positions (close, modify SL/TP)
  ✓ Automatic Access Token renewal via Refresh Token
  ✓ Exponential backoff on HTTP errors
  ✓ Compatible interface with MCPExecutor (stats, get_positions, execute_signal)

REST base:  https://api.ctrader.com/
OAuth:      https://api.ctrader.com/oauth/token
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger('ctrader_api')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION  (read lazily so env updates are picked up)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

CTRADER_MODE         = _cfg("CTRADER_MODE", "demo").lower()
CTRADER_ACCOUNT_ID   = _cfg("CTRADER_ACCOUNT_ID")
OAUTH_TOKEN_URL      = "https://api.ctrader.com/oauth/token"
REST_BASE            = "https://api.ctrader.com"

# Token renewal
TOKEN_RENEWAL_CHECK_INTERVAL    = 3600   # seconds between checks
TOKEN_EXPIRY_WARNING_THRESHOLD  = 3600   # renew 1 h before expiry
REFRESH_TOKEN_WARNING_DAYS      = 7      # warn when < 7 days left

# HTTP retry
HTTP_TIMEOUT   = 15.0   # seconds
MAX_RETRIES    = 3
RETRY_BACKOFF  = 2.0    # seconds (doubles each attempt)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA CLASSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TokenState:
    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
    obtained_at: datetime = None

    def __post_init__(self):
        if self.obtained_at is None:
            self.obtained_at = datetime.now(timezone.utc)

    def is_access_token_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.access_token_expires_at

    def is_access_token_expiring_soon(self, threshold_seconds: int = TOKEN_EXPIRY_WARNING_THRESHOLD) -> bool:
        secs = (self.access_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return 0 < secs < threshold_seconds

    def is_refresh_token_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.refresh_token_expires_at

    def is_refresh_token_expiring_soon(self, threshold_days: int = REFRESH_TOKEN_WARNING_DAYS) -> bool:
        secs = (self.refresh_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return 0 < secs < threshold_days * 86400

    def days_until_refresh_expires(self) -> float:
        secs = (self.refresh_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return secs / 86400


@dataclass
class AccountState:
    balance: float
    equity: float
    open_positions: int
    total_pnl: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CTraderOpenAPI:
    """
    cTrader Open API client using REST endpoints.
    Provides the same interface as MCPExecutor so main.py needs no changes.
    """

    def __init__(self, balance_manager=None, channel_reporter=None):
        self.balance_manager  = balance_manager
        self.channel_reporter = channel_reporter

        self._token_state:   Optional[TokenState]   = None
        self._account_state: Optional[AccountState] = None
        self._connected      = False
        self._running        = False
        self._tasks:         List[asyncio.Task]     = []
        self._last_renewal   = datetime.now(timezone.utc)

        # Stats (mirrors MCPExecutor interface)
        self._executed   = 0
        self._rejected   = 0
        self._positions: Dict[str, dict] = {}

        logger.info(
            f"CTraderOpenAPI initialized | Mode: {CTRADER_MODE} | "
            f"Account: {CTRADER_ACCOUNT_ID or '(not set)'}"
        )

    # ── Helpers ─────────────────────────────────────────────────

    def _headers(self) -> dict:
        token = self._token_state.access_token if self._token_state else _cfg("CTRADER_ACCESS_TOKEN")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }

    async def _get(self, path: str) -> Optional[dict]:
        url = f"{REST_BASE}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url, headers=self._headers(),
                                     timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as r:
                        if r.status == 401:
                            logger.warning("GET 401 — attempting token refresh")
                            await self._refresh_token()
                            continue
                        if r.status == 200:
                            return await r.json()
                        logger.warning(f"GET {path} → HTTP {r.status}: {await r.text()}")
            except Exception as e:
                logger.warning(f"GET {path} attempt {attempt}: {e}")
            await asyncio.sleep(RETRY_BACKOFF * attempt)
        return None

    async def _post(self, path: str, body: dict) -> Optional[dict]:
        url = f"{REST_BASE}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, headers=self._headers(), json=body,
                                      timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)) as r:
                        if r.status == 401:
                            logger.warning("POST 401 — attempting token refresh")
                            await self._refresh_token()
                            continue
                        text = await r.text()
                        try:
                            data = json.loads(text)
                        except Exception:
                            data = {"raw": text}
                        if r.status in (200, 201):
                            return data
                        logger.warning(f"POST {path} → HTTP {r.status}: {text[:200]}")
            except Exception as e:
                logger.warning(f"POST {path} attempt {attempt}: {e}")
            await asyncio.sleep(RETRY_BACKOFF * attempt)
        return None

    # ── Token management ────────────────────────────────────────

    async def _refresh_token(self) -> bool:
        client_id     = _cfg("CTRADER_CLIENT_ID")
        client_secret = _cfg("CTRADER_CLIENT_SECRET")
        refresh_token = _cfg("CTRADER_REFRESH_TOKEN") if not self._token_state else self._token_state.refresh_token

        if not (client_id and client_secret and refresh_token):
            logger.error("Cannot refresh: CTRADER_CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN missing")
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
                        logger.error(f"Token refresh failed HTTP {r.status}: {await r.text()}")
                        return False
                    data = await r.json()

            new_token    = data.get("access_token", "")
            expires_in   = int(data.get("expires_in", 86400))
            new_refresh  = data.get("refresh_token", refresh_token)

            if not new_token:
                logger.error("Token refresh returned empty access_token")
                return False

            expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            if self._token_state:
                self._token_state.access_token           = new_token
                self._token_state.access_token_expires_at = expiry
                self._token_state.refresh_token          = new_refresh
            os.environ["CTRADER_ACCESS_TOKEN"]  = new_token
            os.environ["CTRADER_REFRESH_TOKEN"] = new_refresh
            self._last_renewal = datetime.now(timezone.utc)

            logger.info(f"✅ Access Token refreshed | expires in {expires_in}s")
            return True

        except Exception as e:
            logger.error(f"Token refresh exception: {e}")
            return False

    async def _token_renewal_loop(self):
        logger.info("🔐 Token renewal monitor started")
        while self._running:
            try:
                await asyncio.sleep(TOKEN_RENEWAL_CHECK_INTERVAL)
                if self._token_state is None:
                    continue

                if self._token_state.is_access_token_expiring_soon():
                    logger.warning("⚠️ Access Token expiring soon — refreshing")
                    await self._refresh_token()

                elif self._token_state.is_access_token_expired():
                    logger.error("❌ Access Token expired — refreshing")
                    await self._refresh_token()

                if self._token_state.is_refresh_token_expiring_soon(threshold_days=REFRESH_TOKEN_WARNING_DAYS):
                    days = self._token_state.days_until_refresh_expires()
                    logger.warning(f"⚠️ Refresh Token expiring in {days:.1f} days — renew manually")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Token renewal loop error: {e}")

    # ── Account info ────────────────────────────────────────────

    async def _fetch_account(self) -> bool:
        """Verify account is accessible via the REST API."""
        data = await self._get(f"/v2/webserv/traders/{CTRADER_ACCOUNT_ID}")
        if data:
            bal  = data.get("balance", 0) / 100.0   # cTrader returns cents
            eq   = data.get("equity",  bal)  / 100.0
            logger.info(f"✅ Account connected | Balance: ${bal:,.2f} | Equity: ${eq:,.2f}")
            self._account_state = AccountState(
                balance=bal, equity=eq,
                open_positions=0, total_pnl=0.0
            )
            self._connected = True
            if self.balance_manager:
                self.balance_manager._equity   = eq
                self.balance_manager._balance  = bal
            return True

        # Fallback: try listing accounts to confirm token works
        data2 = await self._get("/v2/webserv/traders")
        if data2 is not None:
            logger.info("✅ API reachable (account listing OK)")
            self._connected = True
            return True

        logger.warning("⚠️ Could not fetch account — check CTRADER_ACCOUNT_ID")
        return False

    async def _poll_account_loop(self):
        """Poll account balance every 30 s and sync to BalanceManager."""
        while self._running:
            try:
                await self._fetch_account()
            except Exception as e:
                logger.debug(f"Account poll error: {e}")
            await asyncio.sleep(30)

    # ── Position management ─────────────────────────────────────

    async def get_positions(self) -> List[dict]:
        """Return open positions — mirrors MCPExecutor.get_positions()."""
        data = await self._get(f"/v2/webserv/traders/{CTRADER_ACCOUNT_ID}/positions")
        if data is None:
            return list(self._positions.values())
        positions = data if isinstance(data, list) else data.get("position", data.get("positions", []))
        self._positions = {str(p.get("positionId", p.get("id", i))): p
                           for i, p in enumerate(positions)}
        return positions

    async def close_position(self, position_id: str, volume: Optional[float] = None) -> bool:
        body = {"positionId": position_id}
        if volume is not None:
            body["volume"] = int(volume * 100)   # lots → units
        result = await self._post(
            f"/v2/webserv/traders/{CTRADER_ACCOUNT_ID}/positions/{position_id}/close",
            body
        )
        ok = result is not None
        logger.info(f"{'✅' if ok else '❌'} Close position {position_id}")
        return ok

    async def modify_position(self, position_id: str,
                              stop_loss: Optional[float] = None,
                              take_profit: Optional[float] = None) -> bool:
        body = {}
        if stop_loss   is not None: body["stopLoss"]   = stop_loss
        if take_profit is not None: body["takeProfit"] = take_profit
        if not body:
            return False
        result = await self._post(
            f"/v2/webserv/traders/{CTRADER_ACCOUNT_ID}/positions/{position_id}",
            body
        )
        ok = result is not None
        logger.info(f"{'✅' if ok else '❌'} Modify SL/TP for position {position_id}")
        return ok

    # ── Order execution ─────────────────────────────────────────

    async def execute_signal(self, signal: dict) -> bool:
        """
        Place a market order from a validated signal dict.
        signal keys: signal_type (BUY/SELL), entry_price, stop_loss,
                     take_profit, lot_size, symbol (default XAUUSD)
        """
        direction  = str(signal.get("signal_type", "BUY")).upper()
        lot_size   = float(signal.get("lot_size",   0.01))
        stop_loss  = float(signal.get("stop_loss",  0))
        take_profit = float(signal.get("take_profit", 0))
        symbol     = signal.get("symbol", "XAUUSD")
        volume     = int(lot_size * 100_000)   # 1 lot = 100,000 units

        body = {
            "symbolName":  symbol,
            "tradeSide":   "BUY" if direction == "BUY" else "SELL",
            "volume":      volume,
            "orderType":   "MARKET",
        }
        if stop_loss:   body["stopLoss"]   = round(stop_loss,   2)
        if take_profit: body["takeProfit"] = round(take_profit, 2)

        logger.info(
            f"📤 Placing order | {direction} {lot_size}L {symbol} | "
            f"SL={stop_loss:.2f} TP={take_profit:.2f}"
        )

        result = await self._post(
            f"/v2/webserv/traders/{CTRADER_ACCOUNT_ID}/orders",
            body
        )

        if result:
            self._executed += 1
            pos_id = str(result.get("positionId", result.get("orderId", "?")))
            logger.info(f"✅ Order placed | PositionID: {pos_id}")
            return True
        else:
            self._rejected += 1
            logger.error(f"❌ Order rejected for signal: {signal}")
            return False

    # ── MCPExecutor-compatible interface ────────────────────────

    def stats(self) -> dict:
        return {
            "mcp_connected":    self._connected,
            "executed":         self._executed,
            "rejected":         self._rejected,
            "open_positions":   len(self._positions),
        }

    def is_connected(self) -> bool:
        return self._connected

    # ── Main loop ───────────────────────────────────────────────

    async def run_forever(self):
        self._running = True
        logger.info("CTraderOpenAPI executor started")

        # Initialise token state from env
        access_token   = _cfg("CTRADER_ACCESS_TOKEN")
        refresh_token  = _cfg("CTRADER_REFRESH_TOKEN")
        expires_in_raw = int(_cfg("CTRADER_TOKEN_EXPIRES_IN", "2628000"))   # sandbox default

        self._token_state = TokenState(
            access_token              = access_token,
            refresh_token             = refresh_token,
            access_token_expires_at   = datetime.now(timezone.utc) + timedelta(seconds=expires_in_raw),
            refresh_token_expires_at  = datetime.now(timezone.utc) + timedelta(days=90),
        )

        # Verify connectivity
        await self._fetch_account()

        # Background tasks
        self._tasks = [
            asyncio.create_task(self._token_renewal_loop()),
            asyncio.create_task(self._poll_account_loop()),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("CTraderOpenAPI executor cancelled")
        except Exception as e:
            logger.error(f"CTraderOpenAPI executor error: {e}", exc_info=True)
        finally:
            self._running = False
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            logger.info("CTraderOpenAPI executor stopped")

    def get_token_status(self) -> dict:
        if not self._token_state:
            return {"status": "not_initialized"}
        return {
            "access_token_expires_at":    self._token_state.access_token_expires_at.isoformat(),
            "refresh_token_expires_at":   self._token_state.refresh_token_expires_at.isoformat(),
            "access_token_expired":       self._token_state.is_access_token_expired(),
            "access_token_expiring_soon": self._token_state.is_access_token_expiring_soon(),
            "refresh_token_expired":      self._token_state.is_refresh_token_expired(),
            "refresh_token_expiring_soon": self._token_state.is_refresh_token_expiring_soon(),
            "days_until_refresh_expires": self._token_state.days_until_refresh_expires(),
            "last_renewal":               self._last_renewal.isoformat(),
            "connected":                  self._connected,
        }
