#!/usr/bin/env python3
"""Simple Telegram bot using requests (not python-telegram-bot). More reliable."""
import os, sys, time, json, sqlite3, logging, random
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
import requests as http

# ═══════ CONFIG ═══════════════════════════════════════════
BOT_TOKEN = "8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI"
CHANNEL_USERNAME = "@zenfxctc"
ADMIN_USER_ID = 955396728
FREE_TRADES = 10
DEMO_MODE = True
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
CHECK_INTERVAL = 5         # seconds between polling

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ═══════ DB ═══════════════════════════════════════════════
DB_PATH = Path(__file__).parent / "bot_data" / "users.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '', first_name TEXT DEFAULT '',
                trades_remaining INTEGER DEFAULT 0, total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
                is_subscribed INTEGER DEFAULT 0, is_restricted INTEGER DEFAULT 0,
                last_trade_at TEXT, subscribed_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                symbol TEXT, action TEXT, price REAL, sl REAL, tp REAL,
                pnl REAL DEFAULT 0, strategy TEXT DEFAULT 'Candy',
                created_at TEXT DEFAULT (datetime('now'))
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
        conn.execute("INSERT OR IGNORE INTO users (user_id,username,first_name) VALUES (?,?,?)",
                     (user_id, username, first_name)); conn.commit()

def db_grant(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""UPDATE users SET trades_remaining=?, total_trades=0, wins=0, losses=0,
            is_subscribed=1, is_restricted=0, subscribed_at=datetime('now') WHERE user_id=?""",
            (FREE_TRADES, user_id)); conn.commit()

def db_trade(user_id, remaining, total, wins=0, losses=0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET trades_remaining=?, total_trades=?, wins=wins+?, losses=losses+?, last_trade_at=datetime('now') WHERE user_id=?",
                     (remaining, total, wins, losses, user_id)); conn.commit()

def db_restrict(user_id, val=1):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET is_restricted=? WHERE user_id=?", (val, user_id)); conn.commit()

# ═══════ TELEGRAM API ═══════════════════════════════════
def tg(method, data=None):
    """Call Telegram API. Returns JSON response."""
    for attempt in range(3):
        try:
            r = http.post(f"{API}/{method}", json=data, timeout=15)
            return r.json()
        except Exception as e:
            if attempt < 2: time.sleep(2)
    return None

def send_msg(chat_id, text, parse_mode="HTML", kb=None):
    d = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if kb: d["reply_markup"] = json.dumps(kb)
    return tg("sendMessage", d)

def edit_msg(chat_id, msg_id, text, parse_mode="HTML"):
    return tg("editMessageText", {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": parse_mode})

def answer_cb(cb_id, text=""):
    return tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})

def get_member(chat_id, user_id):
    return tg("getChatMember", {"chat_id": chat_id, "user_id": user_id})

def restrict_member(chat_id, user_id):
    return tg("restrictChatMember", {
        "chat_id": chat_id, "user_id": user_id,
        "permissions": {"can_send_messages": False},
        "until_date": int((datetime.now() + timedelta(days=365)).timestamp()),
    })

def unrestrict_member(chat_id, user_id):
    return tg("restrictChatMember", {
        "chat_id": chat_id, "user_id": user_id,
        "permissions": {"can_send_messages": True},
    })

# ═══════ TRADING ═══════════════════════════════════════
import pandas as pd, numpy as np

def execute_trade(symbol):
    """Execute a Candy/Scalp trade. Returns result dict."""
    # Simulate data
    base = {"EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)
    price = round(base + random.uniform(-0.005, 0.005), 5)
    atr = round(base * 0.002, 5)
    action = random.choice(["BUY", "SELL"])
    sl = round(price - atr * 1.5 if action == "BUY" else price + atr * 1.5, 5)
    tp = round(price + atr * 2.5 if action == "BUY" else price - atr * 2.5, 5)
    pnl = round(random.uniform(-0.8, 1.2), 2)
    return {"action": action, "symbol": symbol, "price": price, "sl": sl, "tp": tp, "pnl": pnl, "strategy": "🍬Candy", "success": True}

# ═══════ HANDLERS ═══════════════════════════════════════
def handle_update(update):
    """Process a single Telegram update."""
    try:
        # Message
        msg = update.get("message")
        if msg:
            chat_id = msg["chat"]["id"]
            user = msg.get("from", {})
            uid = user["id"]
            uname = user.get("username", "")
            fname = user.get("first_name", "")
            text = msg.get("text", "")
            cb_data = None

            # Callback query
        cb = update.get("callback_query")
        if cb:
            chat_id = cb["message"]["chat"]["id"]
            uid = cb["from"]["id"]
            uname = cb["from"].get("username", "")
            fname = cb["from"].get("first_name", "")
            text = cb.get("data", "")
            cb_id = cb["id"]
            msg_id = cb["message"]["message_id"]
            cb_data = (cb_id, msg_id)

        db_create(uid, uname, fname)
        user_data = db_get(uid)

        # --- COMMANDS ---
        if text == "/start":
            kb = {"inline_keyboard": [[
                {"text": "📢 Join Channel", "url": f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"},
                {"text": "✅ Verify", "callback_data": "verify"},
            ]]}
            send_msg(chat_id,
                f"👋 <b>Welcome {fname}!</b>\n\n"
                f"🤖 I trade FOREX using smart Candy + Scalp strategies.\n\n"
                f"<b>How it works:</b>\n"
                f"1️⃣ Subscribe to {CHANNEL_USERNAME} (paid channel)\n"
                f"2️⃣ Send /verify to confirm\n"
                f"3️⃣ Get <b>{FREE_TRADES} free trades</b>\n"
                f"4️⃣ Send /trade EURUSD to start\n\n"
                f"📊 /trade [SYMBOL] - Execute\n📈 /status - Remaining trades", kb=kb)

        elif text == "/verify" or text == "verify":
            member = get_member(CHANNEL_USERNAME, uid)
            status = member.get("result", {}).get("status", "left") if member else "left"
            if status in ("member", "administrator", "creator"):
                db_grant(uid)
                unrestrict_member(CHANNEL_USERNAME, uid)
                if cb_data:
                    edit_msg(chat_id, cb_data[1], f"✅ <b>Verified!</b> You have {FREE_TRADES} trades! 🎉")
                    answer_cb(cb_data[0])
                else:
                    send_msg(chat_id, f"✅ <b>Verified!</b> You have {FREE_TRADES} trades! 🎉\n\nTry /trade EURUSD")
            else:
                kb = {"inline_keyboard": [[
                    {"text": "📢 Join Channel", "url": f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"}
                ]]}
                if cb_data:
                    edit_msg(chat_id, cb_data[1], f"❌ Not a member of {CHANNEL_USERNAME}. Join first!", kb=kb)
                    answer_cb(cb_data[0])
                else:
                    send_msg(chat_id, f"❌ You're not a member of {CHANNEL_USERNAME}.\nJoin and /verify again.", kb=kb)

        elif text and text.startswith("/trade"):
            if not user_data or user_data["trades_remaining"] <= 0:
                send_msg(chat_id, f"⛔ No trades left. Subscribe at {CHANNEL_USERNAME} and /verify.")
                if user_data: db_restrict(uid)
                return
            if user_data["is_restricted"]:
                send_msg(chat_id, "🚫 Account restricted.")
                return

            symbol = text.split()[1].upper() if len(text.split()) > 1 else "EURUSD"
            if symbol not in SYMBOLS:
                send_msg(chat_id, f"❌ Invalid symbol. Options: {', '.join(SYMBOLS)}")
                return

            result = execute_trade(symbol)
            if not result["success"]:
                send_msg(chat_id, f"❌ Trade failed")
                return

            remaining = user_data["trades_remaining"] - 1
            is_win = 1 if result["pnl"] > 0 else 0
            is_loss = 1 if result["pnl"] < 0 else 0
            db_trade(uid, remaining, user_data["total_trades"] + 1, is_win, is_loss)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO trade_log (user_id,symbol,action,price,sl,tp,pnl,strategy) VALUES (?,?,?,?,?,?,?,?)",
                             (uid, symbol, result["action"], result["price"], result["sl"], result["tp"], result["pnl"], result["strategy"]))
                conn.commit()

            emoji = "🟢" if result["action"] == "BUY" else "🔴"
            pnl_s = f"💰 PnL: <code>${result['pnl']:+.2f}</code>" if result["pnl"] else ""
            send_msg(chat_id,
                f"{emoji} <b>Trade #{user_data['total_trades']+1}</b>\n"
                f"━━━━━━━━━━━━\n"
                f"📊 {result['action']} {result['symbol']}\n"
                f"💵 <code>{result['price']}</code>\n"
                f"🎯 TP: <code>{result['tp']}</code>\n🛑 SL: <code>{result['sl']}</code>\n"
                f"{pnl_s}\n━━━━━━━━━━━━\n"
                f"🎫 Remaining: <b>{remaining}/{FREE_TRADES}</b>")

            if remaining <= 0:
                send_msg(chat_id, f"⛔ Trades exhausted. Renew at {CHANNEL_USERNAME} and /verify.")
                restrict_member(CHANNEL_USERNAME, uid)
                db_restrict(uid)

        elif text == "/status":
            if not user_data:
                send_msg(chat_id, "👋 Send /start first!")
                return
            member = get_member(CHANNEL_USERNAME, uid)
            status = member.get("result", {}).get("status", "left") if member else "left"
            sub = "✅ Subscribed" if status in ("member","administrator","creator") else "❌ Not subscribed"
            rest = "🚫 Restricted" if user_data["is_restricted"] else "✅ Active"
            tot = user_data["total_trades"]
            wr = (user_data["wins"]/tot*100) if tot > 0 else 0
            send_msg(chat_id,
                f"📊 <b>Your Stats</b>\n━━━━━━━━━━━━━━\n"
                f"📡 Channel: {sub}\n🔒 Status: {rest}\n"
                f"🎫 Trades left: <b>{user_data['trades_remaining']}/{FREE_TRADES}</b>\n"
                f"📈 Used: {tot} | ✅ {user_data['wins']} | ❌ {user_data['losses']}\n"
                f"📊 Win rate: {wr:.1f}%")

        elif text == "/help":
            send_msg(chat_id,
                f"<b>🤖 Bot Commands</b>\n\n"
                f"• /start - Welcome + subscribe\n• /verify - Verify channel membership\n"
                f"• /trade EURUSD - Execute one trade\n• /status - Your stats\n\n"
                f"Symbols: {', '.join(SYMBOLS)}")

        # Admin
        elif text and uid == ADMIN_USER_ID:
            if text.startswith("/grant"):
                parts = text.split()
                if len(parts) >= 2:
                    try:
                        target = int(parts[1])
                        trades = int(parts[2]) if len(parts) > 2 else FREE_TRADES
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("UPDATE users SET trades_remaining=trades_remaining+?, is_restricted=0 WHERE user_id=?", (trades, target))
                            conn.commit()
                        unrestrict_member(CHANNEL_USERNAME, target)
                        send_msg(chat_id, f"✅ Granted {trades} trades to {target}")
                    except: send_msg(chat_id, "Usage: /grant <user_id> [trades]")

            elif text == "/stats":
                with sqlite3.connect(DB_PATH) as conn:
                    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    sub = conn.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
                    rest = conn.execute("SELECT COUNT(*) FROM users WHERE is_restricted=1").fetchone()[0]
                    tr = conn.execute("SELECT SUM(total_trades) FROM users").fetchone()[0] or 0
                send_msg(chat_id, f"📊 <b>Bot Stats</b>\n👥 Users: {total}\n✅ Subscribed: {sub}\n🔒 Restricted: {rest}\n📈 Trades: {tr}")

            elif text.startswith("/broadcast "):
                msg_text = text[11:]
                with sqlite3.connect(DB_PATH) as conn:
                    users = conn.execute("SELECT user_id FROM users").fetchall()
                sent = 0
                for (uid2,) in users:
                    if send_msg(uid2, f"📢 {msg_text}"): sent += 1
                    time.sleep(0.05)
                send_msg(chat_id, f"✅ Sent to {sent}/{len(users)}")

    except Exception as e:
        logging.error(f"Handler error: {e}")

# ═══════ MAIN LOOP ═══════════════════════════════════
def main():
    init_db()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    
    import sys; sys.stdout.flush()
    print(f"\n{'='*50}")
    import sys; sys.stdout.flush()
    print(f"  🤖 Telegram Trading Bot (requests-based)")
    import sys; sys.stdout.flush()
    print(f"  📢 {CHANNEL_USERNAME} | 🎫 {FREE_TRADES} trades")
    import sys; sys.stdout.flush()
    print(f"{'='*50}\n")

    offset = 0
    while True:
        try:
            r = http.post(f"{API}/getUpdates", json={"offset": offset, "timeout": 30}, timeout=35)
            data = r.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    handle_update(update)
        except KeyboardInterrupt:
            print("\n🛑 Stopped")
            break
        except Exception as e:
            logging.error(f"Poll error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
