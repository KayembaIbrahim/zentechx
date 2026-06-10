#!/bin/bash
# Restart the Telegram trading bot
cd "$(dirname "$0")"
pkill -f "telegram_trader_bot.py" 2>/dev/null
sleep 1
setsid /Zentech𝕏3/Trading/venv/bin/python -u telegram_trader_bot.py > logs/tg_bot.log 2>&1 &
echo "🤖 Bot restarted! PID: $!"
