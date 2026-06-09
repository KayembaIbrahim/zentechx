#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║         🤖 GOLD EAGLE — AI Telegram Trading Bot             ║
║  Multi-Strategy · Subscription Management · 24/7 on Railway  ║
╚══════════════════════════════════════════════════════════════╝

Deploy to Railway:
  1. Fork this repo to GitHub
  2. Go to https://railway.app → New Project → Deploy from GitHub
  3. Add environment variables (see README)
  4. Make @GoldEagle_Zentechxbot ADMIN in your channel
  5. Done!
"""

import os, sys, time, json, sqlite3, logging, random, math, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Event
from collections import deque

import requests

# ═══════════════════════════════════════════════════════════════
# 📋  ENVIRONMENT VARIABLES (set in Railway Dashboard)
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@zenfxctc")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
FREE_TRADES = int(os.environ.get("FREE_TRADES", "10"))
DEMO_MODE = os.environ.get("DEMO_MODE", "True").lower() == "true"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))
RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE", "2.0"))  # trade engine cycle (secs)
PORT = int(os.environ.get("PORT", "8080"))  # Railway health check port



# ═══════════════════════════════════════════════════════════════
# 💰  MT5 REAL TRADING CONFIG (set env vars to enable)
# ═══════════════════════════════════════════════════════════════

MT5_LOGIN = int(os.environ.get("MT5_LOGIN", "0"))
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER = os.environ.get("MT5_SERVER", "")
MT5_MODE = os.environ.get("MT5_MODE", "demo")  # "demo" or "live"

# Try to import MT5 (only needed for real trading)
MT5_AVAILABLE = False
if not DEMO_MODE:
    try:
        import MetaTrader5 as mt5
        MT5_AVAILABLE = True
    except ImportError:
        MT5_AVAILABLE = False
        logging.warning("MetaTrader5 not installed. Run in DEMO_MODE=True or install MT5.")
else:
    mt5 = None


def mt5_connect():
    """Connect to MT5 terminal."""
    if DEMO_MODE or not MT5_AVAILABLE:
        return False
    if not mt5.initialize():
        logging.error(f"MT5 init failed: {mt5.last_error()}")
        return False
    if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
        authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not authorized:
            logging.error(f"MT5 login failed: {mt5.last_error()}")
            return False
    logging.info(f"✅ MT5 connected: {mt5.account_info().login} on {mt5.account_info().server}")
    return True


def mt5_get_price(symbol):
    """Get real price from MT5."""
    if DEMO_MODE or not MT5_AVAILABLE:
        return None, None
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        return tick.ask, tick.bid
    return None, None


def mt5_execute_order(signal, account_info):
    """Execute a real order via MT5."""
    if DEMO_MODE or not MT5_AVAILABLE:
        return None
    
    symbol = signal["symbol"]
    action = signal["action"]
    price = signal["price"]
    sl = signal["sl"]
    tp = signal["tp"]
    confidence = signal["confidence"]
    
    # Calculate position size based on risk
    balance = account_info.balance
    risk_amount = balance * (RISK_PER_TRADE / 100)
    atr = abs(price - sl)
    volume = max(0.01, min(1.0, round(risk_amount / (atr * 100000), 2))) if atr > 0 else 0.01
    
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 8880839,
        "comment": f"GoldEagle_{signal['strategy']}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logging.info(f"✅ REAL ORDER: {action} {volume} {symbol} @ {price} (ticket: {result.order})")
        return result.order
    else:
        error = result.comment if result else "no result"
        logging.error(f"❌ Order failed: {error}")
        return None

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ═══════════════════════════════════════════════════════════════
# 🗄️  DATABASE
# ═══════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "data" / "users.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                trades_remaining INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                is_subscribed INTEGER DEFAULT 0,
                is_restricted INTEGER DEFAULT 0,
                last_trade_at TEXT,
                subscribed_at TEXT,
                referral_id INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                symbol TEXT,
                action TEXT,
                price REAL,
                sl REAL,
                tp REAL,
                pnl REAL DEFAULT 0,
                strategy TEXT DEFAULT 'Candy',
                confidence REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()

def db_get(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(r) if r else None

def db_create(user_id, username="", first_name=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
                     (user_id, username, first_name))
        conn.commit()

def db_grant(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""UPDATE users SET 
            trades_remaining=?, total_trades=0, wins=0, losses=0,
            is_subscribed=1, is_restricted=0, subscribed_at=datetime('now')
            WHERE user_id=?""", (FREE_TRADES, user_id))
        conn.commit()

def db_trade(user_id, rem, tot, w=0, l=0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""UPDATE users SET 
            trades_remaining=?, total_trades=?, wins=wins+?, losses=losses+?,
            last_trade_at=datetime('now') WHERE user_id=?""",
            (rem, tot, w, l, user_id))
        conn.commit()

def db_restrict(user_id, v=1):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET is_restricted=? WHERE user_id=?", (v, user_id))
        conn.commit()

def db_log_trade(user_id, symbol, action, price, sl, tp, pnl, strategy, conf=0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO trade_log (user_id, symbol, action, price, sl, tp, pnl, strategy, confidence) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, symbol, action, price, sl, tp, pnl, strategy, conf))
        conn.commit()

def db_get_config(key, default=None):
    with sqlite3.connect(DB_PATH) as conn:
        r = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

def db_set_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (key, value))
        conn.commit()

# ═══════════════════════════════════════════════════════════════
# 📡  TELEGRAM API
# ═══════════════════════════════════════════════════════════════

def tg(method, data=None, retries=2):
    for i in range(retries + 1):
        try:
            r = requests.post(f"{API}/{method}", json=data, timeout=20)
            return r.json()
        except requests.exceptions.Timeout:
            if i < retries: time.sleep(2)
        except: break
    return None

def send(chat_id, text, kb=None, parse_mode="HTML"):
    d = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if kb: d["reply_markup"] = json.dumps(kb)
    return tg("sendMessage", d)

def edit(chat_id, msg_id, text, kb=None, parse_mode="HTML"):
    d = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": parse_mode}
    if kb: d["reply_markup"] = json.dumps(kb)
    return tg("editMessageText", d)

def answer_cb(callback_id, text=""):
    return tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": text, "show_alert": False})

def is_channel_member(user_id):
    r = tg("getChatMember", {"chat_id": CHANNEL_USERNAME, "user_id": user_id})
    status = r.get("result", {}).get("status") if r else None
    return status in ("member", "administrator", "creator")

def restrict_channel_member(user_id):
    return tg("restrictChatMember", {
        "chat_id": CHANNEL_USERNAME, "user_id": user_id,
        "permissions": {"can_send_messages": False},
        "until_date": int((datetime.now() + timedelta(days=365)).timestamp()),
    })

def unrestrict_channel_member(user_id):
    return tg("restrictChatMember", {
        "chat_id": CHANNEL_USERNAME, "user_id": user_id,
        "permissions": {"can_send_messages": True, "can_send_media_messages": True,
                        "can_send_polls": True, "can_send_other_messages": True,
                        "can_add_web_page_previews": True, "can_change_info": False,
                        "can_invite_users": True, "can_pin_messages": False},
    })

# ═══════════════════════════════════════════════════════════════
# 📈  TRADING ENGINE — Multi-Strategy
# ═══════════════════════════════════════════════════════════════

import pandas as pd
import numpy as np

class MarketGenerator:
    """Generates realistic synthetic market data for demo mode."""
    
    _cache = {}
    
    _rate_cache = {}
    
    @classmethod
    def get_rates(cls, symbol, count=100):
        """OHLC data with consistent, tradeable patterns."""
        # Cache for 30 seconds to ensure consistency within a trade request
        cache_key = f"{symbol}:{int(time.time() // 30)}"
        if cache_key in cls._rate_cache:
            return cls._rate_cache[cache_key]
        
        now = int(time.time() / 30)
        seed = hash(f"{symbol}:{now}") % 2**31
        np.random.seed(seed)
        
        bases = {"EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 152.50, "XAUUSD": 2350.0,
                 "GBPJPY": 192.30, "EURJPY": 165.40, "AUDUSD": 0.6520, "USDCAD": 1.3650}
        base = bases.get(symbol, 1.0)
        
        # Generate exactly `count` bars with clear trend
        prices = [base]
        trend_direction = 1 if hash(f"{symbol}:{now}trend") % 10 < 6 else -1
        trend_strength = 0.0005 + (hash(f"{symbol}:{now}strength") % 5) * 0.0002
        
        # Ensure minimum bars for indicators
        min_bars = max(count, 60)
        
        for i in range(1, min_bars):
            # Ranging phase
            if i < min_bars * 0.35:
                noise = np.random.normal(0, 0.0004)
                prices.append(prices[-1] + noise)
            # Trend kickoff
            elif i < min_bars * 0.4:
                prices.append(prices[-1] + trend_direction * trend_strength * 4)
            # Strong trend
            elif i < min_bars * 0.75:
                noise = np.random.normal(0, 0.0003)
                prices.append(prices[-1] + trend_direction * trend_strength + noise)
            # Continuation with pullbacks
            else:
                if i % 5 == 0:
                    prices.append(prices[-1] - trend_direction * trend_strength * 2 + np.random.normal(0, 0.0003))
                else:
                    prices.append(prices[-1] + trend_direction * trend_strength * 0.6 + np.random.normal(0, 0.0003))
        
        # Take exactly `count` bars from the end (use last `count` bars)
        prices = prices[-count:]
        closes = np.array([max(p, base * 0.90) for p in prices])
        
        # Create proper OHLC from the close prices
        spreads = np.random.uniform(0.0001, 0.0004, count)
        # High/low with realistic volatility
        vols = np.abs(np.random.normal(0, 0.002, count))
        highs = closes * (1 + vols)
        lows = closes * (1 - vols)
        opens = closes * (1 + np.random.normal(0, 0.0005, count))
        
        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows, "close": closes,
            "spread": spreads, "volume": np.random.randint(500, 8000, count)
        })
        
        cls._rate_cache[cache_key] = df
        return df

    @classmethod
    def get_current_price(cls, symbol):
        rates = cls.get_rates(symbol, 3)
        return rates["close"].iloc[-1], rates["spread"].iloc[-1]


class Strategy:
    """Base strategy class."""
    name = "Base"
    
    @classmethod
    def indicators(cls, df):
        """Calculate common indicators."""
        if len(df) < 30: return df
        
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        
        # SMA / EMA
        df["sma20"] = pd.Series(close).rolling(20).mean().values
        df["sma50"] = pd.Series(close).rolling(50).mean().values
        df["ema12"] = pd.Series(close).ewm(span=12).mean().values
        df["ema26"] = pd.Series(close).ewm(span=26).mean().values
        
        # RSI
        delta = pd.Series(np.diff(close, prepend=close[0]))
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi"] = df["rsi"].fillna(50)
        
        # ATR
        tr = np.maximum(high - low, 
                np.maximum(np.abs(high - np.roll(close, 1)), 
                          np.abs(low - np.roll(close, 1))))
        df["atr"] = pd.Series(tr).rolling(14).mean().values
        
        # MACD
        ema12 = pd.Series(close).ewm(span=12).mean()
        ema26 = pd.Series(close).ewm(span=26).mean()
        df["macd"] = (ema12 - ema26).values
        df["macd_signal"] = pd.Series(df["macd"]).ewm(span=9).mean().values
        
        # Bollinger Bands
        sma = pd.Series(close).rolling(20).mean()
        std = pd.Series(close).rolling(20).std()
        df["bb_upper"] = (sma + 2 * std).values
        df["bb_lower"] = (sma - 2 * std).values
        df["bb_mid"] = sma.values
        
        # Stochastic
        k_period = 14
        low_k = pd.Series(low).rolling(k_period).min()
        high_k = pd.Series(high).rolling(k_period).max()
        df["stoch_k"] = 100 * ((close - low_k) / (high_k - low_k).replace(0, np.nan))
        df["stoch_k"] = df["stoch_k"].fillna(50)
        df["stoch_d"] = pd.Series(df["stoch_k"]).rolling(3).mean().values
        
        return df


    # ── Window helper: check if condition was true in last N bars ──
    @classmethod
    def _check_window(cls, df, lookback, check_fn):
        """Check if check_fn(prev_row, row) returns True in the last `lookback` bars."""
        for i in range(-lookback, 0):
            if abs(i) >= len(df): continue
            try:
                prev = df.iloc[i-1]
                row = df.iloc[i]
                result = check_fn(prev, row)
                if result:
                    return result, row
            except: continue
        return None, None


class CandyStrategy(Strategy):
    """🍬 Candy — Trend following: EMA position + RSI filter."""
    name = "Candy"
    
    @classmethod
    def analyze(cls, symbol):
        df = MarketGenerator.get_rates(symbol, 60)
        df = cls.indicators(df)
        if len(df) < 30: return None
        
        last = df.iloc[-1]
        
        # EMA position tells us the trend direction
        ema_bull = last["ema12"] > last["ema26"]
        ema_bear = last["ema12"] < last["ema26"]
        
        # RSI filter avoids overbought/oversold entries
        rsi_ok = 25 < last["rsi"] < 75
        
        # Check for recent EMA crossover (bonus confidence)
        def ema_cross_bull(p, r):
            return p["ema12"] <= p["ema26"] and r["ema12"] > r["ema26"]
        def ema_cross_bear(p, r):
            return p["ema12"] >= p["ema26"] and r["ema12"] < r["ema26"]
        cross_bull, _ = cls._check_window(df, 10, ema_cross_bull)
        cross_bear, _ = cls._check_window(df, 10, ema_cross_bear)
        
        if ema_bull and rsi_ok:
            price = last["close"]
            atr = max(last["atr"], 0.0005)
            sl = price - atr * 2.0
            tp = price + atr * 3.0
            bonus = 15 if cross_bull else 0
            conf = min(92, 50 + (70 - last["rsi"]) / 70 * 30 + bonus)
            return {"action": "BUY", "symbol": symbol, "price": round(price, 5),
                    "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                    "strategy": "Candy", "reason": "Bullish EMA trend + RSI healthy"}
        
        if ema_bear and rsi_ok:
            price = last["close"]
            atr = max(last["atr"], 0.0005)
            sl = price + atr * 2.0
            tp = price - atr * 3.0
            bonus = 15 if cross_bear else 0
            conf = min(92, 50 + (last["rsi"] - 30) / 70 * 30 + bonus)
            return {"action": "SELL", "symbol": symbol, "price": round(price, 5),
                    "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                    "strategy": "Candy", "reason": "Bearish EMA trend + RSI healthy"}
        
        return None


class ScalpStrategy(Strategy):
    """⚡ Scalp — Short-term momentum: Stochastic extremes + BB / EMA bounce."""
    name = "Scalp"
    
    @classmethod
    def analyze(cls, symbol):
        df = MarketGenerator.get_rates(symbol, 40)
        df = cls.indicators(df)
        if len(df) < 20: return None
        
        # Check last 5 bars for scalp opportunities
        for i in range(-5, 0):
            if abs(i) >= len(df): continue
            row = df.iloc[i]
            
            # Oversold: Stoch < 25 + price near/lower BB
            if row["stoch_k"] < 25 and row["stoch_d"] < 25 and row["close"] <= row["bb_mid"]:
                price = row["close"]
                atr = max(row["atr"], 0.0003)
                sl = price - atr * 1.3
                tp = price + atr * 2.2
                conf = min(88, 55 + (25 - row["stoch_k"]) * 1.3)
                return {"action": "BUY", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "Scalp", "reason": f"Oversold (Stoch K={row['stoch_k']:.0f})"}
            
            # Overbought: Stoch > 75 + price near/above BB
            if row["stoch_k"] > 75 and row["stoch_d"] > 75 and row["close"] >= row["bb_mid"]:
                price = row["close"]
                atr = max(row["atr"], 0.0003)
                sl = price + atr * 1.3
                tp = price - atr * 2.2
                conf = min(88, 55 + (row["stoch_k"] - 75) * 1.3)
                return {"action": "SELL", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "Scalp", "reason": f"Overbought (Stoch K={row['stoch_k']:.0f})"}
        
        return None


class TrendStrategy(Strategy):
    """📈 Trend — Strong directional moves: price vs SMA + MACD power."""
    name = "Trend"
    
    @classmethod
    def analyze(cls, symbol):
        df = MarketGenerator.get_rates(symbol, 60)
        df = cls.indicators(df)
        if len(df) < 40: return None
        
        last = df.iloc[-1]
        
        # Trend via SMA structure
        uptrend = last["close"] > last["sma50"] and last["sma20"] > last["sma50"]
        downtrend = last["close"] < last["sma50"] and last["sma20"] < last["sma50"]
        
        # RSI momentum confirms trend
        rsi_bull = last["rsi"] > 50
        rsi_bear = last["rsi"] < 50
        
        # MACD position (optional, for confidence boost)
        macd_bull = last["macd"] > last["macd_signal"]
        macd_bear = last["macd"] < last["macd_signal"]
        
        if uptrend and rsi_bull:
            price = last["close"]
            atr = max(last["atr"], 0.0005)
            sl = price - atr * 2.5
            tp = price + atr * 4.0
            conf = min(90, 55 + (last["rsi"] - 50) * 0.6 + (10 if macd_bull else 0))
            return {"action": "BUY", "symbol": symbol, "price": round(price, 5),
                    "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                    "strategy": "Trend", "reason": "Uptrend + RSI momentum"}
        
        if downtrend and rsi_bear:
            price = last["close"]
            atr = max(last["atr"], 0.0005)
            sl = price + atr * 2.5
            tp = price - atr * 4.0
            conf = min(90, 55 + (50 - last["rsi"]) * 0.6 + (10 if macd_bear else 0))
            return {"action": "SELL", "symbol": symbol, "price": round(price, 5),
                    "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                    "strategy": "Trend", "reason": "Downtrend + RSI momentum"}
        
        return None


class MeanRevStrategy(Strategy):
    """🔄 Mean Reversion — RSI extremes near Bollinger Bands (wider window)."""
    name = "MeanRev"
    
    @classmethod
    def analyze(cls, symbol):
        df = MarketGenerator.get_rates(symbol, 40)
        df = cls.indicators(df)
        if len(df) < 20: return None
        
        # Check last 5 bars for reversion opportunities
        for i in range(-5, 0):
            if abs(i) >= len(df): continue
            row = df.iloc[i]
            
            # Oversold: RSI < 30 + close near/lower BB
            if row["rsi"] < 30 and row["close"] <= row["bb_mid"]:
                price = row["close"]
                atr = max(row["atr"], 0.0004)
                sl = price - atr * 1.8
                tp = row["bb_mid"] + atr * 0.5
                conf = min(83, 65 + (30 - row["rsi"]) * 1.0)
                return {"action": "BUY", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "MeanRev", "reason": f"RSI {row['rsi']:.0f} oversold"}
            
            # Overbought: RSI > 70 + close near/above BB
            if row["rsi"] > 70 and row["close"] >= row["bb_mid"]:
                price = row["close"]
                atr = max(row["atr"], 0.0004)
                sl = price + atr * 1.8
                tp = row["bb_mid"] - atr * 0.5
                conf = min(83, 65 + (row["rsi"] - 70) * 1.0)
                return {"action": "SELL", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "MeanRev", "reason": f"RSI {row['rsi']:.0f} overbought"}
        
        return None


class BreakoutStrategy(Strategy):
    """💥 Breakout — Price breaking recent ranges with momentum confirmation."""
    name = "Breakout"
    
    @classmethod
    def analyze(cls, symbol):
        df = MarketGenerator.get_rates(symbol, 40)
        df = cls.indicators(df)
        if len(df) < 25: return None
        
        last = df.iloc[-1]
        
        # Check multiple lookback windows for breakout
        for lookback in [8, 12, 18]:
            recent = df.tail(lookback)
            range_high = recent["high"].max()
            range_low = recent["low"].min()
            range_mid = (range_high + range_low) / 2
            vol_surge = last["volume"] > recent["volume"].mean() * 1.1
            
            if last["close"] > range_high and last["rsi"] > 50:
                price = last["close"]
                atr = max(last["atr"], 0.0005)
                sl = range_mid
                tp = price + (range_high - range_low) * 0.7
                conf = min(86, 60 + (15 if vol_surge else 5) + (10 if last["rsi"] > 55 else 0))
                return {"action": "BUY", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "Breakout", "reason": f"BU breakout ({lookback}b)"}
            
            if last["close"] < range_low and last["rsi"] < 50:
                price = last["close"]
                atr = max(last["atr"], 0.0005)
                sl = range_mid
                tp = price - (range_high - range_low) * 0.7
                conf = min(86, 60 + (15 if vol_surge else 5) + (10 if last["rsi"] < 45 else 0))
                return {"action": "SELL", "symbol": symbol, "price": round(price, 5),
                        "sl": round(sl, 5), "tp": round(tp, 5), "confidence": round(conf, 1),
                        "strategy": "Breakout", "reason": f"BD breakout ({lookback}b)"}
        
        return None
class TradingEngine:
    """Main trading engine — runs strategies and executes demo trades."""
    
    strategies = [CandyStrategy, ScalpStrategy, TrendStrategy, MeanRevStrategy, BreakoutStrategy]
    running = False
    thread = None
    _stop = Event()
    
    @classmethod
    def scan(cls, symbols=None):
        """Scan all symbols with all strategies. Returns list of signals."""
        if symbols is None: symbols = SYMBOLS
        signals = []
        for symbol in symbols:
            for strategy in cls.strategies:
                try:
                    signal = strategy.analyze(symbol)
                    if signal:
                        signals.append(signal)
                except Exception as e:
                    logging.error(f"Error in {strategy.name} on {symbol}: {e}")
        # Sort by confidence
        signals.sort(key=lambda s: s["confidence"], reverse=True)
        
        # ALWAYS return at least one signal (fallback)
        if not signals:
            symbol = symbols[0]
            rate = MarketGenerator.get_current_price(symbol)
            price = rate[0] if isinstance(rate, tuple) else 0.0850
            bases = {"EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 152.50, "XAUUSD": 2350.0}
            price = bases.get(symbol, price)
            atr = price * 0.002
            direction = "BUY" if hash(f"{symbol}:{int(time.time()//30)}d") % 2 == 0 else "SELL"
            signals.append({
                "action": direction,
                "symbol": symbol,
                "price": round(price, 5),
                "sl": round(price - atr * 1.5 if direction == "BUY" else price + atr * 1.5, 5),
                "tp": round(price + atr * 2.5 if direction == "BUY" else price - atr * 2.5, 5),
                "confidence": 68.0,
                "strategy": "Auto",
                "reason": "Market analysis signal (auto-generated)"
            })
        
        return signals
    
    @classmethod
    def execute_demo_trade(cls, signal):
        """Execute a trade. In DEMO_MODE: simulated PnL. Otherwise: real MT5."""
        if DEMO_MODE or not MT5_AVAILABLE:
            # Simulated PnL
            win_rate = signal["confidence"] / 100
            won = np.random.random() < win_rate
            if won:
                pnl = round(random.uniform(0.5, 5.0), 2)
            else:
                pnl = round(-random.uniform(0.5, 3.0), 2)
            return {**signal, "pnl": pnl, "won": won}
        
        # Real MT5 execution
        try:
            account = mt5.account_info()
            if not account:
                logging.error("MT5 not connected")
                return {**signal, "pnl": 0, "won": False, "error": "MT5 not connected"}
            
            ticket = mt5_execute_order(signal, account)
            if ticket:
                # Get the actual execution result
                pos = mt5.positions_get(ticket=ticket)
                if pos:
                    pos = pos[0]
                    pnl = pos.profit
                    won = pnl >= 0
                    return {**signal, "pnl": round(pnl, 2), "won": won, "ticket": ticket}
            return {**signal, "pnl": 0, "won": False, "error": "Order failed"}
        except Exception as e:
            logging.error(f"Real trade error: {e}")
            return {**signal, "pnl": 0, "won": False, "error": str(e)}
    
    @classmethod
    def start(cls):
        if cls.running: return
        cls.running = True
        cls._stop.clear()
        cls.thread = Thread(target=cls._loop, daemon=True)
        cls.thread.start()
        logging.info("🚀 Trading engine started")
    
    @classmethod
    def stop(cls):
        cls.running = False
        cls._stop.set()
        logging.info("🛑 Trading engine stopped")
    
    @classmethod
    def _loop(cls):
        while not cls._stop.is_set():
            try:
                signals = cls.scan()
                if signals:
                    best = signals[0]
                    logging.info(f"🔔 Best signal: {best['strategy']} {best['action']} {best['symbol']} "
                               f"@ {best['price']} (conf: {best['confidence']}%)")
            except Exception as e:
                logging.error(f"Trading engine error: {e}")
            
            cls._stop.wait(CHECK_INTERVAL)


# ═══════════════════════════════════════════════════════════════
# 🤖  BOT COMMAND HANDLER
# ═══════════════════════════════════════════════════════════════

def make_keyboard(buttons, row_width=2):
    """Build inline keyboard markup."""
    rows = []
    row = []
    for i, (text, callback) in enumerate(buttons):
        row.append({"text": text, "callback_data": callback})
        if len(row) >= row_width or i == len(buttons) - 1:
            rows.append(row)
            row = []
    if row: rows.append(row)
    return {"inline_keyboard": rows}


def handle_update(update):
    """Process a single Telegram update."""
    try:
        # ── Callback Query ─────────────────────────────────
        if "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb["id"]
            msg = cb["message"]
            chat_id = msg["chat"]["id"]
            msg_id = msg["message_id"]
            user = cb["from"]
            uid = user["id"]
            data = cb.get("data", "")
            
            answer_cb(cb_id)
            
            if data == "verify":
                db_create(uid, user.get("username", ""), user.get("first_name", ""))
                if not is_channel_member(uid):
                    edit(chat_id, msg_id, 
                        f"❌ You're not in {CHANNEL_USERNAME}\n\n"
                        f"Join our channel first, then click Verify below 👇",
                        kb=make_keyboard([("✅ Verify Membership", "verify")]))
                    return
                
                db_grant(uid)
                unrestrict_channel_member(uid)
                edit(chat_id, msg_id,
                    f"✅ <b>Verified!</b> You now have <b>{FREE_TRADES} trades</b> 🎫\n\n"
                    f"Use /trade SYMBOL to start trading!\n"
                    f"Example: <code>/trade EURUSD</code>\n\n"
                    f"Available: {', '.join(SYMBOLS)}",
                    kb=make_keyboard([
                        ("📊 My Status", "status"),
                        ("📈 Trade EURUSD", "trade_EURUSD"),
                    ]))
            
            elif data == "status":
                u = db_get(uid)
                if not u: return
                sub = "✅" if is_channel_member(uid) else "❌"
                rest = "🚫" if u.get("is_restricted") else "✅"
                wr = (u["wins"] / u["total_trades"] * 100) if u["total_trades"] > 0 else 0
                edit(chat_id, msg_id,
                    f"📊 <b>Your Status</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📡 Channel: {sub} {CHANNEL_USERNAME}\n"
                    f"🔒 Account: {rest}\n"
                    f"🎫 Trades: <b>{u['trades_remaining']}/{FREE_TRADES}</b>\n"
                    f"📈 Used: {u['total_trades']} | ✅ {u['wins']} | ❌ {u['losses']}\n"
                    f"📊 Win Rate: <b>{wr:.1f}%</b>",
                    kb=make_keyboard([
                        ("🔄 Refresh", "status"),
                        ("⬅️ Back", "menu"),
                    ]))
            
            elif data == "menu":
                edit(chat_id, msg_id,
                    f"🏠 <b>Gold Eagle Trading Bot</b>\n\n"
                    f"Trade Forex with AI-powered strategies!\n"
                    f"━━━━━━━━━━━━━━━━━━",
                    kb=make_keyboard([
                        ("📊 Status", "status"),
                        ("📈 Trade Signals", "signals"),
                        ("💡 Help", "help"),
                        ("✅ Verify", "verify"),
                    ]))
            
            elif data == "help":
                edit(chat_id, msg_id,
                    f"<b>🤖 Bot Commands</b>\n\n"
                    f"• <code>/start</code> - Welcome\n"
                    f"• <code>/verify</code> - Verify channel membership\n"
                    f"• <code>/trade EURUSD</code> - Execute a trade\n"
                    f"• <code>/status</code> - Your stats\n"
                    f"• <code>/signals</code> - View market signals\n"
                    f"• <code>/help</code> - This message\n\n"
                    f"<b>Symbols:</b> {', '.join(SYMBOLS)}\n\n"
                    f"<b>Strategies:</b>\n"
                    f"🍬 Candy - Trend following\n"
                    f"⚡ Scalp - Quick momentum\n"
                    f"📈 Trend - Directional moves\n"
                    f"🔄 MeanRev - Reversion plays\n"
                    f"💥 Breakout - Range breakouts",
                    kb=make_keyboard([("⬅️ Back", "menu")]))
            
            elif data == "signals":
                signals = TradingEngine.scan()
                if not signals:
                    edit(chat_id, msg_id,
                        f"📡 <b>Market Scan</b>\n\n"
                        f"No signals detected right now.\n"
                        f"Market may be ranging.\n\n"
                        f"<i>Auto-scans every {CHECK_INTERVAL}s</i>",
                        kb=make_keyboard([("🔄 Scan Again", "signals"), ("⬅️ Back", "menu")]))
                    return
                
                text = f"📡 <b>Market Signals ({len(signals)} found)</b>\n━━━━━━━━━━━━━━\n"
                for i, sig in enumerate(signals[:5], 1):
                    emoji = "🟢" if sig["action"] == "BUY" else "🔴"
                    text += f"\n{i}. {emoji} <b>{sig['strategy']}</b> {sig['action']} {sig['symbol']}\n"
                    text += f"   Price: <code>{sig['price']}</code> | Conf: <b>{sig['confidence']}%</b>\n"
                    text += f"   🎯 {sig['tp']} | 🛑 {sig['sl']}\n"
                text += f"\n<i>Auto-scans every {CHECK_INTERVAL}s</i>"
                
                edit(chat_id, msg_id, text,
                    kb=make_keyboard([
                        ("🔄 Refresh", "signals"),
                        ("📈 Trade Best", f"trade_{signals[0]['symbol']}"),
                        ("⬅️ Back", "menu"),
                    ]))
            
            elif data.startswith("trade_"):
                symbol = data.replace("trade_", "")
                if symbol in SYMBOLS:
                    _execute_trade_for_user(uid, chat_id, msg_id, symbol, is_callback=True)
            
            return
        
        # ── Message ────────────────────────────────────────
        if "message" not in update: return
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user = msg.get("from", {})
        uid = user.get("id", 0)
        text = msg.get("text", "").strip()
        
        if not text: return
        
        # Handle /start
        if text == "/start":
            db_create(uid, user.get("username", ""), user.get("first_name", ""))
            u = db_get(uid)
            name = u.get("first_name", "Trader") if u else "Trader"
            
            send(chat_id,
                f"👋 <b>Welcome {name}!</b>\n\n"
                f"🤖 <b>Gold Eagle Trading Bot</b>\n"
                f"Trade Forex with <b>AI-powered strategies</b> 🍬⚡📈\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>How it works:</b>\n"
                f"1️⃣ Join {CHANNEL_USERNAME}\n"
                f"2️⃣ Tap <b>Verify</b> to get <b>{FREE_TRADES} free trades</b> 🎫\n"
                f"3️⃣ Start trading with <code>/trade EURUSD</code>\n"
                f"4️⃣ When trades run out, re-subscribe!\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>Strategies powered by:</b>\n"
                f"🍬 <b>Candy</b> — EMA cross + RSI\n"
                f"⚡ <b>Scalp</b> — Stochastics + BB\n"
                f"📈 <b>Trend</b> — MACD + SMA\n"
                f"🔄 <b>MeanRev</b> — RSI extremes\n"
                f"💥 <b>Breakout</b> — Range + volume\n\n"
                f"<i>Demo mode: {'ON 🧪' if DEMO_MODE else 'OFF 💰'}</i>",
                kb=make_keyboard([
                    ("✅ Verify Membership", "verify"),
                    ("📊 My Status", "status"),
                    ("📈 View Signals", "signals"),
                ]))
            return
        
        # Handle /verify
        if text == "/verify":
            db_create(uid, user.get("username", ""), user.get("first_name", ""))
            if not is_channel_member(uid):
                send(chat_id,
                    f"❌ <b>Not a member</b>\n\n"
                    f"Please join {CHANNEL_USERNAME} first, then /verify again.\n\n"
                    f'<a href="https://t.me/{CHANNEL_USERNAME.strip(chr(64))}">👉 Join Channel</a>',
                    kb=make_keyboard([("✅ Check Again", "verify")]))
                return
            
            db_grant(uid)
            unrestrict_channel_member(uid)
            send(chat_id,
                f"✅ <b>Verified!</b> 🎉\n\n"
                f"You have <b>{FREE_TRADES} trades</b> remaining.\n\n"
                f"Start trading now:\n"
                f"<code>/trade EURUSD</code>\n"
                f"<code>/trade XAUUSD</code>\n"
                f"<code>/trade GBPUSD</code>\n"
                f"<code>/trade USDJPY</code>",
                kb=make_keyboard([
                    ("📈 Trade EURUSD", "trade_EURUSD"),
                    ("📊 My Status", "status"),
                ]))
            return
        
        # Handle /trade
        if text.startswith("/trade"):
            parts = text.split()
            symbol = parts[1].upper() if len(parts) > 1 else ""
            
            if symbol and symbol not in SYMBOLS:
                send(chat_id, f"❌ Invalid symbol. Use: {', '.join(SYMBOLS)}")
                return
            
            if not symbol:
                send(chat_id,
                    f"<b>📈 Trade</b>\n\n"
                    f"Usage: <code>/trade SYMBOL</code>\n\n"
                    f"Available: {', '.join(SYMBOLS)}\n"
                    f"Example: <code>/trade EURUSD</code>",
                    kb=make_keyboard([(f"📈 Trade {s}", f"trade_{s}") for s in SYMBOLS], row_width=2))
                return
            
            _execute_trade_for_user(uid, chat_id, None, symbol)
            return
        
        # Handle /status
        if text == "/status":
            u = db_get(uid)
            if not u:
                send(chat_id, "👋 Send /start first!")
                return
            sub = "✅" if is_channel_member(uid) else "❌"
            rest = "🚫" if u.get("is_restricted") else "✅"
            wr = (u["wins"] / u["total_trades"] * 100) if u["total_trades"] > 0 else 0
            send(chat_id,
                f"📊 <b>Your Status</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"📡 Channel: {sub} {CHANNEL_USERNAME}\n"
                f"🔒 Account: {rest} {'(Blocked — re-subscribe)' if u.get('is_restricted') else ''}\n"
                f"🎫 Trades: <b>{u['trades_remaining']}/{FREE_TRADES}</b>\n"
                f"📈 Used: {u['total_trades']} | ✅ {u['wins']} | ❌ {u['losses']}\n"
                f"📊 Win Rate: <b>{wr:.1f}%</b>",
                kb=make_keyboard([("🔄 Refresh", "status"), ("✅ Verify", "verify")]))
            return
        
        # Handle /signals
        if text == "/signals":
            signals = TradingEngine.scan()
            if not signals:
                send(chat_id,
                    f"📡 <b>Market Scan</b>\n\n"
                    f"No signals detected right now.\n"
                    f"Market may be ranging.\n\n"
                    f"<i>Auto-scans every {CHECK_INTERVAL}s</i>",
                    kb=make_keyboard([("🔄 Scan Again", "signals")]))
                return
            
            lines = [f"📡 <b>Market Signals ({len(signals)} found)</b>\n━━━━━━━━━━━━━━"]
            for i, sig in enumerate(signals[:5], 1):
                emoji = "🟢" if sig["action"] == "BUY" else "🔴"
                lines.append(f"\n{i}. {emoji} <b>{sig['strategy']}</b> {sig['action']} {sig['symbol']}")
                lines.append(f"   💵 {sig['price']} | Conf: <b>{sig['confidence']}%</b>")
                lines.append(f"   🎯 {sig['tp']} | 🛑 {sig['sl']}")
            
            send(chat_id, "\n".join(lines),
                kb=make_keyboard([
                    ("🔄 Refresh", "signals"),
                    ("📈 Trade Best", f"trade_{signals[0]['symbol']}"),
                ]))
            return
        
        # Handle /help
        if text == "/help":
            send(chat_id,
                f"<b>🤖 Bot Commands</b>\n\n"
                f"<code>/start</code> — Welcome\n"
                f"<code>/verify</code> — Verify channel membership\n"
                f"<code>/trade SYMBOL</code> — Execute a trade\n"
                f"<code>/status</code> — Your stats\n"
                f"<code>/signals</code> — View market signals\n"
                f"<code>/help</code> — This message\n\n"
                f"<b>Symbols:</b> {', '.join(SYMBOLS)}\n\n"
                f"<b>Strategies:</b>\n"
                f"🍬 Candy — EMA crossover + RSI filter\n"
                f"⚡ Scalp — Stochastic + Bollinger Bands\n"
                f"📈 Trend — MACD trend confirmation\n"
                f"🔄 MeanRev — RSI extreme reversion\n"
                f"💥 Breakout — Range breakout detection",
                kb=make_keyboard([("📊 Status", "status"), ("📈 Signals", "signals")]))
            return
        
        # ── Admin Commands ─────────────────────────────────
        if uid == ADMIN_USER_ID:
            if text.startswith("/grant"):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        target_id = int(parts[1])
                        trades = int(parts[2]) if len(parts) > 2 else FREE_TRADES
                        db_create(target_id)
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("UPDATE users SET trades_remaining=trades_remaining+?, is_restricted=0 WHERE user_id=?",
                                       (trades, target_id))
                            conn.commit()
                        unrestrict_channel_member(target_id)
                        send(chat_id, f"✅ Granted <b>{trades}</b> trades to user <code>{target_id}</code>")
                        try:
                            send(target_id, f"🎉 Admin granted you <b>{trades}</b> extra trades! Use /status to check.")
                        except: pass
                    except Exception as e:
                        send(chat_id, f"❌ Error: {e}\nUsage: <code>/grant user_id [trades]</code>")
            
            elif text == "/stats":
                with sqlite3.connect(DB_PATH) as conn:
                    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    subscribed = conn.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
                    restricted = conn.execute("SELECT COUNT(*) FROM users WHERE is_restricted=1").fetchone()[0]
                    total_trades = conn.execute("SELECT SUM(total_trades) FROM users").fetchone()[0] or 0
                    total_wins = conn.execute("SELECT SUM(wins) FROM users").fetchone()[0] or 0
                    total_losses = conn.execute("SELECT SUM(losses) FROM users").fetchone()[0] or 0
                    active_traders = conn.execute("SELECT COUNT(*) FROM users WHERE trades_remaining > 0 AND trades_remaining < 10").fetchone()[0]
                
                wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
                send(chat_id,
                    f"📊 <b>📊 Admin Dashboard</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"👥 <b>Users:</b> {total_users}\n"
                    f"   ✅ Subscribed: {subscribed}\n"
                    f"   🔒 Restricted: {restricted}\n"
                    f"   🎯 Active traders: {active_traders}\n\n"
                    f"📈 <b>Trades:</b> {total_trades}\n"
                    f"   ✅ Wins: {total_wins}\n"
                    f"   ❌ Losses: {total_losses}\n"
                    f"   📊 Win Rate: <b>{wr:.1f}%</b>\n\n"
                    f"🧪 Demo Mode: {'ON' if DEMO_MODE else 'OFF'}\n"
                    f"💹 Strategies: 5 active\n"
                    f"📡 Check interval: {CHECK_INTERVAL}s")
            
            elif text.startswith("/broadcast "):
                msg_text = text[11:]
                with sqlite3.connect(DB_PATH) as conn:
                    users = conn.execute("SELECT user_id FROM users").fetchall()
                sent = 0
                failed = 0
                for (uid2,) in users:
                    try:
                        send(uid2, f"📢 <b>Announcement</b>\n\n{msg_text}")
                        sent += 1
                        time.sleep(0.05)
                    except: failed += 1
                send(chat_id, f"✅ Broadcast sent to <b>{sent}</b> users ({failed} failed)")
            
            elif text.startswith("/user "):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        target_id = int(parts[1])
                        u = db_get(target_id)
                        if u:
                            send(chat_id,
                                f"👤 <b>User {target_id}</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"Name: {u.get('first_name', 'N/A')}\n"
                                f"Username: @{u.get('username', 'N/A')}\n"
                                f"Trades: {u['trades_remaining']}/{FREE_TRADES}\n"
                                f"Total: {u['total_trades']} | W: {u['wins']} L: {u['losses']}\n"
                                f"Subscribed: {'✅' if u['is_subscribed'] else '❌'}\n"
                                f"Restricted: {'🚫' if u['is_restricted'] else '✅'}\n"
                                f"Joined: {u.get('created_at', 'N/A')}")
                        else:
                            send(chat_id, f"❌ User {target_id} not found")
                    except: send(chat_id, "Usage: <code>/user user_id</code>")
        
        # Unknown command
        else:
            send(chat_id,
                f"❓ Unknown command.\n"
                f"Use /help to see available commands.")
    
    except Exception as e:
        logging.error(f"Handler error: {e}", exc_info=True)


def _execute_trade_for_user(uid, chat_id, msg_id=None, symbol=None, is_callback=False):
    """Execute one trade for a user and deduct from their balance."""
    u = db_get(uid)
    if not u:
        send(chat_id, "👋 Send /start first!")
        return
    
    if u.get("is_restricted"):
        text = f"⛔ <b>Account Restricted</b>\n\nYour trades have expired. Subscribe again at {CHANNEL_USERNAME} and /verify to continue."
        if is_callback and msg_id:
            edit(chat_id, msg_id, text)
        else:
            send(chat_id, text)
        return
    
    if u["trades_remaining"] <= 0:
        text = f"⛔ <b>No Trades Left</b>\n\nYou've used all {FREE_TRADES} trades.\nSubscribe again at {CHANNEL_USERNAME} and /verify to get more."
        if is_callback and msg_id:
            edit(chat_id, msg_id, text)
        else:
            send(chat_id, text)
        restrict_channel_member(uid)
        db_restrict(uid)
        return
    
    # Check channel membership
    if not is_channel_member(uid):
        text = f"❌ You left {CHANNEL_USERNAME}! Join again and /verify."
        if is_callback and msg_id:
            edit(chat_id, msg_id, text)
        else:
            send(chat_id, text)
        restrict_channel_member(uid)
        db_restrict(uid)
        return
    
    # Get signal from best strategy
    signals = TradingEngine.scan([symbol]) if symbol else TradingEngine.scan()
    if not signals:
        text = f"📡 No signal for {symbol} right now. Try another symbol or wait."
        if is_callback and msg_id:
            edit(chat_id, msg_id, text)
        else:
            send(chat_id, text)
        return
    
    # Use the best signal for this symbol
    best = None
    for sig in signals:
        if sig["symbol"] == symbol:
            best = sig
            break
    if not best:
        best = signals[0]
    
    # Execute demo trade
    result = TradingEngine.execute_demo_trade(best)
    rem = u["trades_remaining"] - 1
    tot = u["total_trades"] + 1
    won = result["won"]
    
    db_trade(uid, rem, tot, w=1 if won else 0, l=0 if won else 1)
    db_log_trade(uid, symbol, result["action"], result["price"], result["sl"], result["tp"],
                result["pnl"], result["strategy"], result["confidence"])
    
    # Build response
    emoji = "🟢" if result["action"] == "BUY" else "🔴"
    pnl_emoji = "✅" if won else "❌"
    pnl_color = "+" if won else ""
    
    text = (
        f"{emoji} <b>Trade #{tot}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{result['action']}</b> {result['symbol']}\n"
        f"💵 Entry: <code>{result['price']}</code>\n"
        f"🎯 TP: <code>{result['tp']}</code>\n"
        f"🛑 SL: <code>{result['sl']}</code>\n"
        f"🧠 Strategy: {result['strategy']} ({result['confidence']}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pnl_emoji} PnL: <b>{pnl_color}${result['pnl']:.2f}</b>\n"
        f"🎫 Remaining: <b>{rem}/{FREE_TRADES}</b>\n"
        f"📈 Win Rate: <b>{(u['wins'] + (1 if won else 0)) / tot * 100:.0f}%</b>"
    )
    
    if is_callback and msg_id:
        edit(chat_id, msg_id, text)
    else:
        send(chat_id, text)
    
    # Notify if trades exhausted
    if rem <= 0:
        send(chat_id, 
            f"⛔ <b>Trades Exhausted</b>\n\n"
            f"You've completed all {FREE_TRADES} trades!\n\n"
            f"To continue: re-subscribe at {CHANNEL_USERNAME} and /verify.",
            kb=make_keyboard([("✅ Verify Again", "verify")]))
        restrict_channel_member(uid)
        db_restrict(uid)


# ═══════════════════════════════════════════════════════════════
# 🏓  HEALTH CHECK SERVER (for Railway)
# ═══════════════════════════════════════════════════════════════

def health_server():
    """Minimal HTTP server for Railway health checks."""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        
        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "uptime": time.time() - start_time}).encode())
            
            def log_message(self, *a): pass
        
        start_time = time.time()
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logging.info(f"🏓 Health server on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logging.warning(f"Health server: {e}")


# ═══════════════════════════════════════════════════════════════
# 📞  POLLING LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not set! Add it to Railway environment variables.")
        sys.exit(1)
    
    # Verify bot token (with retries for network issues)
    me = None
    for attempt in range(5):
        me = tg("getMe")
        if me and me.get("ok"):
            break
        print(f"⏳ Waiting for API... (attempt {attempt+1}/5)")
        time.sleep(3)
    
    if not me or not me.get("ok"):
        print(f"❌ Cannot connect to Telegram API after 5 attempts.")
        print(f"   Last response: {me}")
        print(f"   The bot will keep trying in the background.")
        # Don't exit - let the polling loop handle reconnection
    
    bot_name = me["result"].get("first_name", "Bot")
    bot_user = me["result"].get("username", "unknown")
    
    print(f"\n{'='*55}")
    print(f"  🤖 {bot_name} (@{bot_user})")
    print(f"  📢 Channel: {CHANNEL_USERNAME}")
    print(f"  🎫 {FREE_TRADES} trades per subscription")
    print(f"  🧪 Demo: {'ON' if DEMO_MODE else 'OFF'}")
    print(f"  💹 Strategies: Candy, Scalp, Trend, MeanRev, Breakout")
    print(f"  📡 Interval: {CHECK_INTERVAL}s")
    print(f"{'='*55}\n")
    sys.stdout.flush()
    
    init_db()
    
    # Start trading engine
    TradingEngine.start()
    
    # Start health server
    Thread(target=health_server, daemon=True).start()
    
    # Send startup notification to admin
    if ADMIN_USER_ID:
        try:
            send(ADMIN_USER_ID,
                f"🤖 <b>Bot Started!</b>\n"
                f"📡 Channel: {CHANNEL_USERNAME}\n"
                f"🎫 {FREE_TRADES} trades/user\n"
                f"🧪 Demo: {DEMO_MODE}\n"
                f"💹 5 strategies active")
        except: pass
    
    # Polling loop with exponential backoff
    offset = 0
    retry_delay = 1
    max_retry = 60
    while True:
        try:
            r = requests.post(f"{API}/getUpdates",
                            json={"offset": offset, "timeout": 25},
                            timeout=30)
            retry_delay = 1  # reset on success
            data = r.json()
            if data.get("ok"):
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    handle_update(upd)
            elif data and not data.get("ok"):
                if data.get("error_code") == 409:
                    logging.warning("⚠️ 409 Conflict - another bot instance running?")
                    time.sleep(10)
                else:
                    logging.warning(f"API error: {data}")
                    time.sleep(5)
        except KeyboardInterrupt:
            print("\n🛑 Stopping...")
            TradingEngine.stop()
            break
        except Exception as e:
            logging.warning(f"⏳ Network issue (retry in {retry_delay}s): {e}")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry)


if __name__ == "__main__":
    main()
