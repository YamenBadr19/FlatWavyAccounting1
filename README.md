# 🏆 FlatWavyAccounting — Autonomous Trading System

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production%20Ready-brightgreen.svg)](#)

**Advanced autonomous trading system for Gold (XAUUSD) & Bitcoin (BTCUSD) with:**
- 🤖 **cTrader Open API** direct integration (no MCP needed)
- 🧠 **Google Gemini AI** for signal validation & trade analysis
- 📊 **5-filter technical confluence** pipeline
- 🛑 **Smart ATR-based trailing stops** (dynamic, intelligent)
- 💬 **Real-time Telegram** notifications
- ☘️ **Automatic break-even** & profit trailing
- 🔄 **Auto-reconnection** with exponential backoff
- ⚡ **Production-ready** with watchdog monitoring

---

## 🚀 Quick Start

### Local Machine (5 minutes)

```bash
# Clone
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1

# Setup
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r python-brain/requirements.txt

# Configure
echo "CTRADER_ACCESS_TOKEN=your-token" > .env
echo "CTRADER_ACCOUNT_ID=your-account-id" >> .env
echo "TELEGRAM_API_ID=your-api-id" >> .env
echo "TELEGRAM_API_HASH=your-api-hash" >> .env
echo "TELEGRAM_STRING_SESSION=your-session" >> .env
echo "GEMINI_API_KEY=your-gemini-key" >> .env

# Run
python watchdog.py

# Monitor (in another terminal)
python status.py --watch 5
```

### Cloud Deployment (Choose one)

- **AWS EC2** (production): See [DEPLOYMENT.md](DEPLOYMENT.md#option-2-aws-ec2-production-recommended)
- **DigitalOcean** ($4/mo): See [DEPLOYMENT.md](DEPLOYMENT.md#option-3-digitalocean-droplet-recommended---4month)
- **Heroku** (free): See [DEPLOYMENT.md](DEPLOYMENT.md#option-1-heroku-free-tier-available-limited)
- **Google Cloud Run** (serverless): See [DEPLOYMENT.md](DEPLOYMENT.md#option-4-google-cloud-run-serverless)
- **PythonAnywhere** (easiest): See [DEPLOYMENT.md](DEPLOYMENT.md#option-5-pythonanywhere-easiest)

---

## 📚 Documentation

| Document | Purpose |
|----------|----------|
| [SETUP_GUIDE.md](SETUP_GUIDE.md) | Complete installation & configuration guide |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Cloud platform comparison & quick deploy commands |
| [python-brain/README.md](python-brain/README.md) | Core trading logic architecture |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│         Gold Blueprint Trading System v3.0           │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ╔════════════════════════════════════════════╗    │
│  ║  📊 Market Data Feed (XAUUSD, BTCUSD)      ║    │
│  ║  Updates: Every 60s                        ║    │
│  ║  Indicators: RSI, ATR, EMA, MACD, BB       ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  📰 Economic Calendar (ForexFactory)       ║    │
│  ║  Updates: Every 5 min                      ║    │
│  ║  Alerts: High-impact USD events ±30 min   ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  📡 Telegram Signal Listener               ║    │
│  ║  Source: Custom trading signal channels    ║    │
│  ║  Filters: Basic validation                 ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  🔍 Market Analyzer (5-Filter Pipeline)   ║    │
│  ║  Filter 1: Basic validation                ║    │
│  ║  Filter 2: Technical confluence            ║    │
│  ║  Filter 3: Risk/Reward ratio               ║    │
│  ║  Filter 4: Market conditions               ║    │
│  ║  Filter 5: Account balance check           ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  🧠 Gemini AI Signal Validator             ║    │
│  ║  • Confidence scoring (0-100)              ║    │
│  ║  • Risk assessment                         ║    │
│  ║  • Confluence evaluation                   ║    │
│  ║  • Recommended TP/SL levels                ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  💰 cTrader Open API Executor              ║    │
│  ║  • WebSocket connection (stable)           ║    │
│  ║  • Position management                     ║    │
│  ║  • Order execution                         ║    │
│  ║  • Real-time account state                 ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  🛑 Smart Trailing Stop Monitor            ║    │
│  ║  • Break-even after +15 pips               ║    │
│  ║  • Trail with ATR × 1.5x multiplier        ║    │
│  ║  • Updates every 5 seconds                 ║    │
│  ║  • Never locks in losses                   ║    │
│  ╚════════════════════════════════════════════╝    │
│            ↓                                        │
│  ╔════════════════════════════════════════════╗    │
│  ║  📢 Telegram Notifications                 ║    │
│  ║  • Position opened/closed                  ║    │
│  ║  • Break-even reached                      ║    │
│  ║  • P&L updates                             ║    │
│  ║  • Connection alerts                       ║    │
│  ╚════════════════════════════════════════════╝    │
│                                                      │
├─────────────────────────────────────────────────────┤
│  🔄 Background Services:                            │
│  • Watchdog (auto-restart on crash)                │
│  • Heartbeat monitor (liveness detection)          │
│  • Status dashboard (real-time metrics)            │
│  • Signal audit log (all executions)               │
└─────────────────────────────────────────────────────┘
```

---

## 🔧 Core Components

### 1. **ctrader_api.py** — Direct API Integration
- WebSocket connection to cTrader Open API
- Automatic reconnection with exponential backoff
- Position & order management
- Real-time account state monitoring
- **Smart Trailing Stop:**
  - ATR-based calculation (ATR × 1.5x)
  - Break-even at +15 pips
  - Dynamic updates every 5 seconds
  - Never moves stop loss against your profits

### 2. **gemini_analyzer.py** — AI Signal Validation
- Signal confidence scoring (0-100)
- Market sentiment analysis
- Risk/reward validation
- Suggested TP/SL levels
- Trade exit recommendations

### 3. **market_data_feed.py** — Real-Time Market Data
- Fetches XAUUSD & BTCUSD prices
- Calculates indicators:
  - RSI(14), ATR(14), EMA(50), EMA(200)
  - MACD, Bollinger Bands
  - Volume analysis
  - Trend detection (UPTREND/DOWNTREND/SIDEWAYS)
  - Volatility assessment (LOW/NORMAL/HIGH)
- Updates every 60 seconds

### 4. **balance_manager.py** — Account Safety
- Real-time balance & equity monitoring
- Margin usage tracking
- Prevents trading if:
  - Balance < $100
  - Used margin > 80%
  - Free margin insufficient
- Max lot size calculator

### 5. **market_analyzer.py** — 5-Filter Pipeline
1. **Filter 1**: Basic signal validation
2. **Filter 2**: Technical confluence (RSI, EMA, price action)
3. **Filter 3**: Risk/Reward ratio (min 1:1)
4. **Filter 4**: Market conditions (ATR > 0.5)
5. **Filter 5**: Account health check

### 6. **telegram_listener.py** — Signal Reception
- Telegram userbot for signal listening
- Automatic reconnection
- Break-even monitor with intelligent stop management
- News event detection

### 7. **watchdog.py** — Process Supervisor
- Monitors brain process liveness
- Auto-restart on crash
- Exponential backoff (5s → 120s)
- Crash loop detection & pause
- Telegram alerts on restart

### 8. **status.py** — Real-Time Dashboard
- Brain health status
- Market data snapshot
- cTrader connection status
- Account balance display
- Last 5 executed signals
- News mode status
- Auto-refresh option

---

## ⚙️ Configuration

### Environment Variables

```bash
# cTrader Open API
CTRADER_MODE=demo                # demo or live
CTRADER_ACCESS_TOKEN=xxx         # Your API token
CTRADER_ACCOUNT_ID=yyy           # Your account ID

# Telegram
TELEGRAM_API_ID=123              # From my.telegram.org/apps
TELEGRAM_API_HASH=xxx            # From my.telegram.org/apps
TELEGRAM_STRING_SESSION=xxx      # From generate_session.py

# Gemini AI (Optional)
GEMINI_API_KEY=xxx               # From makersuite.google.com

# System
LOG_LEVEL=INFO                   # DEBUG, INFO, WARNING, ERROR
```

### Get Credentials

```bash
# 1. cTrader Access Token
#    • Log in to cTrader
#    • Settings → API → OpenAPI
#    • Create Application → Copy Access Token

# 2. Telegram API ID & Hash
#    • Go to my.telegram.org/apps
#    • Log in with your Telegram account
#    • Create New Application
#    • Copy API ID and API Hash

# 3. Telegram String Session
python generate_session.py
#    • Enter API ID
#    • Enter API Hash
#    • Enter phone number
#    • Enter 2FA code (if enabled)
#    • Copy session string to .env

# 4. Gemini API Key (Optional)
#    • Go to makersuite.google.com/app/apikey
#    • Click "Create API Key"
#    • Copy to .env
```

---

## 📊 Smart Trailing Stop System

### Algorithm

```
1. Position opened with SL at entry ± (ATR × 1.5)

2. For BUY positions:
   IF P&L >= +15 pips:
      → Move SL to entry (break-even)
      → Trail SL: Max(current_price - ATR×1.5, last_SL)
   
   IF new_SL > last_SL:
      → Update SL in broker
      → Log update

3. For SELL positions:
   IF P&L >= +15 pips:
      → Move SL to entry (break-even)
      → Trail SL: Min(current_price + ATR×1.5, last_SL)
   
   IF new_SL < last_SL:
      → Update SL in broker
      → Log update

4. Repeat every 5 seconds while position is open
```

### Benefits

✅ **Never locks in losses** — SL only moves in your favor
✅ **Dynamic to volatility** — Uses ATR for intelligent spacing
✅ **Automatic profit locking** — Moves to break-even automatically
✅ **Reduces emotional trading** — No manual adjustments needed
✅ **Maximizes profitable trades** — Trails until reversal

---

## 🚀 Execution Flow

```
1. SIGNAL RECEIVED (from Telegram)
   ↓
2. BASIC VALIDATION
   ✓ Has required fields? (symbol, entry, SL, TP)
   ↓
3. TECHNICAL FILTERS (5-stage pipeline)
   ✓ Confluence check (RSI, EMA, price)
   ✓ Risk/Reward ratio (min 1:1)
   ✓ Market volatility (ATR check)
   ✓ Account balance sufficient?
   ↓
4. GEMINI AI ANALYSIS
   ✓ Confidence >= 60%?
   ✓ Risk level acceptable?
   ✓ Sentiment aligned?
   ↓
5. ORDER EXECUTION
   → cTrader Open API
   → Position opened
   → Trailing stop activated
   ↓
6. NOTIFICATION
   → Telegram alert
   → Audit log entry
   ↓
7. MONITORING
   → Smart trailing stop updates every 5s
   → Break-even reached → Telegram alert
   → P&L tracking
   → Auto-close on TP/SL hit
```

---

## 📈 Indicators & Analysis

### Technical Indicators
- **RSI(14)**: Momentum & overbought/oversold
- **ATR(14)**: Volatility & stop loss sizing
- **EMA(50)**: Trend direction
- **EMA(200)**: Long-term trend
- **MACD**: Trend confirmation
- **Bollinger Bands**: Support/resistance & breakout
- **Volume**: Trend strength

### Confluence Scoring
- 5+ indicators aligned = **High confluence** ✓ Execute
- 3-4 indicators aligned = **Medium confluence** ~ Review
- <3 indicators aligned = **Low confluence** ✗ Skip

---

## 🔒 Safety Features

1. **Balance Protection**
   - Minimum balance: $100
   - Max used margin: 80%
   - Auto-stops trading if insufficient funds

2. **Risk Management**
   - Max risk per trade: 1% of balance
   - Risk/reward min ratio: 1:1
   - Dynamic lot sizing

3. **Connection Safety**
   - Auto-reconnect on disconnect
   - Heartbeat monitoring (every 30s)
   - Watchdog supervision

4. **News Event Protection**
   - Detects high-impact USD events
   - Caps lot size during news (0.01L max)
   - ±30 minute event window

5. **Audit Trail**
   - All signals logged (signal_audit.jsonl)
   - All trades tracked
   - P&L history maintained

---

## 📱 Telegram Integration

### Notifications
```
🟢 Position Opened
   Symbol: XAUUSD
   Type: BUY
   Entry: $2,645.50
   Volume: 0.5L
   SL: $2,620.00
   TP: $2,670.00
   Time: 14:23 UTC

🎯 Break-Even Reached
   Symbol: XAUUSD
   New SL: $2,645.50 (entry level)
   Current: $2,650.00 (+15 pips)

🔴 Position Closed
   Symbol: XAUUSD
   Close: $2,665.75
   P&L: +$101.25
   Time: 15:45 UTC
```

---

## 📊 Monitoring

### Real-Time Status Dashboard
```bash
python status.py --watch 5
```

Shows:
- ✅ Brain health (alive/offline)
- 📊 Market data (price, RSI, ATR, EMA)
- 💰 Account balance (equity, margin usage)
- 📡 cTrader connection status
- 📰 News mode (active/clear)
- 📈 Last 5 executed signals

### Log Files
```bash
tail -f gold_blueprint.log      # Main brain logs
tail -f watchdog.log             # Watchdog restarts
cat signal_audit.jsonl           # All executed signals
```

---

## 🛠️ Development

### Project Structure
```
FlatWavyAccounting1/
├── python-brain/
│   ├── main.py                 # Entry point
│   ├── ctrader_api.py          # API integration
│   ├── gemini_analyzer.py      # AI validator
│   ├── market_data_feed.py     # Data fetching
│   ├── market_analyzer.py      # 5-filter pipeline
│   ├── balance_manager.py      # Account monitoring
│   ├── telegram_listener.py    # Signal listening
│   ├── signal_queue.py         # Execution relay
│   ├── channel_reporter.py     # Notifications
│   ├── control_bot.py          # Commands
│   ├── news_feed.py            # Calendar
│   ├── fix_executor.py         # Legacy MCP
│   └── requirements.txt        # Dependencies
├── watchdog.py                 # Process supervisor
├── status.py                   # Dashboard
├── generate_session.py         # Telegram session
├── Dockerfile                  # Docker build
├── docker-compose.yml          # Compose config
├── SETUP_GUIDE.md              # Installation guide
├── DEPLOYMENT.md               # Cloud deployment
└── README.md                   # This file
```

---

## ⚠️ Disclaimer

**Trading involves risk. Use this system at your own risk.**

- Always test on **DEMO account** first
- Start with **small lot sizes** (0.01L)
- Monitor regularly
- Never trade more than you can afford to lose
- Past performance ≠ future results

---

## 📞 Support

- **GitHub Issues**: [Report bugs](https://github.com/YamenBadr19/FlatWavyAccounting1/issues)
- **Documentation**: [SETUP_GUIDE.md](SETUP_GUIDE.md) | [DEPLOYMENT.md](DEPLOYMENT.md)
- **Logs**: Check `gold_blueprint.log` and `watchdog.log`

---

## 📄 License

MIT License — See LICENSE file

---

## 🙏 Credits

- cTrader API: [cTrader Official](https://ctrader.com/)
- Gemini AI: [Google AI Studio](https://makersuite.google.com/)
- Data: [yfinance](https://github.com/ranaroussi/yfinance)
- Indicators: [TA-Lib](https://github.com/mrjbq7/ta-lib)

---

**Happy profitable trading! 🚀📈**
