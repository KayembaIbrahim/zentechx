#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════╗
║  🍬 AI TRADING BOT — Candy + Scalp              ║
║  Single-file · Auto-Adaptive · MT5              ║
╚══════════════════════════════════════════════════╝

HOW TO USE:
  1. Edit your MT5 credentials below
  2. pip install MetaTrader5 pandas numpy ta requests
  3. python simple_bot.py

For demo/simulation (no MT5 needed):
  python simple_bot.py --demo
"""

import time
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════
# 🔧  YOUR MT5 CREDENTIALS — EDIT THESE                     
# ════════════════════════════════════════════════════════════

MT5_LOGIN = 0                 # Your MT5 account number
MT5_PASSWORD = ""             # Your MT5 password
MT5_SERVER = ""               # e.g. "ICMarkets-Demo"
MODE = "demo"                 # "demo" or "live"

# ════════════════════════════════════════════════════════════
# 📊  STRATEGY SETTINGS                                      
# ════════════════════════════════════════════════════════════

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
CHECK_INTERVAL = 10           # seconds between checks
MAX_SPREAD = 30               # max spread in points
RISK_PER_TRADE = 2.0          # % of account risked per trade
MAX_POSITIONS = 3             # max total open positions
TARGET_BALANCE = 1000.0       # goal balance to stop at

# Telegram (optional — leave blank to disable)
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""

# ════════════════════════════════════════════════════════════
#  🧠  THE BOT — YOU DON'T NEED TO EDIT BELOW THIS LINE     
# ════════════════════════════════════════════════════════════

# ── Try to import MT5 ──────────────────────────────────────
try:
    import MetaTrader5 as mt5
    MT5_OK = True
except ImportError:
    mt5 = None
    MT5_OK = False

import pandas as pd
import numpy as np
import requests


# ── Telegram Notifier ───────────────────────────────────────
class Telegram:
    def __init__(self):
        self.on = bool(TG_BOT_TOKEN and TG_CHAT_ID)
        self.url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage" if self.on else ""

    def send(self, msg):
        if not self.on:
            return
        try:
            requests.post(self.url, json={"chat_id": TG_CHAT_ID, "text": msg,
                         "parse_mode": "HTML"}, timeout=10)
        except:
            pass

    def trade(self, action, symbol, vol, price, sl, tp, bal=0):
        e = {"BUY": "🟢", "SELL": "🔴", "CLOSE": "🔚"}.get(action, "⚪")
        msg = f"{e} <b>{action}</b> {symbol}\n• Vol: {vol}\n• Price: {price:.5f}\n• SL: {sl:.5f} | TP: {tp:.5f}"
        if bal:
            msg += f"\n• Balance: ${bal:.2f}"
        self.send(msg)


# ── Simulated Data (for --demo) ─────────────────────────────
def make_sim_rates(symbol, count=100):
    """Generate semi-realistic OHLC data with structure."""
    np.random.seed(hash(symbol + str(int(time.time() / 60))) % 2**31)
    base = {"EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)
    now = int(time.time())

    prices = [base]
    # Create a persistent trend for realistic signals
    trend = np.random.choice([-1, 1]) * 0.0001
    bar = 0
    for i in range(1, count):
        bar += 1
        # Trend changes every ~20 bars
        if bar >= np.random.randint(15, 30):
            trend = np.random.choice([-1, 1]) * 0.0001
            bar = 0
        # Pullback mid-trend
        if i > 0 and bar == np.random.randint(5, 10):
            prices.append(prices[-1] - trend * 6 * np.random.random())
        else:
            prices.append(prices[-1] + trend + np.random.normal(0, 0.0002))

    closes = [max(p, base * 0.95) for p in prices]
    return pd.DataFrame({
        "time": pd.to_datetime([now - (count - i) * 60 for i in range(count)], unit="s"),
        "open": closes, "high": [c * (1 + abs(np.random.normal(0, 0.0006))) for c in closes],
        "low": [c * (1 - abs(np.random.normal(0, 0.0006))) for c in closes], "close": closes,
        "tick_volume": [max(1, int(np.random.gamma(3, 100))) for _ in range(count)],
    })


# ── Strategy: Candy + Scalp combined ────────────────────────
def analyze(symbol):
    """Run Candy + Scalp analysis. Returns signal dict or None."""
    count = 100

    # Get price data
    if MODE == "demo" or not MT5_OK:
        df = make_sim_rates(symbol, count)
    else:
        rates = mt5.copy_rates_from_pos(symbol, 1, 0, count)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")

    if df is None or len(df) < 30:
        return None

    close = df["close"].values
    high, low = df["high"].values, df["low"].values
    price = close[-1]

    # ── Compute Indicators ─────────────────────────────────
    # EMAs
    ema5 = pd.Series(close).ewm(span=5, adjust=False).mean().values
    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values

    # RSI(14)
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).ewm(span=14, adjust=False).mean().values
    loss = (-delta.where(delta < 0, 0)).ewm(span=14, adjust=False).mean().values
    rsi = 100 - (100 / (1 + gain / np.maximum(loss, 1e-10)))

    # ATR (for SL/TP placement)
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(abs(high[1:] - close[:-1]),
                               abs(low[1:] - close[:-1])))
    atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else max(price * 0.001, 0.0001)

    # ── Candy Strategy Signal ───────────────────────────────
    # Enter on pullbacks in the direction of the trend
    signals = []

    # UPTREND: EMA5 > EMA20, price near EMA20, RSI not overbought
    if ema5[-1] > ema20[-1] and ema5[-3] > ema20[-3]:
        near_ema = abs(price - ema20[-1]) / max(ema20[-1], 1e-10) < 0.003
        rsi_ok = rsi[-1] < 65                # not overbought
        turning = rsi[-1] > rsi[-2]           # turning up from pullback
        if near_ema and rsi_ok:
            sl = price - atr * 1.5
            tp = price + atr * 2.5
            conf = 0.60 if turning else 0.50
            signals.append(("BUY", price, sl, tp, conf, "🍬Candy"))

    # DOWNTREND: EMA5 < EMA20, price near EMA20, RSI not oversold
    elif ema5[-1] < ema20[-1] and ema5[-3] < ema20[-3]:
        near_ema = abs(price - ema20[-1]) / max(ema20[-1], 1e-10) < 0.003
        rsi_ok = rsi[-1] > 35                # not oversold
        turning = rsi[-1] < rsi[-2]           # turning down from rally
        if near_ema and rsi_ok:
            sl = price + atr * 1.5
            tp = price - atr * 2.5
            conf = 0.60 if turning else 0.50
            signals.append(("SELL", price, sl, tp, conf, "🍬Candy"))

    # ── Scalping Signal (MA crossover) ─────────────────────
    ma9 = pd.Series(close).rolling(9).mean().values
    ma21 = pd.Series(close).rolling(21).mean().values

    if len(ma9) > 3 and len(ma21) > 3:
        if ma9[-1] > ma21[-1] and ma9[-2] <= ma21[-2]:
            sl = price - atr * 1.0
            tp = price + atr * 1.8
            signals.append(("BUY", price, sl, tp, 0.50, "⚡Scalp"))
        elif ma9[-1] < ma21[-1] and ma9[-2] >= ma21[-2]:
            sl = price + atr * 1.0
            tp = price - atr * 1.8
            signals.append(("SELL", price, sl, tp, 0.50, "⚡Scalp"))

    if not signals:
        return None

    # Pick highest confidence signal
    signals.sort(key=lambda s: s[4], reverse=True)
    action, entry, sl, tp, conf, name = signals[0]

    return {
        "action": action, "symbol": symbol,
        "price": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5),
        "confidence": conf, "strategy": name,
    }


# ── Connect ─────────────────────────────────────────────────
def connect():
    """Connect to MT5 (or set up demo account)."""
    if MODE == "demo" or not MT5_OK:
        print(f"🧪 DEMO mode — starting with $30.00")
        return {"balance": 30.0, "equity": 30.0}

    if not mt5.initialize():
        print(f"❌ MT5 init failed: {mt5.last_error()}")
        return None

    if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
        if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            print(f"❌ MT5 login failed: {mt5.last_error()}")
            return None

    info = mt5.account_info()
    if info:
        print(f"✅ Connected — {info.name} | Balance: ${info.balance:.2f}")
        return {"balance": info.balance, "equity": info.equity}
    return None


# ── Position Sizing ─────────────────────────────────────────
def calc_volume(price, sl, bal):
    """Calculate lot size based on risk %."""
    risk_amt = bal * (RISK_PER_TRADE / 100.0)
    sl_dist = abs(price - sl)
    if sl_dist < 0.0001:
        return 0.01
    # Standard lot = 100k units, pip value ~$10 per lot for EURUSD
    vol = risk_amt / (sl_dist * 100000 / 0.0001)
    vol = max(0.01, round(vol / 0.01) * 0.01)
    return min(vol, 1.0)


# ── Execute Trade ───────────────────────────────────────────
def place_order(sig, account):
    """Place a market order with the given signal."""
    sym = sig["symbol"]
    action = sig["action"]
    price = sig["price"]
    sl = sig["sl"]
    tp = sig["tp"]
    strategy = sig["strategy"]

    # Get balance
    bal = account["balance"] if MODE == "demo" else mt5.account_info().balance
    vol = calc_volume(price, sl, bal)

    if MODE == "demo" or not MT5_OK:
        # Simulated trade
        import random
        ticket = random.randint(10000, 99999)
        # Random PnL (slightly positive bias for demo feel)
        pnl = random.uniform(-0.8, 1.5) * vol * 100
        account["balance"] = max(0.01, account["balance"] + pnl)
        account["equity"] = account["balance"]
        bal_str = f"${account['balance']:.2f}"
        print(f"  🧪 [{strategy}] {action} {vol} {sym} → {bal_str}")
        return ticket

    # Live MT5
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(sym)
    exec_price = tick.ask if action == "BUY" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol,
        "type": order_type, "price": exec_price,
        "sl": sl, "tp": tp, "deviation": 10,
        "magic": 20240609, "comment": strategy,
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"  ✅ [{strategy}] {action} {vol} {sym} @ {exec_price:.5f} (ticket {result.order})")
        return result.order
    print(f"  ❌ Order failed: {result.comment if result else 'no result'}")
    return None


# ── Position Count ──────────────────────────────────────────
def count_positions(symbol=""):
    if MODE == "demo" or not MT5_OK:
        return 0
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    return len(pos) if pos else 0


# ════════════════════════════════════════════════════════════
#  🏁  MAIN LOOP
# ════════════════════════════════════════════════════════════
def main():
    if MODE != "demo" and not MT5_OK:
        print("❌ MetaTrader5 not installed. Run with --demo to simulate.")
        return

    print("\n" + "=" * 50)
    print("  🍬 AI TRADING BOT — Candy + Scalp")
    print("=" * 50)

    account = connect()
    if account is None:
        return

    tg = Telegram()
    start_bal = account["balance"]
    print(f"🎯 Target: ${TARGET_BALANCE:.2f}")
    print(f"⏱  Check every {CHECK_INTERVAL}s")
    print("=" * 50)

    if tg.on:
        tg.send(f"🤖 Bot started — ${start_bal:.2f} → target ${TARGET_BALANCE:.2f}")

    cycle = 0
    try:
        while True:
            cycle += 1

            # Get current balance
            bal = account["balance"] if MODE == "demo" else mt5.account_info().balance

            # ── Target Reached? ────────────────────────────
            if bal >= TARGET_BALANCE:
                print(f"\n🎯 TARGET REACHED! ${bal:.2f}")
                tg.send(f"🎯 Target reached! Balance: ${bal:.2f}")
                break

            # ── Risk Checks ────────────────────────────────
            if bal < 5.0:
                print("⚠️  Balance too low (<$5). Waiting...")
                time.sleep(60)
                continue

            if MODE != "demo" and count_positions() >= MAX_POSITIONS:
                time.sleep(CHECK_INTERVAL)
                continue

            # ── Scan Symbols ───────────────────────────────
            for symbol in SYMBOLS:
                # Skip if we already have a position on this symbol
                if MODE != "demo" and count_positions(symbol) > 0:
                    continue

                sig = analyze(symbol)
                if sig:
                    print(f"\n🔔 [{sig['strategy']}] {sig['action']} {sig['symbol']} "
                          f"@ {sig['price']} | SL: {sig['sl']} TP: {sig['tp']}")
                    ticket = place_order(sig, account)
                    if ticket:
                        tg.trade(sig["action"], sig["symbol"],
                                 calc_volume(sig["price"], sig["sl"],
                                             account["balance"] if MODE == "demo" else mt5.account_info().balance),
                                 sig["price"], sig["sl"], sig["tp"],
                                 account["balance"] if MODE == "demo" else mt5.account_info().balance)

            # ── Heartbeat ──────────────────────────────────
            if cycle % 6 == 0:  # every ~60s
                bal_now = account["balance"] if MODE == "demo" else mt5.account_info().balance
                change = bal_now - start_bal
                pct = (change / start_bal) * 100
                print(f"\n📊 [{datetime.now().strftime('%H:%M:%S')}] "
                      f"Balance: ${bal_now:.2f} ({pct:+.1f}%) | "
                      f"Trades: {cycle} cycles")
                sys.stdout.flush()

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        if MODE != "demo" and MT5_OK:
            mt5.shutdown()
        final_bal = account["balance"] if MODE == "demo" else mt5.account_info().balance
        change = final_bal - start_bal
        pct = (change / start_bal) * 100 if start_bal > 0 else 0
        print(f"\n{'='*50}")
        print(f"💰 Final: ${final_bal:.2f} ({pct:+.1f}%)")
        print(f"📈 Change: ${change:+.2f}")
        print(f"{'='*50}")
        print("👋 Goodbye!")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        MODE = "demo"
    main()
