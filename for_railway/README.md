# 🚀 Deploy to Railway (5 minutes)

## Step 1 — Push to GitHub
```bash
# On your computer:
cd for_railway
git init
git add .
git commit -m "tg trading bot"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

## Step 2 — Deploy on Railway
1. Go to **railway.app** → Login with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select the repo you just pushed
4. Click **Deploy**

## Step 3 — Add Environment Variables
In Railway dashboard, go to your project → **Variables** tab → Add:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | `8880839845:AAGz-T2UL_6F94pbMcN1RpUCaGpY33zQmrI` |
| `CHANNEL_USERNAME` | `@zenfxctc` |
| `ADMIN_USER_ID` | `955396728` |
| `DEMO_MODE` | `True` (change to `False` when MT5 ready) |
| `FREE_TRADES` | `10` |

## Step 4 — Change Start Command
In Railway → **Settings** → Change start command to:
```
python main.py
```

## Done! 🎉
Your bot runs 24/7. No phone needed.
