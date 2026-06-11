# 🔐 Token Management Guide — FlatWavyAccounting

## Overview

This document explains all tokens and credentials required for the FlatWavyAccounting trading system, where to get them, how to renew them, and best practices for managing them securely.

---

## Token Types & Expiration

| Token | Source | Expiration | Renewal | Priority |
|-------|--------|------------|---------|----------|
| **cTrader Access Token** | cTrader API | 24 hours | Automatic | 🔴 CRITICAL |
| **cTrader Refresh Token** | cTrader API | 90 days | Automatic | 🟡 HIGH |
| **Telegram String Session** | Telethon | 6 months | Manual | 🟢 MEDIUM |
| **Gemini API Key** | Google AI Studio | None | Manual | 🟢 MEDIUM |
| **GitHub Token** | GitHub Settings | Configurable | Manual | 🟡 HIGH |

---

## 1. cTrader Tokens (Most Critical)

### 1.1 Access Token

**Purpose**: Authenticates API requests to cTrader broker  
**Expiration**: **24 hours** (CRITICAL!)  
**Renewal**: Automatic via Refresh Token (system handles)

#### Where to Get

1. Log in to [cTrader](https://www.ctrader.com/)
2. Go to **Settings** → **API** → **OpenAPI**
3. Click **"Create Application"**
4. Fill in:
   - Application Name: `FlatWavyAccounting`
   - Redirect URI: `http://localhost:8000` (or your server IP)
5. Click **"Create"**
6. You'll get:
   - **Client ID** (save this)
   - **Client Secret** (save this securely)

#### Get Initial Token

```bash
# Method 1: Using cTrader Web UI (Easiest)
1. In the OpenAPI settings, click "Generate New Token"
2. You'll get:
   - ACCESS_TOKEN (24 hours valid)
   - REFRESH_TOKEN (90 days valid)
3. Copy both to .env immediately

# Method 2: Using cURL
curl -X POST "https://api.ctrader.com/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"

# Response:
{
  "access_token": "eyJ...",
  "expires_in": 86400,
  "token_type": "Bearer",
  "refresh_token": "abc123..."
}
```

#### Setup in Environment

```bash
# Add to .env
CTRADER_CLIENT_ID=your-client-id-here
CTRADER_CLIENT_SECRET=your-client-secret-here
CTRADER_ACCESS_TOKEN=your-access-token-here
CTRADER_REFRESH_TOKEN=your-refresh-token-here
CTRADER_ACCOUNT_ID=your-account-id-here
CTRADER_MODE=demo  # or 'live'
```

### 1.2 Refresh Token

**Purpose**: Automatically renews Access Token without manual intervention  
**Expiration**: **90 days**  
**Auto-Renewal**: YES (built into ctrader_api.py)

#### Automatic Renewal Process

The system automatically:
1. ✅ Checks token expiration every hour
2. ✅ Refreshes 1 hour before expiration
3. ✅ Updates .env with new tokens
4. ✅ Restarts connection with new token
5. ✅ Logs all renewal events

#### Manual Refresh (if auto fails)

```bash
curl -X POST "https://api.ctrader.com/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=refresh_token&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET&refresh_token=YOUR_REFRESH_TOKEN"
```

#### When Refresh Token Expires (90 days)

You must manually generate a new pair:

```bash
# 1. Go to cTrader OpenAPI settings
# 2. Click "Generate New Token"
# 3. Update .env with new tokens
# 4. Restart the bot
python watchdog.py
```

### 1.3 Client ID & Client Secret

**Purpose**: Identify your application to cTrader  
**Expiration**: None (permanent unless revoked)  
**Renewal**: Manual (can regenerate in settings)

#### Security Best Practices

```bash
# ✅ DO
- Store in .env (git-ignored)
- Store in Secrets Manager (production)
- Rotate annually
- Use separate credentials for demo/live

# ❌ DON'T
- Commit to GitHub
- Share with anyone
- Use in logs
- Hardcode in source files
```

---

## 2. Telegram Tokens

### 2.1 Telegram API ID & Hash

**Purpose**: Authenticate Telegram userbot  
**Expiration**: None (permanent)  
**Renewal**: None (stored in app settings)

#### Where to Get

```bash
# 1. Go to https://my.telegram.org/apps
# 2. Log in with your phone number
# 3. Click "Create New Application"
# 4. Fill in:
#    - App title: "FlatWavyAccounting"
#    - Short name: "flatwavy"
#    - URL: "https://github.com/YamenBadr19/FlatWavyAccounting1"
# 5. Click "Create"
# 6. Save:
#    - API ID (integer)
#    - API Hash (string)
```

#### Setup in Environment

```bash
TELEGRAM_API_ID=1234567890
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_PHONE=+1234567890  # Your phone with country code
```

### 2.2 Telegram String Session

**Purpose**: Session token for Telethon userbot (avoids 2FA prompt)  
**Expiration**: ~6 months (Telegram invalidates inactive sessions)  
**Renewal**: Manual (regenerate if fails)

#### Generate Session String

```bash
# Interactive generation (recommended)
python generate_session.py

# Follow prompts:
# 1. Enter API ID: 1234567890
# 2. Enter API Hash: abcdef...
# 3. Enter phone: +1234567890
# 4. Enter 2FA code (if enabled): 123456
# 5. Copy session string to .env

# Result:
TELEGRAM_STRING_SESSION=1BVtsONcBu...(very long string)
```

#### Troubleshooting Session

```bash
# If "Invalid session" error:
# Option 1: Regenerate
python generate_session.py

# Option 2: Check if phone/2FA changed
# Option 3: Delete old session from Telegram Settings → Sessions

# Verify session is valid
python -c "
from telethon import TelegramClient
from telethon.sessions import StringSession
import os

session = os.environ.get('TELEGRAM_STRING_SESSION')
client = TelegramClient(StringSession(session), API_ID, API_HASH)
with client:
    print('✓ Session valid')
"
```

---

## 3. Google Gemini API Key

**Purpose**: AI signal validation (optional but recommended)  
**Expiration**: None (permanent unless revoked)  
**Renewal**: Manual

### Where to Get

```bash
# 1. Go to https://makersuite.google.com/app/apikey
# 2. Click "Create API Key"
# 3. Copy the key
# 4. Add to .env:
GEMINI_API_KEY=AIzaSyDx...(long string)
```

### Free Tier Limits

```
Rate: 60 requests per minute
Quota: 1,500 requests per day (free)
Tokens: 32k input, 8k output
```

### If Rate Limited

```bash
# Option 1: Wait 60 seconds (auto-retry in code)
# Option 2: Reduce analysis frequency
# Option 3: Upgrade to paid tier
# Option 4: Disable Gemini (graceful fallback in code)
```

### Upgrade to Paid

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Enable billing
3. Set up payment method
4. Increases to 10,000 RPM

---

## 4. GitHub Token (for auto-commits)

**Purpose**: Auto-push logs & trades to GitHub  
**Expiration**: Configurable (30-365 days recommended)  
**Renewal**: Manual

### Where to Get

```bash
# 1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
# 2. Click "Generate new token (classic)"
# 3. Set:
#    - Token name: "FlatWavyAccounting"
#    - Expiration: 90 days
#    - Scopes: repo (all), workflow
# 4. Click "Generate token"
# 5. Copy token immediately (can't see again)
```

### Setup in Environment

```bash
GITHUB_TOKEN=ghp_1234567890abcdefgh...
GITHUB_REPO=YamenBadr19/FlatWavyAccounting1
```

### Auto-Commit Setup

```bash
# System automatically commits:
# - Every hour (hourly summary)
# - After major trades (P&L updates)
# - On errors (crash logs)

# Configure frequency in main.py:
AUTO_COMMIT_INTERVAL = 3600  # seconds (1 hour)
```

---

## 5. OpenAI API Key (Optional)

**Purpose**: Alternative to Gemini (if preferred)  
**Expiration**: None (permanent)  
**Renewal**: Manual

### Where to Get

```bash
# 1. Go to https://platform.openai.com/api/keys
# 2. Click "Create new secret key"
# 3. Copy key
# 4. Add to .env:
OPENAI_API_KEY=sk-proj-...(long string)
```

### Setup in Code (if using)

```python
# In gemini_analyzer.py, modify to support both:
if GEMINI_API_KEY:
    # Use Gemini
elif OPENAI_API_KEY:
    # Use OpenAI
else:
    # Fallback to technical analysis only
```

---

## Token Storage & Security

### Development (Local Machine)

```bash
# .env file (never commit!)
CTRADER_CLIENT_ID=xxx
CTRADER_CLIENT_SECRET=xxx
CTRADER_ACCESS_TOKEN=xxx
CTRADER_REFRESH_TOKEN=xxx
TELEGRAM_API_ID=xxx
TELEGRAM_API_HASH=xxx
TELEGRAM_STRING_SESSION=xxx
GEMINI_API_KEY=xxx
GITHUB_TOKEN=xxx

# In .gitignore:
.env
.env.local
*.pem
session*.json
```

### Production (Cloud)

#### AWS EC2

```bash
# Store in AWS Secrets Manager
aws secretsmanager create-secret \
  --name flatwavy-tokens \
  --secret-string '{
    "CTRADER_ACCESS_TOKEN": "xxx",
    "CTRADER_REFRESH_TOKEN": "xxx",
    ...
  }'

# Retrieve in code:
import json
import boto3

client = boto3.client('secretsmanager')
secret = json.loads(
    client.get_secret_value(SecretId='flatwavy-tokens')['SecretString']
)
os.environ['CTRADER_ACCESS_TOKEN'] = secret['CTRADER_ACCESS_TOKEN']
```

#### Heroku / Google Cloud

```bash
# Heroku Config Vars
heroku config:set CTRADER_ACCESS_TOKEN="xxx" --app flatwavy-trader
heroku config:set CTRADER_REFRESH_TOKEN="xxx" --app flatwavy-trader
# ... (all tokens)

# Google Cloud
gcloud secrets create ctrader-access-token --replication-policy="automatic" --data-file=-
gcloud secrets create ctrader-refresh-token --replication-policy="automatic" --data-file=-

# Retrieve in Cloud Run:
from google.cloud import secretmanager

def access_secret(secret_id, version_id="latest"):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/PROJECT_ID/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

os.environ['CTRADER_ACCESS_TOKEN'] = access_secret('ctrader-access-token')
```

### Rotation Schedule

```
✅ cTrader Access Token:     Every 24h (automatic)
✅ cTrader Refresh Token:    Every 90d (manual or automatic)
✅ Telegram String Session:  Every 180d (manual)
✅ Gemini API Key:           Every 365d (manual)
✅ GitHub Token:             Every 90d (manual)
✅ OpenAI API Key:           Every 365d (manual)
```

---

## Token Lifecycle Management

### Creating & Storing

1. ✅ Generate token from source
2. ✅ Store in secure location (.env or Secrets Manager)
3. ✅ Set `TOKEN_CREATED_AT` timestamp
4. ✅ Document expiration date
5. ✅ Set reminder for renewal

### Monitoring

```bash
# System logs token events:
grep "token" gold_blueprint.log

# Watch for:
# - Token renewal successful
# - Token expiration warnings
# - Token refresh failures
# - Connection drops (may indicate expired token)
```

### Renewal

```bash
# Automatic (built-in):
# - cTrader Access Token: System refreshes 1 hour before expiry
# - cTrader Refresh Token: System can auto-refresh (if refresh token set)

# Manual (required):
# - Telegram: Run generate_session.py
# - Gemini: Get new key from console
# - GitHub: Generate new token in settings

# After renewal:
# 1. Update .env
# 2. Restart the bot
# 3. Verify in logs: "Token updated successfully"
```

### Revocation (Emergency)

```bash
# If token compromised:

# cTrader:
# 1. Go to API settings
# 2. Click "Revoke" on the application
# 3. Generate new Client ID/Secret
# 4. Generate new Access + Refresh tokens
# 5. Update .env

# Telegram:
# 1. Go to Telegram Settings → Sessions
# 2. Terminate the "FlatWavyAccounting" session
# 3. Run: python generate_session.py
# 4. Update .env

# GitHub:
# 1. Go to Settings → Developer settings → Tokens
# 2. Click "Delete" on the token
# 3. Generate new token
# 4. Update .env

# Gemini:
# 1. Go to https://makersuite.google.com/app/apikey
# 2. Click delete on the key
# 3. Create new key
# 4. Update .env
```

---

## .env Template

```bash
# ======================================
# CTRADER TOKENS (CRITICAL)
# ======================================
CTRADER_MODE=demo                      # demo or live
CTRADER_CLIENT_ID=your-client-id       # From cTrader API
CTRADER_CLIENT_SECRET=your-secret      # From cTrader API (keep secret!)
CTRADER_ACCESS_TOKEN=eyJ...            # Expires in 24 hours
CTRADER_REFRESH_TOKEN=abc123...        # Expires in 90 days
CTRADER_ACCOUNT_ID=123456789           # Your trading account number

# ======================================
# TELEGRAM CREDENTIALS
# ======================================
TELEGRAM_API_ID=1234567890             # From my.telegram.org/apps
TELEGRAM_API_HASH=abcdef...            # From my.telegram.org/apps
TELEGRAM_PHONE=+1234567890             # Your phone with country code
TELEGRAM_STRING_SESSION=1BVts...       # From generate_session.py

# ======================================
# AI & ANALYSIS (OPTIONAL)
# ======================================
GEMINI_API_KEY=AIzaSy...               # From makersuite.google.com
# OPENAI_API_KEY=sk-proj-...           # Alternative to Gemini

# ======================================
# GITHUB AUTO-COMMIT (OPTIONAL)
# ======================================
GITHUB_TOKEN=ghp_...                   # From GitHub Settings
GITHUB_REPO=YamenBadr19/FlatWavyAccounting1

# ======================================
# SYSTEM
# ======================================
LOG_LEVEL=INFO                         # DEBUG, INFO, WARNING, ERROR
AUTO_COMMIT_INTERVAL=3600              # Seconds (1 hour)
TOKEN_RENEWAL_CHECK_INTERVAL=3600      # Check every hour
```

---

## Token Renewal Checklist

### Monthly

- [ ] Check cTrader Access Token status in logs
- [ ] Verify Refresh Token valid (90 - X days left)
- [ ] Monitor Gemini API quota usage

### Quarterly

- [ ] Regenerate cTrader Refresh Token (if approaching 90 days)
- [ ] Rotate GitHub Token (if using 90-day expiration)
- [ ] Verify Telegram session still active

### Annually

- [ ] Rotate all tokens
- [ ] Update Client ID/Secret if needed
- [ ] Audit all stored credentials
- [ ] Review Secrets Manager/cloud settings

---

## Troubleshooting

### "401 Unauthorized" Error

```bash
# Likely cause: Expired Access Token

# Solution 1: Auto-renewal
# - System should auto-refresh, check logs:
grep "token.*refresh" gold_blueprint.log

# Solution 2: Manual refresh
# - Update CTRADER_REFRESH_TOKEN in .env
# - Restart the bot

# Solution 3: Generate new tokens
# - Go to cTrader API settings
# - Click "Generate New Token"
# - Update .env
```

### "Invalid Telegram Session"

```bash
# Regenerate:
python generate_session.py

# Or check:
# 1. Did you change your phone number?
# 2. Did you enable 2FA and forgot the code?
# 3. Is the session old (>6 months)?
# 4. Did Telegram revoke the session?

# Solution:
# 1. Regenerate in Telegram Settings → Sessions (terminate old)
# 2. Run: python generate_session.py
# 3. Update .env
# 4. Restart bot
```

### "Gemini API Rate Limited"

```bash
# Free tier: 60 req/min, 1,500 req/day

# Solutions:
# 1. Wait 60 seconds (auto-retry)
# 2. Reduce analysis frequency
# 3. Upgrade to paid tier
# 4. Disable Gemini (code falls back gracefully)
```

---

## Security Best Practices

✅ **DO**
- Store tokens in .env (git-ignored)
- Use environment variables in production
- Rotate tokens regularly
- Use Secrets Manager (AWS, GCP, Azure)
- Set up token expiration alerts
- Log all token renewal attempts
- Use separate demo/live tokens
- Revoke compromised tokens immediately

❌ **DON'T**
- Commit .env to Git
- Share tokens with anyone
- Use tokens in logs
- Hardcode tokens in code
- Reuse tokens across projects
- Store tokens in version control
- Use weak/predictable token names
- Ignore token rotation reminders

---

## Token Renewal Automation

The system automatically handles:

```python
# In ctrader_api.py
class TokenManager:
    async def auto_renew_tokens(self):
        """Check and renew tokens every hour."""
        while True:
            await asyncio.sleep(3600)  # Every hour
            
            # Check Access Token (24h expiry)
            if self.is_access_token_expiring(hours=1):
                await self.refresh_access_token()
            
            # Check Refresh Token (90d expiry)
            if self.is_refresh_token_expiring(days=7):
                await self.notify_refresh_token_renewal()
            
            # Log status
            logger.info("Token status: OK")
```

---

## Emergency Contacts

- **cTrader Support**: https://www.ctrader.com/help/
- **Telegram Support**: https://telegram.org/support
- **Google Support**: https://support.google.com/
- **GitHub Support**: https://support.github.com/

---

**Last Updated**: June 2026  
**Version**: 1.0  
**Maintained By**: FlatWavyAccounting Team
