# 🧠 Python Brain — Core Trading Logic

## Overview

The Python Brain is the heart of FlatWavyAccounting, orchestrating real-time market analysis, signal validation, and trade execution.

## Components

### 1. Main Entry Point (`main.py`)

Coordinates 11 concurrent coroutines:

```python
await asyncio.gather(
    market_feed.run_forever(),              # 1. Live market data
    news_feed.run_forever(),                # 2. Economic calendar
    listener.run_with_reconnect(),          # 3. Telegram signals
    analyzer.run(),                         # 4. 5-filter pipeline
    mcp_executor.run_forever(),             # 5. Order execution
    bridge.relay_loop(),                    # 6. Execution bridge
    be_monitor.run_forever(),               # 7. Break-even monitor
    balance_manager.run_forever(),          # 8. Balance monitoring
    control_bot.run_forever(),              # 9. Command handler
    _heartbeat_loop(),                      # 10. Liveness signal
    _market_data_sync_loop(market_feed, analyzer),  # 11. Data sync
)
```

### 2. cTrader API (`ctrader_api.py`)

**WebSocket connection to cTrader Open API**

```python
from python_brain.ctrader_api import CTraderOpenAPI

executor = CTraderOpenAPI(balance_manager=bm)
await executor.open_position(
    symbol="XAUUSD",
    buy=True,
    volume=0.5,
    entry_price=2645.50,
    stop_loss=2620.00,
    take_profit=2670.00,
    trailing_stop=True  # Enable smart trailing
)
```

**Features:**
- ✅ Stable WebSocket connection
- ✅ Automatic reconnection (exponential backoff)
- ✅ Position tracking
- ✅ Smart trailing stop (ATR-based)
- ✅ Real-time account state
- ✅ Order management

### 3. Gemini AI Analyzer (`gemini_analyzer.py`)

**Google Gemini for signal validation**

```python
from python_brain.gemini_analyzer import GeminiAnalyzer

gemini = GeminiAnalyzer(api_key=GEMINI_API_KEY)
result = await gemini.analyze_signal(
    symbol="XAUUSD",
    signal_type="BUY",
    entry_price=2645.50,
    current_price=2645.30,
    market_data={
        'rsi': 62,
        'atr': 18.5,
        'ema50': 2630,
        'trend': 'UPTREND'
    },
    confluence_indicators={
        'rsi_neutral': True,
        'price_above_ema50': True,
        'atr_healthy': True,
        'volume_high': True,
        'macd_bullish': True
    }
)

# result = AnalysisResult(
#     confidence=85,
#     recommendation="BUY",
#     reasoning="Strong confluence with all indicators...",
#     risk_level="MEDIUM",
#     sentiment="BULLISH",
#     confluence_score=4.5
# )
```

### 4. Market Data Feed (`market_data_feed.py`)

**Real-time XAUUSD & BTCUSD data**

```python
from python_brain.market_data_feed import MarketDataFeed

feed = MarketDataFeed()
await feed.run_forever()  # Updates every 60s

# Access latest data
snapshot = feed.snapshot
print(f"Price: ${snapshot.current_price}")
print(f"RSI: {snapshot.rsi_14}")
print(f"ATR: {snapshot.atr_14}")
print(f"Trend: {snapshot.trend}")
```

**Indicators calculated:**
- RSI(14), ATR(14)
- EMA(50), EMA(200)
- MACD
- Bollinger Bands
- Volume analysis
- Trend detection
- Volatility assessment

### 5. Market Analyzer (`market_analyzer.py`)

**5-filter signal validation pipeline**

```python
from python_brain.market_analyzer import MarketAnalyzer

analyzer = MarketAnalyzer(
    signal_queue=signal_queue,
    news_queue=news_queue,
    validated_queue=validated_queue,
    balance_manager=balance_manager
)

# Signals flow through 5 filters:
# 1. Basic validation (required fields)
# 2. Technical confluence (RSI, EMA, price)
# 3. Risk/Reward (min 1:1 ratio)
# 4. Market conditions (ATR, volatility)
# 5. Account balance (sufficient funds)

await analyzer.run()
```

### 6. Balance Manager (`balance_manager.py`)

**Account safety & position sizing**

```python
from python_brain.balance_manager import BalanceManager

bm = BalanceManager()

# Update from API
await bm.update_from_api(balance=5000, equity=5150)

# Check if trading allowed
if bm.can_trade():
    # Safe to open positions
    max_lot = bm.get_max_lot_size(risk_percent=1.0)
    print(f"Max lot size: {max_lot}L")
```

**Safety checks:**
- ✅ Min balance: $100
- ✅ Max used margin: 80%
- ✅ Dynamic lot sizing (1% risk rule)

### 7. Telegram Listener (`telegram_listener.py`)

**Signal reception & break-even monitoring**

```python
from python_brain.telegram_listener import TelegramListener, BreakEvenMonitor

listener = TelegramListener(
    signal_queue=signal_queue,
    news_queue=news_queue,
    market_feed=market_feed,
    be_monitor=be_monitor
)

await listener.run_with_reconnect()

# Break-even monitor
be_monitor = BreakEvenMonitor(
    market_feed=market_feed,
    fix_executor=executor,
    channel_reporter=reporter
)

await be_monitor.run_forever()
```

### 8. Execution Bridge (`signal_queue.py`)

**Signal relay to broker**

```python
from python_brain.signal_queue import ExecutionBridge

bridge = ExecutionBridge(
    validated_queue=validated_queue,
    fix_executor=executor,
    channel_reporter=reporter
)

await bridge.relay_loop()
```

**Features:**
- ✅ Queue management
- ✅ Execution relay
- ✅ Audit logging (signal_audit.jsonl)
- ✅ Execution statistics

### 9. Channel Reporter (`channel_reporter.py`)

**Telegram notifications**

```python
from python_brain.channel_reporter import ChannelReporter

reporter = ChannelReporter()
reporter.set_client(telegram_client)

await reporter.report_position_opened(position)
await reporter.report_position_closed(position, close_price, pnl)
```

### 10. Control Bot (`control_bot.py`)

**Telegram command handler**

```
/status     → System status
/balance    → Account balance
/positions  → Open positions
/close_all  → Close all trades
/news       → Upcoming events
```

### 11. News Feed (`news_feed.py`)

**ForexFactory economic calendar**

```python
from python_brain.news_feed import ForexNewsFeed

feed = ForexNewsFeed()
await feed.run_forever()  # Updates every 5 min

if feed.news_mode_active:
    # Disable trading or reduce lot size
    lot_size = 0.01  # Cap lot
```

---

## Smart Trailing Stop System

### Algorithm

```python
class TrailingStopMonitor:
    async def _update_trailing_stop(self, position):
        """Update trailing stop for a position."""
        atr = await self._get_current_atr(position.symbol)
        
        if position.buy:
            # For BUY: stop below price
            new_stop = position.current_price - (atr * MULTIPLIER)
            
            if new_stop > position.stop_loss:
                await self._modify_stop_loss(position.id, new_stop)
        else:
            # For SELL: stop above price
            new_stop = position.current_price + (atr * MULTIPLIER)
            
            if new_stop < position.stop_loss:
                await self._modify_stop_loss(position.id, new_stop)
```

### Configuration

```python
# In ctrader_api.py
TRAILING_STOP_ATR_MULTIPLIER = 1.5      # Stop = ATR × 1.5
TRAILING_STOP_MIN_DISTANCE_PIPS = 10    # Min 10 pips
TRAILING_STOP_UPDATE_INTERVAL = 5.0     # Check every 5s
```

### Behavior

1. **Position Opens**: SL set at entry ± (ATR × 1.5)
2. **After +15 pips**: Move SL to break-even (entry price)
3. **Continuous**: Trail SL following price with ATR spacing
4. **Exit**: When TP hit or SL triggered

---

## Async Architecture

### Queue Flow

```
Telegram Signal
    ↓
[signal_queue] (maxsize=100)
    ↓
MarketAnalyzer (5 filters)
    ↓ (if passed all filters)
[validated_queue] (maxsize=50)
    ↓
ExecutionBridge (relay)
    ↓
cTrader Open API (execute)
    ↓
[open positions]
    ↓
BreakEvenMonitor (every 5s)
    ↓
Telegram Notifications
```

### Coroutine Coordination

All 11 coroutines run in parallel:

```python
# They communicate via:
# 1. Queues (signal_queue, validated_queue, news_queue)
# 2. Shared state (market_feed.snapshot, balance_manager._state)
# 3. Event callbacks (position_opened, position_closed)
# 4. Logs (gold_blueprint.log)
```

---

## Configuration Files

### Environment Variables

```bash
# cTrader
CTRADER_MODE=demo
CTRADER_ACCESS_TOKEN=xxx
CTRADER_ACCOUNT_ID=yyy

# Telegram
TELEGRAM_API_ID=123
TELEGRAM_API_HASH=xxx
TELEGRAM_STRING_SESSION=xxx

# Gemini
GEMINI_API_KEY=xxx

# System
LOG_LEVEL=INFO
```

---

## Error Handling

### Reconnection Strategy

```python
# Base delay: 2s
# Exponential backoff: ×1.5 each retry
# Max delay: 60s
# Result: 2, 3, 4.5, 6.75, 10.1, 15.2, 22.8, 34.2, 51.3, 60, 60...
```

### Crash Recovery

```
Process crashes
    ↓
Watchdog detects (every 30s)
    ↓
Waits backoff delay
    ↓
Restart process
    ↓
Send Telegram alert
    ↓
If >5 crashes in 10 min:
    → Pause 10 minutes
    → Send critical alert
    → Resume
```

---

## Performance Tips

1. **Use Demo Account First**: Test thoroughly before live
2. **Start Small**: Begin with 0.01 lot size
3. **Monitor Logs**: Check `gold_blueprint.log` regularly
4. **Adjust Risk**: Modify `MAX_USED_MARGIN_PERCENT` as needed
5. **Set Alarms**: Use Telegram alerts for critical events

---

## Debugging

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
python watchdog.py
```

### Check Connection

```bash
python -c "
import asyncio
from python_brain.ctrader_api import CTraderOpenAPI

async def test():
    api = CTraderOpenAPI()
    connected = await api._connect()
    print(f'Connected: {connected}')

asyncio.run(test())
"
```

### Verify All Imports

```bash
python -c "
from python_brain.ctrader_api import CTraderOpenAPI
from python_brain.gemini_analyzer import GeminiAnalyzer
from python_brain.market_data_feed import MarketDataFeed
from python_brain.balance_manager import BalanceManager
from python_brain.telegram_listener import TelegramListener
from python_brain.market_analyzer import MarketAnalyzer
from python_brain.signal_queue import ExecutionBridge
from python_brain.channel_reporter import ChannelReporter
from python_brain.control_bot import ControlBot
from python_brain.news_feed import ForexNewsFeed
print('✓ All imports OK')
"
```

---

## Future Enhancements

- [ ] Machine learning signal validation
- [ ] Advanced portfolio optimization
- [ ] Multi-symbol correlation analysis
- [ ] Real-time risk dashboard
- [ ] Cloud storage for backups
- [ ] Email alerts
- [ ] SMS notifications

---

**Last Updated**: June 2026
