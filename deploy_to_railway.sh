#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Run this on YOUR COMPUTER to deploy the bot to Railway
# ═══════════════════════════════════════════════════════════

set -e

echo "🚀 Creating Trading Bot project..."

# Create project folder
mkdir -p telegram_bot && cd telegram_bot

# ── requirements.txt ──
cat > requirements.txt << 'EOF'
python-telegram-bot>=22.0
pandas>=2.0.0
numpy>=1.24.0
requests>=2.28.0
MetaTrader5>=5.0.0
EOF

# ── runtime.txt ──
echo "python-3.12.3" > runtime.txt

# ── Procfile ──
echo "worker: python main.py" > Procfile

# ── main.py ──
# Download from this link (or create manually)
echo "📥 Downloading main.py..."
curl -sL "https://raw.githubusercontent.com/kyamundu1/zenfx-bot/main/main.py" -o main.py 2>/dev/null || {
  echo "⚠️  Couldn't download. Create main.py manually from the bot code."
  echo "   See instructions below."
}

# ── Git setup ──
git init
git add .
git commit -m "Initial commit"

echo ""
echo "══════════════════════════════════════════════════"
echo "  ✅ Project created!"
echo ""
echo "  Next steps:"
echo "  1. Create a repo on github.com/new"
echo "  2. Run:"
echo "     git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git"
echo "     git push -u origin main"
echo ""
echo "  3. Go to railway.app → New Project → Deploy from GitHub"
echo "  4. Add these environment variables:"
echo "     BOT_TOKEN = 8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI"
echo "     CHANNEL_USERNAME = @zenfxctc"
echo "     ADMIN_USER_ID = 955396728"
echo "     DEMO_MODE = True"
echo "══════════════════════════════════════════════════"
