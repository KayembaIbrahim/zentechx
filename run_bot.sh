#!/bin/bash
cd "/Zentech𝕏3/Trading"
export BOT_TOKEN="8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI"
export CHANNEL_USERNAME="@zenfxctc"
export ADMIN_USER_ID="955396728"
export DEMO_MODE="True"
export FREE_TRADES="10"
export CHECK_INTERVAL="30"

while true; do
    echo "=== Starting bot at $(date) ==="
    python3 -u main.py 2>&1
    EXIT_CODE=$?
    echo "=== Bot exited with code $EXIT_CODE at $(date) ==="
    echo "=== Restarting in 5 seconds ==="
    sleep 5
done
