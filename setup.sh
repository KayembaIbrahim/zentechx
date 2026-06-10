#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  AI Trading Bot — Setup Script
#  Creates venv, installs deps, and verifies installation
# ═══════════════════════════════════════════════════════════

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

echo "╔══════════════════════════════════════════════════╗"
echo "║   🤖  AI Trading Bot — Setup                     ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Python Virtual Environment ───────────────────────
echo "📦 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
echo "   ✅ venv created at $BOT_DIR/venv"

# ── 2. Install Dependencies ─────────────────────────────
echo "📥 Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt 2>&1 || {
    echo "⚠️  Some packages may not be available on this system."
    echo "   MetaTrader5 requires Windows or a specific build."
    echo "   The bot will run in DEMO/simulation mode."
}
echo "   ✅ Dependencies installed"

# ── 3. Verify ──────────────────────────────────────────
echo ""
echo "🔍 Verifying installation..."
python3 -c "
import sys
sys.path.insert(0, '.')
print('   Python:', sys.version.split()[0])
try:
    import pandas; print('   ✅ pandas', pandas.__version__)
except: print('   ❌ pandas missing')
try:
    import numpy; print('   ✅ numpy', numpy.__version__)
except: print('   ❌ numpy missing')
try:
    import ta; print('   ✅ ta', ta.__version__)
except: print('   ❌ ta missing')
try:
    import yaml; print('   ✅ pyyaml', yaml.__version__)
except: print('   ❌ pyyaml missing')
try:
    import MetaTrader5; print('   ✅ MetaTrader5', MetaTrader5.__version__)
except: print('   ⚠️  MetaTrader5 not available — demo mode only')
"

echo ""
echo "⚙️  Creating default config if not exists..."
if [ ! -f config/settings.yaml ]; then
    cp config/settings.yaml.template config/settings.yaml 2>/dev/null || true
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   ✅ Setup Complete                              ║"
echo "║                                                  ║"
echo "║   Quick Start:                                   ║"
echo "║     source venv/bin/activate                     ║"
echo "║     python main.py --demo       (simulation)     ║"
echo "║     python main.py --test       (backtest)       ║"
echo "║                                                  ║"
echo "║   For Live Trading:                              ║"
║"║     1. Edit config/settings.yaml                     ║"
echo "║     2. Set your MT5 login/password/server         ║"
echo "║     3. python main.py                            ║"
echo "╚══════════════════════════════════════════════════╝"
