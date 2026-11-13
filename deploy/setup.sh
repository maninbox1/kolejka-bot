#!/usr/bin/env bash
# Run on the server from inside ~/kolejka_bot:   bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Swap file on small machines (Chrome needs memory)
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2000 ] && ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# 2. Python + dependencies + headless Chromium
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

# 3. Telegram token
if [ ! -f .env ]; then
  read -rp "Paste your TELEGRAM_BOT_TOKEN: " TOKEN
  echo "TELEGRAM_BOT_TOKEN=$TOKEN" > .env
  chmod 600 .env
fi

# 4. Run as a service: starts on boot, restarts on crash
# (fill in the real user and folder: "ubuntu" on Oracle, your Google name on GCP, etc.)
sed -e "s|^User=.*|User=$(whoami)|" \
    -e "s|/home/ubuntu/kolejka_bot|$(pwd)|g" \
    deploy/kolejka-bot.service | sudo tee /etc/systemd/system/kolejka-bot.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now kolejka-bot
sleep 5
sudo systemctl --no-pager status kolejka-bot | head -15
echo "Done. Logs: sudo journalctl -u kolejka-bot -f"
