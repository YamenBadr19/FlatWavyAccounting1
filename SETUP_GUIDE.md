# 🚀 FlatWavyAccounting — Complete Setup & Deployment Guide

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Local Machine Setup](#local-machine-setup)
3. [Cloud Deployment](#cloud-deployment)
4. [Configuration](#configuration)
5. [Running the System](#running-the-system)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Accounts & Credentials

1. **cTrader Account** (Demo or Live)
   - [Register at cTrader](https://www.ctrader.com/)
   - Get Access Token from cTrader API panel
   - Get Account ID from your trading account
   - Get Server Host (demo.ctraderapi.com or live.ctraderapi.com)

2. **Telegram Account**
   - API ID & API Hash from [my.telegram.org/apps](https://my.telegram.org/apps)
   - Phone number for authentication
   - Channel/Group ID for signals and notifications

3. **Google Gemini API Key** (Optional but recommended)
   - Get free API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Used for AI-powered signal analysis

4. **GitHub Personal Access Token** (For auto-commits)
   - Create at [GitHub Settings/Developer Settings](https://github.com/settings/tokens)
   - Requires: `repo`, `workflow` scopes

---

## Local Machine Setup

### Step 1: Clone the Repository

```bash
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1
```

### Step 2: Install Dependencies

#### Option A: Using Python pip (Recommended)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r python-brain/requirements.txt

# Install optional TA-Lib (for advanced indicators)
pip install ta-lib  # Might require special setup on Windows
```

#### Option B: Using conda

```bash
conda create -n flatwavy python=3.10
conda activate flatwavy
conda install -c conda-forge ta-lib
pip install -r python-brain/requirements.txt
```

#### Option C: Using Docker (Recommended for consistency)

```bash
# Build the Docker image
docker build -t flatwavy:latest .

# Run with environment variables
docker run -it \
  -e CTRADER_ACCESS_TOKEN="your-token" \
  -e CTRADER_ACCOUNT_ID="your-account-id" \
  -e TELEGRAM_API_ID="your-api-id" \
  -e TELEGRAM_API_HASH="your-api-hash" \
  -e TELEGRAM_STRING_SESSION="your-session" \
  -e GEMINI_API_KEY="your-gemini-key" \
  flatwavy:latest
```

### Step 3: Configure Environment Variables

Create `.env` file in the root directory:

```bash
# cTrader Configuration
CTRADER_MODE=demo                    # demo or live
CTRADER_HOST=demo.ctraderapi.com     # Auto-switched based on mode
CTRADER_PORT=5035
CTRADER_ACCESS_TOKEN=your_access_token_here
CTRADER_ACCOUNT_ID=your_account_id_here

# Telegram Configuration
TELEGRAM_API_ID=123456789
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_STRING_SESSION=your_session_string_here
TELEGRAM_PHONE=+1234567890

# Gemini AI (Optional)
GEMINI_API_KEY=your_gemini_api_key_here

# GitHub Auto-Commit (Optional)
GITHUB_TOKEN=your_github_token_here
GITHUB_REPO=YamenBadr19/FlatWavyAccounting1

# Logging
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
```

### Step 4: Get Telegram Credentials

```bash
# Run this to generate string session
python generate_session.py

# Follow prompts:
# 1. Enter API ID
# 2. Enter API Hash
# 3. Enter phone number
# 4. Enter 2FA code (if enabled)
# 5. Copy the generated session string to .env
```

### Step 5: Verify Installation

```bash
# Test imports
python -c "from python_brain.ctrader_api import CTraderOpenAPI; print('✓ cTrader API OK')"
python -c "from python_brain.gemini_analyzer import GeminiAnalyzer; print('✓ Gemini OK')"
python -c "from python_brain.market_data_feed import MarketDataFeed; print('✓ Market Feed OK')"

# Check environment variables
python -c "import os; print(f'Mode: {os.environ.get(\"CTRADER_MODE\")}')"
```

---

## Cloud Deployment

### Option 1: Heroku (Free tier available, limited)

```bash
# Install Heroku CLI
curl https://cli.heroku.com/install.sh | sh
heroku login

# Create app
heroku create flatwavy-trading-bot

# Set environment variables
heroku config:set CTRADER_ACCESS_TOKEN="your-token" --app flatwavy-trading-bot
heroku config:set CTRADER_ACCOUNT_ID="your-account" --app flatwavy-trading-bot
heroku config:set TELEGRAM_API_ID="your-id" --app flatwavy-trading-bot
heroku config:set TELEGRAM_API_HASH="your-hash" --app flatwavy-trading-bot
heroku config:set TELEGRAM_STRING_SESSION="your-session" --app flatwavy-trading-bot
heroku config:set GEMINI_API_KEY="your-key" --app flatwavy-trading-bot

# Push code
git push heroku feature/ctrader-open-api:main

# View logs
heroku logs --tail --app flatwavy-trading-bot
```

**Note:** Heroku sleeps free apps. Use [Uptime Robot](https://uptimerobot.com/) to keep it alive.

---

### Option 2: AWS EC2 (Production recommended)

#### Step 1: Launch EC2 Instance

```bash
# 1. Go to AWS Console → EC2
# 2. Launch Instance:
#    - Image: Ubuntu 22.04 LTS (Free tier eligible)
#    - Instance type: t2.micro (free) or t3.small (small trading volume)
#    - Storage: 20GB SSD
#    - Security group: Allow SSH (22), HTTP (80), HTTPS (443)

# 3. Download key pair (e.g., flatwavy.pem)
chmod 400 flatwavy.pem
```

#### Step 2: Connect & Setup

```bash
# Connect to instance
ssh -i flatwavy.pem ubuntu@your-instance-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3.10 python3-pip python3-venv git
sudo apt install -y build-essential libatlas-base-dev libblas-dev liblapack-dev gfortran

# Clone repository
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1

# Setup Python environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r python-brain/requirements.txt
```

#### Step 3: Configure Environment

```bash
# Create .env file
sudo nano .env

# Add all your credentials (paste from your local .env)
# Save: Ctrl+O, Enter, Ctrl+X

# Verify
cat .env
```

#### Step 4: Setup Systemd Service (Auto-restart)

```bash
# Create service file
sudo nano /etc/systemd/system/flatwavy.service
```

Paste:
```ini
[Unit]
Description=FlatWavyAccounting Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/FlatWavyAccounting1
Environment="PATH=/home/ubuntu/FlatWavyAccounting1/venv/bin"
ExecStart=/home/ubuntu/FlatWavyAccounting1/venv/bin/python watchdog.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable flatwavy
sudo systemctl start flatwavy

# Monitor
sudo journalctl -u flatwavy -f
```

---

### Option 3: DigitalOcean Droplet (Recommended - $4/month)

```bash
# 1. Create Droplet at digitalocean.com
#    - OS: Ubuntu 22.04
#    - Size: Basic ($4/month)
#    - Add SSH key

# 2. Connect via SSH
ssh root@your-droplet-ip

# 3. Run same setup as AWS
# (Follow AWS EC2 steps above)
```

---

### Option 4: Google Cloud Run (Serverless)

```bash
# 1. Install gcloud CLI
curl https://sdk.cloud.google.com | bash

# 2. Authenticate
gcloud auth login

# 3. Set project
gcloud config set project your-project-id

# 4. Build and deploy
gcloud run deploy flatwavy-trading-bot \
  --source . \
  --platform managed \
  --region us-central1 \
  --memory 512Mi \
  --timeout 3600 \
  --set-env-vars CTRADER_ACCESS_TOKEN=xxx,CTRADER_ACCOUNT_ID=yyy,... \
  --allow-unauthenticated
```

---

### Option 5: PythonAnywhere (Easiest)

```bash
# 1. Sign up at pythonanywhere.com (free tier)

# 2. Open Web console

# 3. Clone repo
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1
mkvirtualenv --python=/usr/bin/python3.10 flatwavy
pip install -r python-brain/requirements.txt

# 4. Create .env file
echo "CTRADER_ACCESS_TOKEN=xxx" > .env
echo "CTRADER_ACCOUNT_ID=yyy" >> .env
# ... add all variables

# 5. Setup Always-On Task in PythonAnywhere dashboard:
#    Command: /home/your-user/.virtualenvs/flatwavy/bin/python /home/your-user/FlatWavyAccounting1/watchdog.py
#    Working dir: /home/your-user/FlatWavyAccounting1
```

---

## Configuration

### cTrader API Credentials

**Where to get them:**
1. Log in to [cTrader](https://www.ctrader.com/)
2. Go to Settings → API → OpenAPI
3. Create New Application
4. Copy:
   - `ACCESS_TOKEN` (expires periodically, refresh when needed)
   - `ACCOUNT_ID` (your trading account number)

**Check connection:**
```bash
python -c "
import os
os.environ['CTRADER_ACCESS_TOKEN'] = 'your-token'
os.environ['CTRADER_ACCOUNT_ID'] = 'your-account'
from python_brain.ctrader_api import CTraderOpenAPI
print('✓ Connected')
"
```

### Telegram Setup

**Get API ID & Hash:**
1. Go to [my.telegram.org/apps](https://my.telegram.org/apps)
2. Log in with your Telegram account
3. Create New Application
4. Copy API ID and API Hash

**Get String Session:**
```bash
python generate_session.py
# Follow interactive prompts
```

### Gemini API (Optional)

```bash
# Go to https://makersuite.google.com/app/apikey
# Click "Create API Key"
# Copy and paste into .env
```

---

## Running the System

### Local Development (Watchdog + Brain)

```bash
# Terminal 1: Start watchdog (monitors brain)
source venv/bin/activate
python watchdog.py

# Terminal 2 (separate): Check status
python status.py --watch 5
```

### Production (Single Command)

```bash
# Everything runs via watchdog
source venv/bin/activate
python watchdog.py

# Output:
# 2026-06-11 14:23:45 [INFO] GOLD BLUEPRINT — WATCHDOG SUPERVISOR
# 2026-06-11 14:23:46 [INFO] Brain started (PID 12345)
# 2026-06-11 14:23:47 [INFO] Brain healthy | Uptime=1s | Restarts=0
```

### Monitor Status

```bash
# One-time snapshot
python status.py

# Auto-refresh every 5 seconds
python status.py --watch 5

# Custom refresh interval
python status.py -w 10
```

---

## Troubleshooting

### Issue: "cTrader API connection refused"

```bash
# Check:
1. Is cTrader running on your machine? (if using MCP)
2. Is CTRADER_ACCESS_TOKEN valid?
3. Is firewall blocking port 5035?

# Test:
telnet demo.ctraderapi.com 5035  # Should connect
```

### Issue: "Telegram credentials invalid"

```bash
# Regenerate session:
python generate_session.py

# Verify in code:
python -c "
import os
os.environ['TELEGRAM_STRING_SESSION'] = 'your-session'
from telethon import TelegramClient
print('✓ Valid')
"
```

### Issue: "Gemini API rate limited"

```bash
# Reduce analysis frequency or
# Upgrade API quota at https://makersuite.google.com
```

### Issue: "WebSocket disconnects frequently"

```bash
# Increase heartbeat timeout in ctrader_api.py:
RECONNECT_BASE_DELAY = 5.0  # Increase to 10.0
RECONNECT_MAX_DELAY = 120.0  # Increase to 300.0
```

### Issue: "Out of memory on cloud"

```bash
# Reduce data retention in market_data_feed.py:
close_history: List[float]  # Change from 50 bars to 20 bars

# Or upgrade instance type
```

---

## Next Steps

✅ **After setup:**
1. Test on demo account first
2. Run `python status.py` to verify all components
3. Check logs: `tail -f gold_blueprint.log`
4. Start with small lot sizes (0.01)
5. Monitor for 24 hours before live trading

🚀 **Happy trading!**
