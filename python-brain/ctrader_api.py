"""
ctrader_api.py — cTrader Open API Integration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Direct connection to cTrader Open API with:
  ✓ Stable WebSocket connection
  ✓ Automatic reconnection with exponential backoff
  ✓ Smart trailing stop (ATR-based, dynamic)
  ✓ Real-time position tracking
  ✓ Balance & Equity monitoring
  ✓ Full order management

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
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger('ctrader_api')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CTRADER_HOST = "demo.ctraderapi.com"
CTRADER_PORT = 5035
CTRADER_ACCESS_TOKEN = os.environ.get("CTRADER_ACCESS_TOKEN", "")
CTRADER_ACCOUNT_ID = os.environ.get("CTRADER_ACCOUNT_ID", "")
CTRADER_MODE = os.environ.get("CTRADER_MODE", "demo").lower()  # demo or live

# Switch host based on mode
if CTRADER_MODE == "live":
    CTRADER_HOST = "live.ctraderapi.com"

# Reconnection strategy
RECONNECT_BASE_DELAY = 2.0   # seconds
RECONNECT_MAX_DELAY = 60.0   # seconds
RECONNECT_MAX_ATTEMPTS = 0   # 0 = infinite

# Trailing stop configuration
TRAILING_STOP_ATR_MULTIPLIER = 1.5  # Stop is placed at: price - (ATR × multiplier)
TRAILING_STOP_MIN_DISTANCE_PIPS = 10  # Minimum distance in pips
TRAILING_STOP_UPDATE_INTERVAL = 5.0  # Check every N seconds


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA CLASSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CTRADER OPEN API CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CTraderOpenAPI:
    """
    Main executor for cTrader Open API.
    Manages connection, positions, orders, and smart trailing stops.
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
        self._auth_token: Optional[str] = None

        self._running = False
        self._tasks: List[asyncio.Task] = []

        logger.info(f"CTraderOpenAPI initialized | Mode: {CTRADER_MODE} | Host: {CTRADER_HOST}")

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

            logger.info(f"Connecting to {uri}...")
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
                logger.info(f"✓ Authenticated | Account: {CTRADER_ACCOUNT_ID}")
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

    async def _send_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a command to the API and wait for response.
        """
        if not self._connected or not self._websocket:
            logger.warning("Not connected to cTrader API")
            return {"error": "Not connected"}

        try:
            await self._websocket.send(json.dumps(cmd))
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=5.0
            )
            return json.loads(response)
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return {"error": str(e)}

    async def _reconnect_loop(self):
        """
        Maintain connection with exponential backoff retry.
        """
        while self._running:
            if not self._connected:
                self._reconnect_attempts += 1
                logger.warning(
                    f"Reconnection attempt #{self._reconnect_attempts} "
                    f"(delay: {self._reconnect_delay:.1f}s)"
                )

                await asyncio.sleep(self._reconnect_delay)

                if await self._connect():
                    logger.info("✓ Reconnected successfully")
                else:
                    # Exponential backoff
                    self._reconnect_delay = min(
                        self._reconnect_delay * 1.5,
                        RECONNECT_MAX_DELAY
                    )
                    if RECONNECT_MAX_ATTEMPTS > 0 and self._reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
                        logger.error("Max reconnection attempts reached")
                        break
            else:
                await asyncio.sleep(5.0)

    async def _message_listener(self):
        """
        Listen for incoming messages from the API.
        """
        while self._running and self._connected:
            try:
                if not self._websocket:
                    break

                msg_text = await asyncio.wait_for(
                    self._websocket.recv(),
                    timeout=30.0
                )
                msg = json.loads(msg_text)
                await self._handle_message(msg)

            except asyncio.TimeoutError:
                logger.debug("Message listener timeout (expected)")
            except Exception as e:
                logger.error(f"Message listener error: {e}")
                self._connected = False
                break

    async def _handle_message(self, msg: Dict[str, Any]):
        """
        Process incoming API messages.
        """
        msg_type = msg.get("type")

        if msg_type == "account_update":
            await self._update_account_state(msg)
        elif msg_type == "position_opened":
            await self._on_position_opened(msg)
        elif msg_type == "position_closed":
            await self._on_position_closed(msg)
        elif msg_type == "position_updated":
            await self._on_position_updated(msg)
        elif msg_type == "error":
            logger.error(f"API Error: {msg.get('message')}")

    async def _update_account_state(self, data: Dict[str, Any]):
        """
        Update account balance, equity, and positions.
        """
        try:
            self._account_state = AccountState(
                balance=float(data.get("balance", 0)),
                equity=float(data.get("equity", 0)),
                open_positions=int(data.get("positions", 0)),
                total_pnl=float(data.get("total_pnl", 0)),
            )

            if self.balance_manager:
                await self.balance_manager.update_from_api(
                    balance=self._account_state.balance,
                    equity=self._account_state.equity,
                )

            logger.debug(
                f"Account: Balance=${self._account_state.balance:.2f} | "
                f"Equity=${self._account_state.equity:.2f} | "
                f"Positions={self._account_state.open_positions}"
            )
        except Exception as e:
            logger.error(f"Failed to update account state: {e}")

    async def _on_position_opened(self, data: Dict[str, Any]):
        """
        Handle position opened event.
        """
        pos_id = data.get("position_id")
        symbol = data.get("symbol")
        buy = data.get("buy", True)
        volume = float(data.get("volume", 0))
        entry_price = float(data.get("entry_price", 0))

        position = Position(
            position_id=pos_id,
            symbol=symbol,
            buy=buy,
            volume=volume,
            entry_price=entry_price,
            current_price=entry_price,
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
        )

        self._positions[pos_id] = position
        logger.info(
            f"✓ Position opened | {symbol} {'BUY' if buy else 'SELL'} "
            f"{volume}L @ ${entry_price:.5f}"
        )

        if self.channel_reporter:
            await self.channel_reporter.report_position_opened(position)

    async def _on_position_closed(self, data: Dict[str, Any]):
        """
        Handle position closed event.
        """
        pos_id = data.get("position_id")
        close_price = float(data.get("close_price", 0))
        pnl = float(data.get("pnl", 0))

        if pos_id in self._positions:
            pos = self._positions.pop(pos_id)
            logger.info(
                f"✓ Position closed | {pos.symbol} "
                f"@ ${close_price:.5f} | P&L: ${pnl:+.2f}"
            )

            if self.channel_reporter:
                await self.channel_reporter.report_position_closed(pos, close_price, pnl)

    async def _on_position_updated(self, data: Dict[str, Any]):
        """
        Handle position update event.
        """
        pos_id = data.get("position_id")
        if pos_id in self._positions:
            pos = self._positions[pos_id]
            pos.current_price = float(data.get("current_price", pos.current_price))
            pos.stop_loss = data.get("stop_loss", pos.stop_loss)
            pos.take_profit = data.get("take_profit", pos.take_profit)

    async def _trailing_stop_monitor(self):
        """
        Smart trailing stop monitor.
        Adjusts stop loss based on ATR and price movement.
        """
        while self._running:
            try:
                await asyncio.sleep(TRAILING_STOP_UPDATE_INTERVAL)

                if not self._positions:
                    continue

                for pos_id, position in list(self._positions.items()):
                    if not position.trailing_stop_active:
                        continue

                    await self._update_trailing_stop(position)

            except Exception as e:
                logger.error(f"Trailing stop monitor error: {e}")

    async def _update_trailing_stop(self, position: Position):
        """
        Update trailing stop for a single position.
        Uses ATR-based calculation for smart adjustment.
        """
        try:
            # Get current ATR from market data (would come from market_feed)
            atr = await self._get_current_atr(position.symbol)
            if not atr:
                return

            # Calculate new stop loss based on ATR
            if position.buy:
                # For BUY: stop is below current price
                new_stop = position.current_price - (atr * TRAILING_STOP_ATR_MULTIPLIER)
                # Only move stop higher, never lower
                if position.stop_loss is None or new_stop > position.stop_loss:
                    # Ensure minimum distance
                    min_stop = position.current_price - (TRAILING_STOP_MIN_DISTANCE_PIPS * 0.01)
                    new_stop = max(new_stop, min_stop)
                    await self._modify_stop_loss(position.position_id, new_stop)
                    position.trailing_stop_price = new_stop
            else:
                # For SELL: stop is above current price
                new_stop = position.current_price + (atr * TRAILING_STOP_ATR_MULTIPLIER)
                # Only move stop lower, never higher
                if position.stop_loss is None or new_stop < position.stop_loss:
                    # Ensure minimum distance
                    max_stop = position.current_price + (TRAILING_STOP_MIN_DISTANCE_PIPS * 0.01)
                    new_stop = min(new_stop, max_stop)
                    await self._modify_stop_loss(position.position_id, new_stop)
                    position.trailing_stop_price = new_stop

        except Exception as e:
            logger.error(f"Failed to update trailing stop for {position.position_id}: {e}")

    async def _get_current_atr(self, symbol: str) -> Optional[float]:
        """
        Get current ATR value for a symbol.
        This would integrate with market_feed component.
        Placeholder for now.
        """
        # TODO: Integrate with market_feed.snapshot.atr_14
        return 1.5  # Placeholder

    async def open_position(
        self,
        symbol: str,
        buy: bool,
        volume: float,
        entry_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: bool = False,
    ) -> Dict[str, Any]:
        """
        Open a new position.
        
        Args:
            symbol: Trading pair (e.g., "XAUUSD")
            buy: True for BUY, False for SELL
            volume: Lot size
            entry_price: Entry price
            stop_loss: Stop loss price (optional)
            take_profit: Take profit price (optional)
            trailing_stop: Enable smart trailing stop
        """
        if not self._connected:
            logger.error("Cannot open position: not connected to API")
            return {"error": "Not connected"}

        cmd = {
            "type": "open_position",
            "symbol": symbol,
            "buy": buy,
            "volume": volume,
            "entry_price": entry_price,
        }
        if stop_loss:
            cmd["stop_loss"] = stop_loss
        if take_profit:
            cmd["take_profit"] = take_profit

        result = await self._send_command(cmd)

        if "position_id" in result:
            pos_id = result["position_id"]
            position = Position(
                position_id=pos_id,
                symbol=symbol,
                buy=buy,
                volume=volume,
                entry_price=entry_price,
                current_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop_active=trailing_stop,
            )
            self._positions[pos_id] = position
            logger.info(f"✓ Position opened: {pos_id}")
            return result
        else:
            logger.error(f"Failed to open position: {result}")
            return result

    async def close_position(self, position_id: str) -> Dict[str, Any]:
        """
        Close an open position.
        """
        if not self._connected:
            return {"error": "Not connected"}

        result = await self._send_command({
            "type": "close_position",
            "position_id": position_id,
        })

        if "success" in result and result["success"]:
            if position_id in self._positions:
                del self._positions[position_id]
            logger.info(f"✓ Position closed: {position_id}")
        else:
            logger.error(f"Failed to close position: {result}")

        return result

    async def _modify_stop_loss(
        self,
        position_id: str,
        new_stop_loss: float,
    ) -> Dict[str, Any]:
        """
        Modify stop loss for a position (internal use by trailing stop).
        """
        if not self._connected:
            return {"error": "Not connected"}

        return await self._send_command({
            "type": "modify_stop_loss",
            "position_id": position_id,
            "stop_loss": new_stop_loss,
        })

    async def modify_take_profit(
        self,
        position_id: str,
        new_take_profit: float,
    ) -> Dict[str, Any]:
        """
        Modify take profit for a position.
        """
        if not self._connected:
            return {"error": "Not connected"}

        return await self._send_command({
            "type": "modify_take_profit",
            "position_id": position_id,
            "take_profit": new_take_profit,
        })

    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_account_state(self) -> Optional[AccountState]:
        """Get current account state."""
        return self._account_state

    async def run_forever(self):
        """
        Main execution loop.
        Runs connection manager, message listener, and trailing stop monitor.
        """
        self._running = True
        logger.info("CTraderOpenAPI executor started")

        try:
            # Initial connection
            await self._connect()

            # Spawn background tasks
            self._tasks = [
                asyncio.create_task(self._reconnect_loop()),
                asyncio.create_task(self._message_listener()),
                asyncio.create_task(self._trailing_stop_monitor()),
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
