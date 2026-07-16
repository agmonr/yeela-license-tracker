#!/bin/bash
# Sets up the yeela-license-tracker bot: venv, Python deps, Playwright's
# Chromium, and a starter config.py. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

echo "Creating virtualenv..."
python3 -m venv venv
source venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright's Chromium browser..."
playwright install chromium
playwright install-deps chromium

if [ ! -f config.py ]; then
    echo "Creating config.py from config.example.py..."
    cp config.example.py config.py
    echo "Edit config.py with your Sheet ID and email addresses before running the bot."
else
    echo "config.py already exists, leaving it as-is."
fi

if ! command -v sendmail >/dev/null 2>&1; then
    echo "Warning: sendmail not found. mailer.py requires /usr/sbin/sendmail to send email."
fi

echo "Done. Activate the environment with: source venv/bin/activate"
