#!/bin/bash
# Setup script for Railway / local deployment
set -e

echo "🔧 Installing Python dependencies..."
pip install -r requirements.txt

echo "🌐 Installing Playwright browsers..."
playwright install chromium --with-deps 2>/dev/null || {
    echo "⚠️ Playwright browser install failed, trying without deps..."
    playwright install chromium
}

echo "✅ Setup complete!"
echo "Run: python3 bot.py <TELEGRAM_BOT_TOKEN>"
