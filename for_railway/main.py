#!/usr/bin/env python3
"""Telegram Trading Bot — ready for Railway deployment."""
import os, sys, time, json, sqlite3, logging, random, requests
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

# ── Config from Environment Variables ───────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@channel")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
FREE_TRADES = int(os.environ.get("FREE_TRADES", "10"))
DEMO_MODE = os.environ.get("DEMO_MODE", "True").lower() == "true"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Database ────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "users.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
                trades_remaining INTEGER DEFAULT 0, total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
                is_subscribed INTEGER DEFAULT 0, is_restricted INTEGER DEFAULT 0,
                last_trade_at TEXT, subscribed_at TEXT, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                symbol TEXT, action TEXT, price REAL, sl REAL, tp REAL,
                pnl REAL DEFAULT 0, strategy TEXT DEFAULT 'Candy',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)

def db_get(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(r) if r else None

def db_create(user_id, username="", first_name=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,0,0,0,0,0,0,NULL,NULL,datetime('now'))",
                     (user_id, username, first_name)); conn.commit()

def db_grant(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET trades_remaining=?,total_trades=0,wins=0,losses=0,is_subscribed=1,is_restricted=0,subscribed_at=datetime('now') WHERE user_id=?",
                     (FREE_TRADES, user_id)); conn.commit()

def db_trade(user_id, rem, tot, w=0, l=0):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET trades_remaining=?,total_trades=?,wins=wins+?,losses=losses+?,last_trade_at=datetime('now') WHERE user_id=?",
                     (rem, tot, w, l, user_id)); conn.commit()

def db_restrict(user_id, v=1):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET is_restricted=? WHERE user_id=?", (v, user_id)); conn.commit()

# ── Telegram API ────────────────────────────────────────
def tg(method, data=None):
    for i in range(3):
        try:
            r = requests.post(f"{API}/{method}", json=data, timeout=20)
            return r.json()
        except: time.sleep(2)
    return None

def send(chat, text, **kw):
    d = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    if "kb" in kw: d["reply_markup"] = json.dumps(kw["kb"])
    return tg("sendMessage", d)

def edit(chat, mid, text):
    return tg("editMessageText", {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML"})

def answer_cb(cid, text=""):
    return tg("answerCallbackQuery", {"callback_query_id": cid, "text": text})

def is_member(uid):
    r = tg("getChatMember", {"chat_id": CHANNEL_USERNAME, "user_id": uid})
    s = r.get("result", {}).get("status") if r else None
    return s in ("member", "administrator", "creator")

def restrict(uid):
    tg("restrictChatMember", {"chat_id": CHANNEL_USERNAME, "user_id": uid,
        "permissions": {"can_send_messages": False},
        "until_date": int((datetime.now()+timedelta(days=365)).timestamp())})

def unrestrict(uid):
    tg("restrictChatMember", {"chat_id": CHANNEL_USERNAME, "user_id": uid,
        "permissions": {"can_send_messages": True}})

# ── Trade Engine (demo) ─────────────────────────────────
import pandas as pd, numpy as np

def execute_trade(symbol):
    base = {"EURUSD":1.0850,"GBPUSD":1.2650,"USDJPY":152.50,"XAUUSD":2350.0}.get(symbol,1.0)
    price = round(base + random.uniform(-0.005,0.005),5)
    atr = round(base*0.002,5)
    action = random.choice(["BUY","SELL"])
    sl = round(price - atr*1.5 if action=="BUY" else price + atr*1.5,5)
    tp = round(price + atr*2.5 if action=="BUY" else price - atr*2.5,5)
    pnl = round(random.uniform(-0.8,1.2),2)
    return {"action":action,"symbol":symbol,"price":price,"sl":sl,"tp":tp,"pnl":pnl,"strategy":"Candy","success":True}

# ── Commands ────────────────────────────────────────────
def handle(update):
    try:
        msg = update.get("message")
        cb = update.get("callback_query")
        cb_id, msg_id = None, None

        if cb:
            chat = cb["message"]["chat"]["id"]
            uid = cb["from"]["id"]
            uname = cb["from"].get("username","")
            fname = cb["from"].get("first_name","")
            text = cb.get("data","")
            cb_id = cb["id"]
            msg_id = cb["message"]["message_id"]
        elif msg:
            chat = msg["chat"]["id"]
            uid = msg["from"]["id"]
            uname = msg["from"].get("username","")
            fname = msg["from"].get("first_name","")
            text = msg.get("text","")
        else:
            return

        db_create(uid, uname, fname)
        u = db_get(uid)

        # ── /start ───────────────────────────────────────
        if text == "/start":
            kb = {"inline_keyboard":[[
                {"text":"📢 Subscribe","url":f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"},
                {"text":"✅ Verify","callback_data":"verify"}
            ]]}
            send(chat,
                f"👋 <b>Welcome {fname}!</b>\n\n"
                f"1️⃣ Subscribe to {CHANNEL_USERNAME}\n"
                f"2️⃣ /verify → get <b>{FREE_TRADES} trades</b>\n"
                f"3️⃣ /trade EURUSD to start\n"
                f"4️⃣ /status to check", kb=kb)

        # ── /verify ──────────────────────────────────────
        elif text == "/verify" or text == "verify":
            if is_member(uid):
                db_grant(uid)
                unrestrict(uid)
                unrestrict(uid)
                if cb_id:
                    edit(chat, msg_id, f"✅ <b>Verified!</b> {FREE_TRADES} trades granted 🎉")
                    answer_cb(cb_id)
                else:
                    send(chat, f"✅ <b>Verified!</b> You have {FREE_TRADES} trades! 🎉\n\nTry /trade EURUSD")
            else:
                kb = {"inline_keyboard":[[{"text":"📢 Subscribe","url":f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"}]]}
                t = f"❌ Not in {CHANNEL_USERNAME}. Subscribe first!"
                if cb_id:
                    edit(chat, msg_id, t, kb)
                    answer_cb(cb_id)
                else:
                    send(chat, t, kb=kb)

        # ── /trade ───────────────────────────────────────
        elif text and text.startswith("/trade"):
            if not u or u["trades_remaining"] <= 0:
                send(chat, f"⛔ No trades. Subscribe at {CHANNEL_USERNAME} and /verify")
                if u: db_restrict(uid)
                return
            if u.get("is_restricted"):
                send(chat, "🚫 Restricted. Re-subscribe at {CHANNEL_USERNAME}")
                return

            sym = text.split()[1].upper() if len(text.split())>1 else "EURUSD"
            if sym not in SYMBOLS:
                send(chat, f"❌ Invalid. Options: {', '.join(SYMBOLS)}")
                return

            res = execute_trade(sym)
            if not res["success"]: return

            rem = u["trades_remaining"] - 1
            w = 1 if res["pnl"]>0 else 0
            l = 1 if res["pnl"]<0 else 0
            db_trade(uid, rem, u["total_trades"]+1, w, l)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO trade_log (user_id,symbol,action,price,sl,tp,pnl,strategy) VALUES (?,?,?,?,?,?,?,?)",
                             (uid, sym, res["action"], res["price"], res["sl"], res["tp"], res["pnl"], res["strategy"]))
                conn.commit()

            e = "🟢" if res["action"]=="BUY" else "🔴"
            pnl_s = f"💰 PnL: <code>\${res['pnl']:+.2f}</code>" if res["pnl"] else ""
            send(chat,
                f"{e} <b>Trade #{u['total_trades']+1}</b>\n"
                f"📊 {res['action']} {res['symbol']}\n"
                f"💵 <code>{res['price']}</code> | 🎯 {res['tp']} | 🛑 {res['sl']}\n"
                f"{pnl_s}\n🎫 Remaining: <b>{rem}/{FREE_TRADES}</b>")

            if rem <= 0:
                send(chat, f"⛔ Trades done. Subscribe again at {CHANNEL_USERNAME} and /verify")
                restrict(uid)
                db_restrict(uid)

        # ── /status ──────────────────────────────────────
        elif text == "/status":
            if not u: send(chat, "👋 Send /start first!"); return
            sub = "✅" if is_member(uid) else "❌"
            rest = "🚫" if u.get("is_restricted") else "✅"
            wr = (u["wins"]/u["total_trades"]*100) if u["total_trades"]>0 else 0
            send(chat,
                f"📊 <b>Status</b>\n"
                f"📡 Channel: {sub}\n🔒 Account: {rest}\n"
                f"🎫 Left: <b>{u['trades_remaining']}/{FREE_TRADES}</b>\n"
                f"📈 Trades: {u['total_trades']} | ✅ {u['wins']} | ❌ {u['losses']}\n"
                f"📊 Win rate: {wr:.1f}%")

        # ── /help ────────────────────────────────────────
        elif text == "/help":
            send(chat, f"<b>Commands:</b>\n/start - Welcome\n/verify - Verify\n/trade SYMBOL - Trade\n/status - Stats\n\nSymbols: {', '.join(SYMBOLS)}")

        # ── /grant (admin) ───────────────────────────────
        elif text and uid==ADMIN_USER_ID and text.startswith("/grant"):
            parts = text.split()
            if len(parts)>=2:
                try:
                    t = int(parts[1]); tr = int(parts[2]) if len(parts)>2 else FREE_TRADES
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("UPDATE users SET trades_remaining=trades_remaining+?,is_restricted=0 WHERE user_id=?",(tr,t))
                        conn.commit()
                    send(chat, f"✅ Granted {tr} trades to {t}")
                except: send(chat, "Usage: /grant <user_id> [trades]")

        # ── /stats (admin) ──────────────────────────────
        elif text and uid==ADMIN_USER_ID and text=="/stats":
            with sqlite3.connect(DB_PATH) as conn:
                tot = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                sub = conn.execute("SELECT COUNT(*) FROM users WHERE is_subscribed=1").fetchone()[0]
                res = conn.execute("SELECT COUNT(*) FROM users WHERE is_restricted=1").fetchone()[0]
                tr = conn.execute("SELECT SUM(total_trades) FROM users").fetchone()[0] or 0
            send(chat, f"📊 <b>Stats</b>\n👥 Users: {tot}\n✅ Subscribed: {sub}\n🔒 Restricted: {res}\n📈 Trades: {tr}")

        # ── /broadcast (admin) ──────────────────────────
        elif text and uid==ADMIN_USER_ID and text.startswith("/broadcast "):
            t = text[11:]
            with sqlite3.connect(DB_PATH) as conn:
                users = conn.execute("SELECT user_id FROM users").fetchall()
            s=0
            for (u2,) in users:
                try: requests.post(f"{API}/sendMessage",json={"chat_id":u2,"text":f"📢 {t}","parse_mode":"HTML"},timeout=5)
                except: pass
                s+=1; time.sleep(0.05)
            send(chat, f"✅ Sent to {s}/{len(users)}")

    except Exception as e:
        logging.error(f"Handler: {e}")

# ── Main Loop ───────────────────────────────────────────
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    init_db()
    print(f"\n🤖 Telegram Bot starting...")
    print(f"📢 {CHANNEL_USERNAME} | 🎫 {FREE_TRADES} trades | 🧪 Demo: {DEMO_MODE}\n")
    sys.stdout.flush()

    offset = 0
    while True:
        try:
            r = requests.post(f"{API}/getUpdates", json={"offset":offset,"timeout":10}, timeout=15)
            data = r.json()
            if data.get("ok"):
                for upd in data.get("result",[]):
                    offset = upd["update_id"] + 1
                    handle(upd)
        except KeyboardInterrupt: break
        except: time.sleep(3)

if __name__ == "__main__":
    main()
