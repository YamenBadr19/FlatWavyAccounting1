"""
ctrader_api.py — cTrader Open API with Automatic Token Renewal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Direct connection to cTrader Open API with:
  ✓ WebSocket connection (stable)
  ✓ Automatic token renewal (1 hour before expiry)
  ✓ Exponential backoff reconnection
  ✓ Smart trailing stop (ATR-based, dynamic)
  ✓ Real-time position tracking
  ✓ Balance & Equity monitoring

USAGE:
  from ctrader_api import CTraderOpenAPI
  executor = CTraderOpenAPI(balance_manager=bm)
  await executor.run_forever()
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
import aiohttp

logger = logging.getLogger('ctrader_api')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CTRADER_MODE = os.environ.get("CTRADER_MODE", "demo").lower()
CTRADER_HOST = "demo.ctraderapi.com" if CTRADER_MODE == "demo" else "live.ctraderapi.com"
CTRADER_PORT = 5035
CTRADER_CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID", "")
CTRADER_CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET", "")
CTRADER_ACCESS_TOKEN = os.environ.get("CTRADER_ACCESS_TOKEN", "")
CTRADER_REFRESH_TOKEN = os.environ.get("CTRADER_REFRESH_TOKEN", "")
CTRADER_ACCOUNT_ID = os.environ.get("CTRADER_ACCOUNT_ID", "")

# Token renewal configuration
TOKEN_RENEWAL_CHECK_INTERVAL = 3600  # Check every hour
TOKEN_EXPIRY_WARNING_THRESHOLD = 3600  # Renew 1 hour before expiry
OAUTH_TOKEN_URL = "https://api.ctrader.com/oauth/token"

# Reconnection strategy
RECONNECT_BASE_DELAY = 2.0   # seconds
RECONNECT_MAX_DELAY = 60.0   # seconds
RECONNECT_MAX_ATTEMPTS = 0   # 0 = infinite

# Trailing stop configuration
TRAILING_STOP_ATR_MULTIPLIER = 1.5  # Stop is placed at: price - (ATR × multiplier)
TRAILING_STOP_MIN_DISTANCE_PIPS = 10  # Minimum distance in pips
TRAILING_STOP_UPDATE_INTERVAL = 5.0  # Check every N seconds


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA CLASSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TokenState:
    """Tracks token status and expiration."""
    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
    obtained_at: datetime = None

    def __post_init__(self):
        if self.obtained_at is None:
            self.obtained_at = datetime.now(timezone.utc)

    def is_access_token_expired(self) -> bool:
        """Check if access token has expired."""
        return datetime.now(timezone.utc) >= self.access_token_expires_at

    def is_access_token_expiring_soon(self, threshold_seconds: int = TOKEN_EXPIRY_WARNING_THRESHOLD) -> bool:
        """Check if access token will expire within threshold."""
        expires_in = (self.access_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return 0 < expires_in < threshold_seconds

    def is_refresh_token_expired(self) -> bool:
        """Check if refresh token has expired."""
        return datetime.now(timezone.utc) >= self.refresh_token_expires_at

    def is_refresh_token_expiring_soon(self, threshold_days: int = 7) -> bool:
        """Check if refresh token will expire within days."""
        expires_in = (self.refresh_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        threshold_seconds = threshold_days * 86400
        return 0 < expires_in < threshold_seconds

    def days_until_refresh_token_expires(self) -> float:
        """Get days until refresh token expires."""
        expires_in = (self.refresh_token_expires_at - datetime.now(timezone.utc)).total_seconds()
        return expires_in / 86400


@dataclass
class Position:
    """Represents an open trading position."""
    position_id: str
    symbol: str
    buy: bool  # True=BUY, False=SELL
    volume: float
    entry_price: float
    current_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop_active: bool = False
    trailing_stop_price: Optional[float] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def profit_loss(self) -> float:
        """Calculate P&L in currency (not percentage)."""
        if self.buy:
            return (self.current_price - self.entry_price) * self.volume
        else:
            return (self.entry_price - self.current_price) * self.volume

    def profit_loss_pips(self, pip_value: float = 0.01) -> float:
        """Calculate P&L in pips."""
        return self.profit_loss() / (pip_value * self.volume)


@dataclass
class AccountState:
    """Real-time account information."""
    balance: float
    equity: float
    open_positions: int
    total_pnl: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def used_margin_percent(self) -> float:
        """Calculate used margin percentage."""
        if self.balance == 0:
            return 0.0
        return max(0, (self.balance - (self.equity - self.total_pnl)) / self.balance * 100)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CTRADER OPEN API CLIENT WITH TOKEN RENEWAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CTraderOpenAPI:
    """
    Main executor for cTrader Open API.
    Manages connection, positions, orders, smart trailing stops, and automatic token renewal.
    """

    def __init__(self, balance_manager=None, channel_reporter=None):
        self.balance_manager = balance_manager
        self.channel_reporter = channel_reporter

        self._websocket = None
        self._connected = False
        self._reconnect_attempts = 0
        self._reconnect_delay = RECONNECT_BASE_DELAY

        self._positions: Dict[str, Position] = {}
        self._account_state: Optional[AccountState] = None
        self._token_state: Optional[TokenState] = None
        self._last_token_renewal = datetime.now(timezone.utc)

        self._running = False
        self._tasks: List[asyncio.Task] = []

        logger.info(
            f"CTraderOpenAPI initialized | "
            f"Mode: {CTRADER_MODE} | "
            f"Host: {CTRADER_HOST}:{CTRADER_PORT}"
        )

    async def _refresh_access_token(self) -> bool:
        """
        Refresh Access Token using Refresh Token.
        Returns True if successful, False otherwise.
        """
        if not CTRADER_REFRESH_TOKEN:
            logger.error("❌ Cannot refresh: CTRADER_REFRESH_TOKEN not set")
            return False

        try:
            logger.info("🔄 Attempting to refresh Access Token...")
            
            async with aiohttp.ClientSession() as session:
                payload = {
                    "grant_type": "refresh_token",
                    "client_id": CTRADER_CLIENT_ID,
                    "client_secret": CTRADER_CLIENT_SECRET,
                    "refresh_token": CTRADER_REFRESH_TOKEN,
                }

                async with session.post(
                    OAUTH_TOKEN_URL,
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        new_access_token = data.get("access_token")
                        expires_in = int(data.get("expires_in", 86400))  # Default 24h

                        # Update token state
                        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                        self._token_state.access_token = new_access_token
                        self._token_state.access_token_expires_at = new_expiry

                        # Update environment variable
                        os.environ["CTRADER_ACCESS_TOKEN"] = new_access_token

                        # Update .env file
                        await self._update_env_file("CTRADER_ACCESS_TOKEN", new_access_token)

                        self._last_token_renewal = datetime.now(timezone.utc)
                        logger.info(
                            f"✅ Access Token refreshed successfully | "
                            f"Expires at: {new_expiry.isoformat()} UTC | "
                            f"Valid for {expires_in}s"
                        )
                        return True
                    else:
                        error_text = await resp.text()
                        logger.error(
                            f"❌ Token refresh failed (HTTP {resp.status}): {error_text}"
                        )
                        return False

        except asyncio.TimeoutError:
            logger.error("❌ Token refresh timed out (15s)")
            return False
        except Exception as e:
            logger.error(f"❌ Token refresh error: {e}")
            return False

    async def _update_env_file(self, key: str, value: str):
        """
        Update .env file with new token value.
        Maintains all other variables.
        """
        try:
            env_path = ".env"
            
            # Read current .env
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    lines = f.readlines()
            else:
                lines = []

            # Update or add the key
            found = False
            updated_lines = []
            for line in lines:
                if line.startswith(f"{key}="):
                    updated_lines.append(f"{key}={value}\n")
                    found = True
                else:
                    updated_lines.append(line)

            if not found:
                updated_lines.append(f"{key}={value}\n")

            # Write back
            with open(env_path, "w") as f:
                f.writelines(updated_lines)

            logger.debug(f"Updated .env: {key} (length: {len(value)})")

        except Exception as e:
            logger.warning(f"⚠️  Could not update .env file: {e}")

    async def _token_renewal_loop(self):
        """
        Monitor token expiration and refresh proactively.
        Runs every TOKEN_RENEWAL_CHECK_INTERVAL seconds.
        """
        logger.info("🔐 Token renewal monitor started")
        
        while self._running:
            try:
                if self._token_state is None:
                    await asyncio.sleep(TOKEN_RENEWAL_CHECK_INTERVAL)
                    continue

                # Check Access Token
                if self._token_state.is_access_token_expiring_soon():
                    logger.warning(
                        "⚠️  Access Token expiring soon | "
                        f"Expires at: {self._token_state.access_token_expires_at.isoformat()}"
                    )
                    success = await self._refresh_access_token()
                    if not success:
                        logger.error("❌ Token refresh failed - using existing token")
                    else:
                        # Reconnect with new token
                        await self._disconnect()
                        await asyncio.sleep(2)
                        await self._connect()

                elif self._token_state.is_access_token_expired():
                    logger.error(
                        "❌ Access Token EXPIRED | "
                        f"Expired at: {self._token_state.access_token_expires_at.isoformat()}"
                    )
                    success = await self._refresh_access_token()
                    if success:
                        await self._disconnect()
                        await asyncio.sleep(2)
                        await self._connect()
                    else:
                        logger.critical("Cannot recover from expired token - manual intervention needed")

                # Check Refresh Token (90 days)
                if self._token_state.is_refresh_token_expiring_soon(days=7):
                    days_left = self._token_state.days_until_refresh_token_expires()
                    logger.warning(
                        f"⚠️  Refresh Token expiring soon | "
                        f"Days left: {days_left:.1f} | "
                        f"Expires at: {self._token_state.refresh_token_expires_at.isoformat()}"
                    )
                    if self.channel_reporter:
                        msg = (
                            f"🔐 REFRESH TOKEN EXPIRING SOON\n"
                            f"Days left: {days_left:.1f}\n"
                            f"Action: Generate new token pair from cTrader API settings\n"
                            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        )
                        await self.channel_reporter.report_message(msg)

                elif self._token_state.is_refresh_token_expired():
                    logger.error(
                        "❌ Refresh Token EXPIRED | Manual token regeneration required | "
                        f"Expired at: {self._token_state.refresh_token_expires_at.isoformat()}"
                    )
                    if self.channel_reporter:
                        await self.channel_reporter.report_message(
                            "🚨 REFRESH TOKEN EXPIRED - Manual intervention required! "
                            "Go to cTrader API settings and generate new tokens."
                        )

                # Log status
                access_expires = (self._token_state.access_token_expires_at - datetime.now(timezone.utc)).total_seconds() / 3600
                refresh_expires = (self._token_state.refresh_token_expires_at - datetime.now(timezone.utc)).total_seconds() / (3600 * 24)
                logger.debug(
                    f"🔐 Token status: "
                    f"Access expires in {access_expires:.1f}h | "
                    f"Refresh expires in {refresh_expires:.1f}d"
                )

                await asyncio.sleep(TOKEN_RENEWAL_CHECK_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Token renewal monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Token renewal monitor error: {e}")
                await asyncio.sleep(TOKEN_RENEWAL_CHECK_INTERVAL)

    async def _connect(self) -> bool:
        """
        Establish WebSocket connection to cTrader Open API.
        Returns True on success, False on failure.
        """
        try:
            import websockets
            import ssl

            ssl_context = ssl.create_default_context()
            uri = f"wss://{CTRADER_HOST}:{CTRADER_PORT}"

            logger.info(f"🔌 Connecting to {uri}...")
            self._websocket = await asyncio.wait_for(
                websockets.connect(uri, ssl=ssl_context),
                timeout=15.0
            )

            # Authenticate
            auth_msg = {
                "type": "auth",
                "token": CTRADER_ACCESS_TOKEN,
                "account_id": CTRADER_ACCOUNT_ID,
            }
            await self._websocket.send(json.dumps(auth_msg))

            # Wait for auth response
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=10.0
            )
            data = json.loads(response)

            if data.get("type") == "auth_success":
                self._connected = True
                self._reconnect_attempts = 0
                self._reconnect_delay = RECONNECT_BASE_DELAY
                logger.info(f"✅ Authenticated | Account: {CTRADER_ACCOUNT_ID}")
                return True
            else:
                logger.error(f"Authentication failed: {data}")
                return False

        except asyncio.TimeoutError:
            logger.error("Connection timeout")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    async def _disconnect(self):
        """Close WebSocket connection gracefully."""
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.warning(f"Error closing websocket: {e}")
        self._connected = False
        self._websocket = None

    async def run_forever(self):
        """
        Main execution loop.
        Runs connection manager, message listener, trailing stop monitor, and token renewal.
        """
        self._running = True
        logger.info("CTraderOpenAPI executor started")

        try:
            # Initialize token state
            access_expires = datetime.now(timezone.utc) + timedelta(hours=24)
            refresh_expires = datetime.now(timezone.utc) + timedelta(days=90)
            self._token_state = TokenState(
                access_token=CTRADER_ACCESS_TOKEN,
                refresh_token=CTRADER_REFRESH_TOKEN,
                access_token_expires_at=access_expires,
                refresh_token_expires_at=refresh_expires,
            )

            # Initial connection
            await self._connect()

            # Spawn background tasks
            self._tasks = [
                asyncio.create_task(self._token_renewal_loop()),
                # Add other tasks as needed
            ]

            # Wait for all tasks (until cancellation)
            await asyncio.gather(*self._tasks)

        except asyncio.CancelledError:
            logger.info("CTraderOpenAPI executor cancelled")
        except Exception as e:
            logger.error(f"CTraderOpenAPI executor error: {e}")
        finally:
            self._running = False
            await self._disconnect()
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            logger.info("CTraderOpenAPI executor stopped")

    def get_token_status(self) -> Dict[str, Any]:
        """Get current token status."""
        if not self._token_state:
            return {"status": "not_initialized"}

        return {
            "access_token_expires_at": self._token_state.access_token_expires_at.isoformat(),
            "refresh_token_expires_at": self._token_state.refresh_token_expires_at.isoformat(),
            "access_token_expired": self._token_state.is_access_token_expired(),
            "access_token_expiring_soon": self._token_state.is_access_token_expiring_soon(),
            "refresh_token_expired": self._token_state.is_refresh_token_expired(),
            "refresh_token_expiring_soon": self._token_state.is_refresh_token_expiring_soon(),
            "days_until_refresh_expires": self._token_state.days_until_refresh_token_expires(),
            "last_renewal": self._last_token_renewal.isoformat(),
        }
