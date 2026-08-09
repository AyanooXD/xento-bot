#!/bin/bash
set -e

# Install Playwright browsers if not already installed
if ! python3 -c "from playwright._impl._driver import compute_driver_executable; print(compute_driver_executable())" 2>/dev/null; then
    echo "Installing Playwright browsers..."
    python3 -m playwright install chromium 2>/dev/null || {
        echo "Retrying with --with-deps..."
        python3 -m playwright install chromium --with-deps 2>/dev/null || true
    }
fi

echo "Starting Xento Bot..."
exec python3 bot.py
