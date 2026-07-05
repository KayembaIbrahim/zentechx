const express = require('express');
const cors = require('cors');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 5000;
const DATA_FILE = path.join(__dirname, 'signals.json');

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── File-based storage (works without MongoDB) ────────
function readSignals() {
  try {
    if (fs.existsSync(DATA_FILE)) {
      return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
    }
  } catch (e) { /* ignore */ }
  return [];
}

function writeSignals(signals) {
  // Keep max 500 signals
  const trimmed = signals.slice(0, 500);
  fs.writeFileSync(DATA_FILE, JSON.stringify(trimmed, null, 2));
}

// ── MongoDB support (optional) ────────────────────────
let Signal = null;
let mongoConnected = false;

async function tryMongo() {
  const MONGO_URI = process.env.MONGO_URI;
  if (!MONGO_URI) return;
  
  try {
    const mongoose = require('mongoose');
    await mongoose.connect(MONGO_URI, { serverSelectionTimeoutMS: 3000 });
    mongoConnected = true;
    
    const signalSchema = new mongoose.Schema({
      symbol: String, exchange: String, timeframe: { type: String, default: 'D' },
      price: Number, action: { type: String, enum: ['BUY', 'SELL'] },
      strategy: String, confidence: Number, sl: Number, tp: Number,
      time: { type: Date, default: Date.now }
    }, { timestamps: true });
    
    Signal = mongoose.model('Signal', signalSchema);
    console.log('✅ MongoDB connected');
  } catch (err) {
    console.log('ℹ️  MongoDB not available, using file storage');
  }
}

// ── API Routes ─────────────────────────────────────────
app.post('/api/signals', async (req, res) => {
  try {
    const signal = {
      symbol: (req.body.symbol || '').toUpperCase(),
      exchange: req.body.exchange || 'CRYPTO',
      timeframe: req.body.timeframe || 'D',
      price: parseFloat(req.body.price) || 0,
      action: req.body.action || req.body.condition === 'Bullish Entry' ? 'BUY' : 'SELL',
      strategy: req.body.strategy || 'SMA Crossover',
      confidence: req.body.confidence || 75,
      sl: parseFloat(req.body.sl) || 0,
      tp: parseFloat(req.body.tp) || 0,
      time: new Date(req.body.time || Date.now())
    };
    
    if (mongoConnected && Signal) {
      const saved = await Signal.create(signal);
      res.json({ status: 'ok', id: saved._id });
    } else {
      const signals = readSignals();
      signal.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      signals.unshift(signal);
      writeSignals(signals);
      res.json({ status: 'ok', id: signal.id });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/signals', async (req, res) => {
  try {
    const limit = parseInt(req.query.limit) || 30;
    
    if (mongoConnected && Signal) {
      const signals = await Signal.find().sort({ time: -1 }).limit(limit);
      return res.json(signals);
    }
    
    const signals = readSignals().slice(0, limit);
    res.json(signals);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/signals/:symbol', async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    
    if (mongoConnected && Signal) {
      const signals = await Signal.find({ symbol }).sort({ time: -1 }).limit(10);
      return res.json(signals);
    }
    
    const signals = readSignals().filter(s => s.symbol === symbol).slice(0, 10);
    res.json(signals);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/chart/:symbol', async (req, res) => {
  try {
    const symbol = req.params.symbol;
    const interval = req.query.interval || '1h';
    
    // Binance for crypto, free API for others
    let url;
    if (symbol.includes('USD') || symbol.length <= 5) {
      // Try Binance
      const base = symbol.replace('USDT', '').replace('USD', '');
      url = `https://api.binance.com/api/v3/klines?symbol=${base}USDT&interval=${interval}&limit=100`;
    } else {
      url = `https://api.binance.com/api/v3/klines?symbol=${symbol}USDT&interval=${interval}&limit=100`;
    }
    
    const fetch = (await import('node-fetch')).default;
    const response = await fetch(url);
    const data = await response.json();
    
    if (!Array.isArray(data)) {
      return res.json([]);
    }
    
    const candles = data.map(k => ({
      time: Math.floor(k[0] / 1000),
      open: parseFloat(k[1]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3]),
      close: parseFloat(k[4]),
      volume: parseFloat(k[5])
    })).filter(c => c.time && c.close);
    
    res.json(candles);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/health', (req, res) => {
  res.json({ 
    status: 'ok', 
    storage: mongoConnected ? 'mongodb' : 'file',
    signals: readSignals().length,
    uptime: process.uptime()
  });
});

// ── Static files ──────────────────────────────────────
app.get('/indicator.pine', (req, res) => {
  res.sendFile(path.join(__dirname, 'indicator.pine'));
});

app.get('/{*path}', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ── Start ─────────────────────────────────────────────
async function start() {
  await tryMongo();
  app.listen(PORT, () => {
    console.log(`🚀 ZTX Trader live on http://localhost:${PORT}`);
    console.log(`📡 Storage: ${mongoConnected ? 'MongoDB' : 'File (signals.json)'}`);
  });
}

start();
