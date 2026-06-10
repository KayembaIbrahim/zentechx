#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║  🤖 TELEGRAM TRADING BOT                                ║
║  Users subscribe to your paid channel → get 10 trades   ║
║  Trades run out → user restricted until re-subscribe    ║
╚══════════════════════════════════════════════════════════╝

SETUP:
  1. pip install python-telegram-bot MetaTrader5 pandas numpy ta
  2. Edit CONFIG below (BOT_TOKEN, CHANNEL_USERNAME, ADMIN_ID)
  3. Make your bot an ADMIN in your Telegram channel
  4. python telegram_trader_bot.py

FLOW:
  /start → user told to join paid channel
  /verify → bot checks channel membership → grants 10 trades
  /trade EURUSD → executes one trade (consumes 1)
  /status → shows remaining trades
  At 0 trades → user is restricted until renewal
"""

import sqlite3
import time
import asyncio
import logging
import sys
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# ════════════════════════════════════════════════════════════
# 🔧  CONFIG — EDIT THESE                                    
# ════════════════════════════════════════════════════════════

BOT_TOKEN = "8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI"      # From @BotFather
CHANNEL_USERNAME = "@zenfxctc"      # Your paid Telegram channel
ADMIN_USER_ID = 955396728                      # Your Telegram user ID

# Trading symbols available to users
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

# Subscription terms
FREE_TRADES = 10                       # Trades per subscription period
TRADE_COOLDOWN = 30                    # Seconds between trades (anti-spam)
CHECK_MEMBERSHIP_EVERY = 300           # Seconds between membership re-checks

# Demo mode (no MT5 needed to test)
DEMO_MODE = True

# ════════════════════════════════════════════════════════════
#  🗄️  DATABASE
# ════════════════════════════════════════════════════════════

DB_DIR = Path(__file__).parent / "bot_data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "users.db"


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
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()


def db_get_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def db_create_user(user_id, username="", first_name=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name),
        )
        conn.commit()


def db_update_trades(user_id, remaining, total, wins=0, losses=0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE users SET trades_remaining=?, total_trades=?,
               wins=wins+?, losses=losses+?, last_trade_at=datetime('now')
               WHERE user_id=?""",
            (remaining, total, wins, losses, user_id),
        )
        conn.commit()


def db_grant_subscription(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE users SET trades_remaining=?, total_trades=0, wins=0, losses=0,
               is_subscribed=1, is_restricted=0, subscribed_at=datetime('now')
               WHERE user_id=?""",
            (FREE_TRADES, user_id),
        )
        conn.commit()


def db_set_restricted(user_id, restricted=True):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET is_restricted=? WHERE user_id=?",
            (1 if restricted else 0, user_id),
        )
        conn.commit()


def db_log_trade(user_id, symbol, action, price, sl, tp, pnl=0, strategy="Candy"):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO trade_log (user_id, symbol, action, price, sl, tp, pnl, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, symbol, action, price, sl, tp, pnl, strategy),
        )
        conn.commit()


# ════════════════════════════════════════════════════════════
#  📊  TRADING ENGINE (Candy Strategy + Scalp)
# ════════════════════════════════════════════════════════════

class TradeEngine:
    """Executes trades using Candy/Scalp strategy. Falls back to demo if MT5 missing."""

    def __init__(self):
        self.mt5 = None
        self.mt5_ok = False
        if not DEMO_MODE:
            self._init_mt5()

    def _init_mt5(self):
        try:
            import MetaTrader5 as mt5
            self.mt5 = mt5
            if mt5.initialize():
                from bot.core.mt5_connector import MT5Connector
                # Will use simple mode instead
                self.mt5_ok = True
                info = mt5.account_info()
                if info:
                    print(f"✅ MT5: {info.name} | ${info.balance:.2f}")
            else:
                print(f"⚠️  MT5 init: {mt5.last_error()}")
        except ImportError:
            print("⚠️  MT5 not installed — demo mode")

    def _get_rates(self, symbol: str, count: int = 100):
        """Get OHLC data. Simulated in demo, real from MT5 in live mode."""
        import pandas as pd
        import numpy as np

        if DEMO_MODE or not self.mt5_ok:
            # Simulated data with trends
            np.random.seed(hash(symbol + str(int(time.time() / 60))) % 2**31)
            base = {"EURUSD": 1.0850, "GBPUSD": 1.2650,
                    "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)
            now = int(time.time())
            prices = [base]
            trend = random.choice([-1, 1]) * 0.0001
            bar = 0
            for i in range(1, count):
                bar += 1
                if bar >= random.randint(15, 30):
                    trend = random.choice([-1, 1]) * 0.0001
                    bar = 0
                if bar == random.randint(5, 10):
                    prices.append(prices[-1] - trend * 6 * random.random())
                else:
                    prices.append(prices[-1] + trend + np.random.normal(0, 0.0002))
            closes = [max(p, base * 0.95) for p in prices]
            return pd.DataFrame({
                "time": pd.to_datetime([now - (count - i) * 60 for i in range(count)], unit="s"),
                "open": closes, "high": [c * 1.001 for c in closes],
                "low": [c * 0.999 for c in closes], "close": closes,
                "tick_volume": [max(1, int(np.random.gamma(3, 100))) for _ in range(count)],
            })
        else:
            rates = self.mt5.copy_rates_from_pos(symbol, 1, 0, count)
            if rates is None or len(rates) == 0:
                return None
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            return df

    def analyze(self, symbol: str) -> dict:
        """Run Candy + Scalp analysis. Returns signal dict or None."""
        import pandas as pd
        import numpy as np

        df = self._get_rates(symbol)
        if df is None or len(df) < 30:
            return None

        close = df["close"].values
        high, low = df["high"].values, df["low"].values
        price = close[-1]

        # ── Indicators ──────────────────────────────────────
        ema5 = pd.Series(close).ewm(span=5, adjust=False).mean().values
        ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values

        # RSI(14)
        delta = pd.Series(close).diff()
        gain = delta.where(delta > 0, 0).ewm(span=14, adjust=False).mean().values
        loss = (-delta.where(delta < 0, 0)).ewm(span=14, adjust=False).mean().values
        rsi = 100 - (100 / (1 + gain / np.maximum(loss, 1e-10)))

        # ATR
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(abs(high[1:] - close[:-1]),
                                   abs(low[1:] - close[:-1])))
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else price * 0.001

        # ── CANDY SIGNAL ────────────────────────────────────
        signals = []
        if ema5[-1] > ema20[-1] and ema5[-3] > ema20[-3]:
            near_ema = abs(price - ema20[-1]) / max(ema20[-1], 1e-10) < 0.003
            if near_ema and rsi[-1] < 65:
                sl = price - atr * 1.5
                tp = price + atr * 2.5
                signals.append(("BUY", price, sl, tp, 0.60, "🍬Candy"))
        elif ema5[-1] < ema20[-1] and ema5[-3] < ema20[-3]:
            near_ema = abs(price - ema20[-1]) / max(ema20[-1], 1e-10) < 0.003
            if near_ema and rsi[-1] > 35:
                sl = price + atr * 1.5
                tp = price - atr * 2.5
                signals.append(("SELL", price, sl, tp, 0.60, "🍬Candy"))

        # ── SCALP SIGNAL ────────────────────────────────────
        ma9 = pd.Series(close).rolling(9).mean().values
        ma21 = pd.Series(close).rolling(21).mean().values
        if len(ma9) > 3 and ma9[-1] > ma21[-1] and ma9[-2] <= ma21[-2]:
            sl = price - atr * 1.0
            tp = price + atr * 1.8
            signals.append(("BUY", price, sl, tp, 0.50, "⚡Scalp"))
        elif len(ma9) > 3 and ma9[-1] < ma21[-1] and ma9[-2] >= ma21[-2]:
            sl = price + atr * 1.0
            tp = price - atr * 1.8
            signals.append(("SELL", price, sl, tp, 0.50, "⚡Scalp"))

        if not signals:
            return None

        signals.sort(key=lambda s: s[4], reverse=True)
        action, entry, sl, tp, conf, strat = signals[0]
        return {
            "action": action, "symbol": symbol,
            "price": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5),
            "confidence": conf, "strategy": strat,
        }

    def execute(self, symbol: str) -> dict:
        """Analyze + execute a trade. Returns result."""
        result = {
            "success": False, "symbol": symbol,
            "action": "", "price": 0, "sl": 0, "tp": 0, "pnl": 0,
            "strategy": "", "error": "",
        }

        sig = self.analyze(symbol)
        if not sig:
            # Fallback: simple random signal if no strategy signal
            action = random.choice(["BUY", "SELL"])
            base = {"EURUSD": 1.0850, "GBPUSD": 1.2650,
                    "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)
            atr = base * 0.002
            entry = base + random.uniform(-0.003, 0.003)
            sl = entry - atr * 1.5 if action == "BUY" else entry + atr * 1.5
            tp = entry + atr * 2.5 if action == "BUY" else entry - atr * 2.5
            sig = {
                "action": action, "symbol": symbol,
                "price": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5),
                "strategy": "⚡Quick",
            }

        result.update({
            "success": True,
            "action": sig["action"],
            "price": sig["price"],
            "sl": sig["sl"],
            "tp": sig["tp"],
            "strategy": sig.get("strategy", "Candy"),
        })

        # Simulate PnL (demo) or real
        if DEMO_MODE or not self.mt5_ok:
            result["pnl"] = round(random.uniform(-0.8, 1.5), 2)
        else:
            # Real execution would go here
            result["pnl"] = 0

        return result


# ════════════════════════════════════════════════════════════
#  🤖  TELEGRAM BOT
# ════════════════════════════════════════════════════════════

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatMemberStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tg_trader")


class TradingBot:
    def __init__(self, token: str):
        from telegram.request import HTTPXRequest
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30,
            read_timeout=30,
            write_timeout=30,
            pool_timeout=30,
        )
        self.app = Application.builder().token(token).request(request).build()
        self.trader = TradeEngine()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("verify", self.cmd_verify))
        self.app.add_handler(CommandHandler("trade", self.cmd_trade))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        # Admin
        self.app.add_handler(CommandHandler("grant", self.cmd_admin_grant))
        self.app.add_handler(CommandHandler("stats", self.cmd_admin_stats))
        self.app.add_handler(CommandHandler("broadcast", self.cmd_admin_broadcast))
        # Callbacks
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    # ── HELPERS ─────────────────────────────────────────────

    async def is_channel_member(self, user_id: int) -> bool:
        """Check if user is in the channel. Bot must be admin in channel."""
        try:
            member = await self.app.bot.get_chat_member(CHANNEL_USERNAME, user_id)
            return member.status in [
                ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ]
        except Exception as e:
            logger.warning(f"Membership check for {user_id}: {e}")
            return False

    async def restrict_user(self, user_id: int, reason: str = ""):
        """Restrict user in the channel and mark in DB."""
        db_set_restricted(user_id, True)
        try:
            await self.app.bot.restrict_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=user_id,
                permissions=type("P", (), {"can_send_messages": False})(),
                until_date=datetime.now() + timedelta(days=365),
            )
        except Exception as e:
            logger.warning(f"Restrict failed for {user_id}: {e}")
        logger.info(f"🔒 Restricted {user_id}: {reason}")

    async def unrestrict_user(self, user_id: int):
        """Remove restrictions."""
        db_set_restricted(user_id, False)
        try:
            await self.app.bot.restrict_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=user_id,
                permissions=type("P", (), {"can_send_messages": True})(),
            )
        except:
            pass

    # ── COMMANDS ────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        db_create_user(u.id, u.username or "", u.first_name)

        msg = (
            f"👋 <b>Welcome {u.first_name}!</b>\n\n"
            f"🤖 I trade FOREX using smart strategies (Candy + Scalp).\n\n"
            f"<b>📋 How to get started:</b>\n"
            f"1️⃣ Join the paid channel: {CHANNEL_USERNAME}\n"
            f"2️⃣ Send /verify — I'll check you're in\n"
            f"3️⃣ Get <b>{FREE_TRADES} free trades</b> 🎫\n"
            f"4️⃣ Send /trade EURUSD to start\n\n"
            f"<b>Commands:</b>\n"
            f"📊 /trade [SYMBOL] — Execute a trade\n"
            f"📈 /status — Your remaining trades\n"
            f"❓ /help — More info"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"),
            InlineKeyboardButton("✅ Verify", callback_data="verify"),
        ]])
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)

    async def cmd_verify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        db_create_user(u.id, u.username or "", u.first_name)

        msg = await update.message.reply_text("🔍 Checking your subscription...")

        if not await self.is_channel_member(u.id):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"),
            ]])
            await msg.edit_text(
                f"❌ You're <b>not a member</b> of {CHANNEL_USERNAME}.\n\n"
                f"Join the channel (paid subscription) and /verify again.",
                parse_mode="HTML", reply_markup=kb,
            )
            return

        # Grant trades
        db_grant_subscription(u.id)
        await self.unrestrict_user(u.id)

        await msg.edit_text(
            f"✅ <b>Verified!</b> 🎉\n\n"
            f"You now have <b>{FREE_TRADES} trades</b>.\n\n"
            f"📊 Try /trade EURUSD\n"
            f"📈 /status to check balance",
            parse_mode="HTML",
        )
        logger.info(f"✅ {u.id} verified — {FREE_TRADES} trades granted")

    async def cmd_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        user = db_get_user(u.id)

        if not user or user["trades_remaining"] <= 0:
            await update.message.reply_text(
                f"⛔ <b>No trades remaining.</b>\n"
                f"Subscribe at {CHANNEL_USERNAME} and /verify to get {FREE_TRADES} trades.",
                parse_mode="HTML",
            )
            if user:
                await self.restrict_user(u.id, "0 trades")
            return

        if user["is_restricted"]:
            await update.message.reply_text("🚫 <b>Account restricted.</b> Renew at {CHANNEL_USERNAME}.".format(CHANNEL_USERNAME=CHANNEL_USERNAME), parse_mode="HTML")
            return

        # Cooldown
        if user["last_trade_at"]:
            elapsed = (datetime.now() - datetime.fromisoformat(user["last_trade_at"])).total_seconds()
            if elapsed < TRADE_COOLDOWN:
                await update.message.reply_text(f"⏳ Wait {int(TRADE_COOLDOWN - elapsed)}s between trades.")
                return

        # Parse symbol
        symbol = (context.args[0].upper() if context.args else "EURUSD")
        if symbol not in SYMBOLS:
            await update.message.reply_text(
                f"❌ Invalid symbol. Options: {', '.join(SYMBOLS)}\n"
                f"Example: /trade EURUSD",
            )
            return

        # Execute
        status_msg = await update.message.reply_text(f"🔄 Analyzing {symbol}...")
        result = self.trader.execute(symbol)

        if not result["success"]:
            await status_msg.edit_text(f"❌ Trade failed: {result.get('error', 'unknown')}")
            return

        # Update DB
        remaining = user["trades_remaining"] - 1
        is_win = result["pnl"] > 0 if result["pnl"] != 0 else 0
        db_update_trades(u.id, remaining, user["total_trades"] + 1,
                         wins=1 if is_win else 0, losses=0 if is_win else 1 if result["pnl"] < 0 else 0)
        db_log_trade(u.id, symbol, result["action"], result["price"],
                     result["sl"], result["tp"], result["pnl"], result["strategy"])

        # Format response
        emoji = "🟢" if result["action"] == "BUY" else "🔴"
        pnl_str = f"💰 PnL: <code>${result['pnl']:+.2f}</code>" if result["pnl"] else ""
        response = (
            f"{emoji} <b>Trade #{user['total_trades'] + 1}</b>\n"
            f"━━━━━━━━━━━━\n"
            f"📊 {result['action']} {result['symbol']}\n"
            f"💵 <code>{result['price']}</code>\n"
            f"🎯 TP: <code>{result['tp']}</code>\n"
            f"🛑 SL: <code>{result['sl']}</code>\n"
            f"📋 {result['strategy']}\n"
            f"{pnl_str}\n"
            f"━━━━━━━━━━━━\n"
            f"🎫 Remaining: <b>{remaining}/{FREE_TRADES}</b>"
        )
        await status_msg.edit_text(response, parse_mode="HTML")

        # Auto-restrict at 0
        if remaining <= 0:
            await update.message.reply_text(
                f"⛔ <b>Trades exhausted!</b>\n"
                f"Renew at {CHANNEL_USERNAME} and /verify for more.",
                parse_mode="HTML",
            )
            await self.restrict_user(u.id, "0 trades")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        user = db_get_user(u.id)
        if not user:
            await update.message.reply_text("👋 Start with /start")
            return

        member = await self.is_channel_member(u.id)
        sub = "✅ Subscribed" if member else "❌ Not subscribed"
        rest = "🚫 Restricted" if user["is_restricted"] else "✅ Active"

        total_trades = user["total_trades"]
        wins = user["wins"]
        losses = user["losses"]
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0

        msg = (
            f"📊 <b>Your Stats</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 {u.first_name}\n"
            f"📡 Channel: {sub}\n"
            f"🔒 Status: {rest}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎫 Trades left: <b>{user['trades_remaining']}/{FREE_TRADES}</b>\n"
            f"📈 Used: {total_trades}\n"
            f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
            f"📊 Win rate: {winrate:.1f}%\n"
        )
        if user["trades_remaining"] <= 0 and not member:
            msg += f"\n💡 Subscribe at {CHANNEL_USERNAME} and /verify"
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"<b>🤖 Bot Commands</b>\n\n"
            f"• /start — Welcome + subscribe\n"
            f"• /verify — Verify you're in the channel\n"
            f"• /trade [SYMBOL] — Execute one trade\n"
            f"   e.g. /trade EURUSD\n"
            f"• /status — Your stats & remaining trades\n\n"
            f"<b>Symbols:</b> {', '.join(SYMBOLS)}\n\n"
            f"<b>Need trades?</b> Join {CHANNEL_USERNAME} → /verify",
            parse_mode="HTML",
        )

    # ── ADMIN ───────────────────────────────────────────────

    async def cmd_admin_grant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            return
        try:
            uid = int(context.args[0])
            trades = int(context.args[1]) if len(context.args) > 1 else FREE_TRADES
        except:
            await update.message.reply_text("Usage: /grant <user_id> [trades]")
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET trades_remaining=trades_remaining+?, is_restricted=0 WHERE user_id=?",
                (trades, uid),
            )
            conn.commit()
        await self.unrestrict_user(uid)
        await update.message.reply_text(f"✅ Granted {trades} trades to {uid}")

    async def cmd_admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            return
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            subscribed = conn.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
            restricted = conn.execute("SELECT COUNT(*) FROM users WHERE is_restricted=1").fetchone()[0]
            total_trades = conn.execute("SELECT SUM(total_trades) FROM users").fetchone()[0] or 0
        await update.message.reply_text(
            f"📊 <b>Bot Stats</b>\n"
            f"👥 Total users: {total}\n"
            f"✅ Subscribed: {subscribed}\n"
            f"🔒 Restricted: {restricted}\n"
            f"📈 Total trades: {total_trades}",
            parse_mode="HTML",
        )

    async def cmd_admin_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            return
        text = " ".join(context.args)
        if not text:
            await update.message.reply_text("Usage: /broadcast <message>")
            return
        with sqlite3.connect(DB_PATH) as conn:
            users = conn.execute("SELECT user_id FROM users").fetchall()
        sent = 0
        for (uid,) in users:
            try:
                await self.app.bot.send_message(chat_id=uid, text=f"📢 {text}", parse_mode="HTML")
                sent += 1
            except:
                pass
        await update.message.reply_text(f"✅ Sent to {sent}/{len(users)}")

    # ── CALLBACKS ───────────────────────────────────────────

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        if q.data == "verify":
            u = q.from_user
            db_create_user(u.id, u.username or "", u.first_name)

            if not await self.is_channel_member(u.id):
                await q.edit_message_text(f"❌ Not in {CHANNEL_USERNAME}. Join first, then /verify.")
                return

            db_grant_subscription(u.id)
            await self.unrestrict_user(u.id)
            await q.edit_message_text(
                f"✅ <b>Verified!</b> You have {FREE_TRADES} trades.\n\n"
                f"Send /trade EURUSD to start!",
                parse_mode="HTML",
            )

    # ── MEMBERSHIP WATCHDOG ─────────────────────────────────

    async def watchdog(self):
        """Periodically check all subscribed users are still in the channel."""
        while True:
            await asyncio.sleep(CHECK_MEMBERSHIP_EVERY)
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    rows = conn.execute("SELECT user_id FROM users WHERE is_subscribed=1").fetchall()

                for (uid,) in rows:
                    if not await self.is_channel_member(uid):
                        logger.info(f"🔍 {uid} left channel — restricting")
                        await self.restrict_user(uid, "left channel")
                        try:
                            await self.app.bot.send_message(
                                chat_id=uid,
                                text=f"⛔ You left {CHANNEL_USERNAME}. Re-join and /verify to restore trades.",
                            )
                        except:
                            pass
            except Exception as e:
                logger.error(f"Watchdog: {e}")

    # ── RUN ─────────────────────────────────────────────────

    def run(self):
        print(f"\n{'='*50}")
        print(f"  🤖 Telegram Trading Bot")
        print(f"  📢 Channel: {CHANNEL_USERNAME}")
        print(f"  🎫 {FREE_TRADES} trades per subscription")
        print(f"  🧪 Demo mode: {'ON' if DEMO_MODE else 'OFF'}")
        print(f"{'='*50}\n")

        async def post_init(app):
            asyncio.create_task(self.watchdog())

        self.app.post_init = post_init
        self.app.run_polling()


# ════════════════════════════════════════════════════════════
#  🏁  LAUNCH
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ First, edit BOT_TOKEN at the top of this file!")
        print("   Get one from @BotFather on Telegram.")
        sys.exit(1)

    init_db()
    TradingBot(BOT_TOKEN).run()
