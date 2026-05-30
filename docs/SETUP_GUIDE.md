# Gold Blueprint Trading System — Setup Guide

## Prerequisites

- Python 3.10+
- Telegram account (personal account, not a bot)
- cTrader Demo account (for validation before going live)
- GitHub account (for version control)

---

## Step 1: Configure Credentials

```bash
cp config.example.py config.py
```

Open `config.py` and fill in:

| Field | Where to get it |
|---|---|
| `TELEGRAM_API_ID` | https://my.telegram.org/apps → Create App |
| `TELEGRAM_API_HASH` | Same page as above |
| `TELEGRAM_PHONE` | Your phone number with country code, e.g. `+12025551234` |
| `SIGNALS_FOLDER_ID` | Run `python find_folders.py` (Step 2) |
| `NEWS_FOLDER_ID` | Run `python find_folders.py` (Step 2) |

---

## Step 2: Find Your Telegram Folder IDs

```bash
python find_folders.py
```

This will authenticate with Telegram (you will receive a confirmation code via SMS or Telegram message) and print all your channels/groups with their numeric IDs.

Copy the IDs of your **Signals** and **News** folders/channels into `config.py`.

---

## Step 3: Install Python Dependencies

```bash
pip install -r requirements.txt
```

The `cryptg` package (optional but recommended) enables C-level encryption, reducing CPU usage by ~60% on high-message-volume channels.

---

## Step 4: Start the Python Brain

```bash
python python-brain/main.py
```

Expected startup output:
```
============================================================
  GOLD BLUEPRINT TRADING SYSTEM — BRAIN ONLINE
  Architecture: Brain (Python) + Body (C# cTrader)
============================================================
[INFO] brain: Authenticated as: YourName (@yourhandle)
[INFO] brain: Signals folder: accessible
[INFO] brain: News folder: accessible
[INFO] brain: TELEGRAM LISTENER ACTIVE
[INFO] brain: Signal processing loop started
[INFO] brain: News processing loop started
[INFO] brain: Signal relay loop started
```

---

## Step 5: Install the cBot in cTrader

1. Open **cTrader Desktop**
2. Go to **Automate** → **New cBot**
3. Create a new project and copy in the 3 files from `csharp-body/`:
   - `Blueprint_cBot.cs` (main cBot file)
   - `RiskManager.cs` (add to the same project)
   - `PositionMonitor.cs` (add to the same project)
4. Build the project (Ctrl+B)
5. In the **Backtesting / Chart** panel, drag the cBot onto an **XAUUSD** chart

### cBot Parameters

| Parameter | Default | Description |
|---|---|---|
| Min Lot Size | 0.01 | Iron Rule minimum — do not change |
| Max Lot Size | 0.05 | Iron Rule maximum — do not change |
| Profit Lock Threshold | $10.00 | Trigger point for the 50% partial close |
| Trailing Stop (pips) | 10 | Distance to trail SL after break-even |
| Signal File Path | `signal_latest.json` | Path to the relay file from Python brain |

Set **Signal File Path** to the full absolute path of `signal_latest.json` in your project folder (e.g. `C:\GoldBlueprint\signal_latest.json`).

---

## Step 6: Validate on Demo (Mandatory)

**Do not skip this step.** Run for a minimum of 7 days on a Demo account:

1. Start `python python-brain/main.py`
2. Run the cBot on your **cTrader Demo** chart (XAUUSD, M1 or M5)
3. Monitor `gold_blueprint.log` for signal processing output
4. Check `signal_audit.jsonl` for a full record of every decision
5. Verify that:
   - BUY signals near R1 are BLOCKED in the log
   - News_Mode correctly clamps lot to 0.01
   - $10 profit lock fires and partial close executes
   - Trailing stop activates post break-even

---

## Step 7: Go Live

Once demo validation passes:

1. Switch `SESSION_NAME` in `config.py` to a new value (forces fresh auth)
2. Point the cBot to your **Live** cTrader account
3. Start with the minimum lot (News_Mode=True equivalent) for the first 48h
4. Monitor `signal_audit.jsonl` and cTrader logs daily

---

## Cloud Deployment (Python Brain)

To run the brain 24/7 without your laptop:

**Railway.app (recommended)**
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

**Render.com**
- Create a new Web Service
- Set Start Command: `python python-brain/main.py`
- Set environment variables: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`

> **Important**: On first cloud deploy, run locally first to generate the session file (`gold_blueprint_session.session`), then upload that file to the cloud service alongside your code. This avoids the 2FA code prompt on the server.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `config.py not found` | Copy `config.example.py` to `config.py` |
| `Cannot access Signals folder` | Re-run `find_folders.py` and verify the ID |
| `FloodWait` in logs | Normal — Telegram throttle. Script handles it automatically. |
| `Signal file not found` in cBot | Check Signal File Path parameter is the full absolute path |
| cBot not opening trades | Verify `is_ready_for_execution: true` appears in `signal_latest.json` |
