# 🌍 Cloud Deployment Quick Reference

## At-a-Glance Comparison

| Platform | Cost | Setup Time | Availability | Best For |
|----------|------|-----------|--------------|----------|
| **Local Machine** | $0 | 10 min | 99.9% (if always on) | Development |
| **Heroku** | Free-$50/mo | 5 min | 99.95% (limited) | Learning |
| **AWS EC2** | $5-20/mo | 20 min | 99.99% | Production |
| **DigitalOcean** | $4-10/mo | 15 min | 99.99% | Production |
| **Google Cloud Run** | $0.40/mi | 10 min | 99.95% | Serverless |
| **PythonAnywhere** | Free-$5/mo | 5 min | 99% | Easiest |

---

## 🚀 Quick Start by Platform

### 1. Local Machine (Windows/Mac/Linux)

```bash
# Setup (10 minutes)
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1
python3 -m venv venv
source venv/bin/activate
pip install -r python-brain/requirements.txt
echo "CTRADER_ACCESS_TOKEN=xxx" > .env
echo "CTRADER_ACCOUNT_ID=yyy" >> .env
# ... add other variables

# Run
python watchdog.py

# Monitor (in another terminal)
python status.py --watch
```

---

### 2. Heroku (Free but with limitations)

```bash
# Setup (5 minutes)
brew install heroku/brew/heroku  # macOS
# For Linux/Windows: https://devcenter.heroku.com/articles/heroku-cli

heroku login
heroku create flatwavy-trader
heroku buildpacks:add heroku/python --app flatwavy-trader

# Set secrets
heroku config:set CTRADER_ACCESS_TOKEN=xxx --app flatwavy-trader
heroku config:set CTRADER_ACCOUNT_ID=yyy --app flatwavy-trader
# ... (add all from your .env)

# Deploy
git push heroku feature/ctrader-open-api:main

# Monitor
heroku logs --tail --app flatwavy-trader
```

**Note:** Free Heroku apps sleep after 30 min. Use Uptime Robot (uptimerobot.com) to ping every 5 min and keep alive.

---

### 3. AWS EC2 (Most Popular)

#### 3A. Launch Instance

1. Go to [AWS Console](https://console.aws.amazon.com/ec2/)
2. **Launch Instance**:
   - AMI: Ubuntu 22.04 LTS (Free Tier Eligible)
   - Instance Type: `t2.micro` (free) or `t3.small` ($0.021/hr)
   - Storage: 20 GB SSD
   - Security Group:
     - SSH (22): Your IP only
     - HTTP (80): Open
     - HTTPS (443): Open
3. Download `.pem` key file

#### 3B. Connect & Deploy

```bash
# Make key readable
chmod 400 your-key.pem

# SSH in
ssh -i your-key.pem ubuntu@your-ec2-public-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python
sudo apt install -y python3.10 python3-pip python3-venv git
sudo apt install -y build-essential libatlas-base-dev

# Clone & setup
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1
python3 -m venv venv
source venv/bin/activate
pip install -r python-brain/requirements.txt

# Create .env
echo "CTRADER_ACCESS_TOKEN=xxx" > .env
echo "CTRADER_ACCOUNT_ID=yyy" >> .env
# ... add all credentials

# Run with nohup (survives SSH disconnect)
nohup python watchdog.py > watchdog.log 2>&1 &

# Check running
ps aux | grep watchdog

# View logs
tail -f watchdog.log
```

#### 3C. Auto-Restart on Reboot

```bash
# Create systemd service
sudo tee /etc/systemd/system/flatwavy.service > /dev/null << 'EOF'
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

[Install]
WantedBy=multi-user.target
EOF

# Enable & start
sudo systemctl enable flatwavy
sudo systemctl start flatwavy

# Check status
sudo systemctl status flatwavy

# View logs
sudo journalctl -u flatwavy -f
```

---

### 4. DigitalOcean ($4/month)

```bash
# 1. Create Droplet at digitalocean.com:
#    - OS: Ubuntu 22.04
#    - Plan: Basic ($4/month)
#    - Region: Closest to you
#    - Auth: SSH Key (create one)

# 2. Connect
ssh root@your-droplet-ip

# 3. Run same AWS steps above (from "Update system" onward)
```

---

### 5. Google Cloud Run (Serverless)

```bash
# 1. Install gcloud CLI
# https://cloud.google.com/sdk/docs/install

# 2. Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# 3. Create Dockerfile (if not exists)
# Already in repo

# 4. Deploy
gcloud run deploy flatwavy-trading-bot \
  --source . \
  --platform managed \
  --region us-central1 \
  --memory 1Gi \
  --timeout 3600 \
  --set-env-vars="CTRADER_ACCESS_TOKEN=xxx,CTRADER_ACCOUNT_ID=yyy,..." \
  --allow-unauthenticated

# 5. View logs
gcloud run logs read flatwavy-trading-bot
```

---

### 6. PythonAnywhere (Easiest for beginners)

```bash
# 1. Sign up at pythonanywhere.com (free tier: 1 always-on task)

# 2. Open Web console (Bash)

# 3. Clone repo
git clone https://github.com/YamenBadr19/FlatWavyAccounting1.git
cd FlatWavyAccounting1
mkvirtualenv --python=/usr/bin/python3.10 flatwavy
pip install -r python-brain/requirements.txt

# 4. Create .env
echo "CTRADER_ACCESS_TOKEN=xxx" > .env
echo "CTRADER_ACCOUNT_ID=yyy" >> .env
# ... add all

# 5. Go to "Always on" task page
# Create new task:
Command: /home/YOUR_USERNAME/.virtualenvs/flatwavy/bin/python /home/YOUR_USERNAME/FlatWavyAccounting1/watchdog.py
Working dir: /home/YOUR_USERNAME/FlatWavyAccounting1

# Task will run 24/7
```

---

## ✅ Post-Deployment Checklist

```bash
# After deploying to ANY platform:

☐ Verify .env variables are set
☐ Run: python status.py (check all green)
☐ Check logs: tail -f gold_blueprint.log
☐ Test with DEMO account first (not live!)
☐ Verify Telegram notifications are working
☐ Monitor for 24 hours before live trading
☐ Set up monitoring alerts (email/SMS)
☐ Backup your .env file locally
☐ Document your API tokens securely
```

---

## 💡 Recommended Setup for Different Users

**Beginner (Learning):**
→ Local Machine + Demo Account

**Intermediate (Small Trading):**
→ DigitalOcean $4/mo + Demo Account (first month)

**Advanced (Production):**
→ AWS EC2 t3.small + Auto-recovery + Monitoring

**Enterprise:**
→ AWS + RDS + CloudWatch + SNS alerts

---

**Happy trading! 🚀**
