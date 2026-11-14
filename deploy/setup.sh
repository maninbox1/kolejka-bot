#!/usr/bin/env bash
# Run on the server from inside ~/kolejka_bot:   bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Swap file on small machines (Chrome needs memory)
# (/proc/swaps instead of `swapon --show`: swapon isn't on a normal user's PATH on Debian)
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2000 ] && ! grep -q '^/swapfile' /proc/swaps; then
  [ -f /swapfile ] || { sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo /sbin/mkswap /swapfile; }
  sudo /sbin/swapon /swapfile
fi
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 2. Python + dependencies + headless Chromium
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
# Playwright knows the libraries for Ubuntu; on other distros (e.g. Debian 13 on Google Cloud)
# install the distro's own Chromium package instead, which pulls in the same system libraries.
if ! sudo .venv/bin/python -m playwright install-deps chromium; then
  echo "playwright install-deps failed - installing Chromium's libraries via the distro package"
  sudo apt-get install -y chromium fonts-liberation fonts-noto-color-emoji
fi
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
