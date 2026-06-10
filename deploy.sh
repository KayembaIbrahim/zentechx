#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  One-command deploy to Railway
#  Run this on YOUR computer (Mac/Linux)
# ═══════════════════════════════════════════════════════════

set -e

echo "🚀 Creating Telegram Trading Bot project..."

mkdir -p telegram_bot && cd telegram_bot
mkdir -p data

# ── requirements.txt ──
cat > requirements.txt << 'EOF'
requests>=2.28.0
pandas>=2.0.0
numpy>=1.24.0
MetaTrader5>=5.0.0
EOF

# ── runtime.txt ──
echo "python-3.12.3" > runtime.txt

# ── Procfile ──
echo "worker: python main.py" > Procfile

# ── .gitignore ──
echo -e "data/\n__pycache__/\n*.pyc" > .gitignore

echo "✅ Files created!"

echo ""
echo "══════════════════════════════════════════════════"
echo "  NEXT STEPS:"
echo ""
echo "  1. Create main.py (paste from the bot code)"
echo ""
echo "  2. Run:"
echo "     git init"
echo "     git add ."
echo "     git commit -m \"bot\""
echo ""
echo "  3. Create repo at https://github.com/new"
echo "     git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git"
echo "     git push -u origin main"
echo ""
echo "  4. Go to railway.app → New Project → Deploy from GitHub"
echo ""
echo "  5. Add Environment Variables in Railway:"
echo "     BOT_TOKEN = 8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI"
echo "     CHANNEL_USERNAME = @zenfxctc"
echo "     ADMIN_USER_ID = 955396728"
echo "     DEMO_MODE = True"
echo "══════════════════════════════════════════════════"
