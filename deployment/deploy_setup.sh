#!/bin/bash
# deploy_setup.sh — Automated setup script for AWS EC2 instance (Ubuntu 22.04 LTS)

set -e

echo "=== 1. Updating packages and installing dependencies ==="
sudo apt update -y
sudo apt install -y python3-pip python3-venv git

echo "=== 2. Creating python virtual environment ==="
python3 -m venv .venv
source .venv/bin/activate

echo "=== 3. Installing pip requirements ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== 4. Setting up systemd service ==="
# Copy systemd service file to system directory
sudo cp deployment/whatsapp-bot.service /etc/systemd/system/whatsapp-bot.service

# Reload daemon and enable/start service
sudo systemctl daemon-reload
sudo systemctl start whatsapp-bot
sudo systemctl enable whatsapp-bot

echo "=== 5. Checking service status ==="
sudo systemctl status whatsapp-bot --no-pager

echo "=== Setup completed! ==="
echo "NOTE: Make sure to create the '.env' file in the root folder (/home/ubuntu/whatsapp-bot/.env)"
echo "with your environment variables, then run: 'sudo systemctl restart whatsapp-bot'."
