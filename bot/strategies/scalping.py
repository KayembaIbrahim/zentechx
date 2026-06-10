"""Scalping Strategy — fast entries/exits for small, quick profits.

Scalping approach:
1. Uses fast MAs (9/21) on M1 for ultra-short-term trend
2. RSI(7) for momentum confirmation
3. Entries only when momentum aligns with micro-trend
4. Tight stops, quick targets (5-10 pips)
5. Max hold time of 10 candles
"""
import pandas as pd
import numpy as np
from typing import Optional
from bot.strategies.base import BaseStrategy, Signal
from bot.utils.logger import logger


class ScalpingStrategy(BaseStrategy):
    """Aggressive scalping on M1 for fast accumulation."""

    def __init__(self, config: dict, connector):
        super().__init__("Scalp", config, connector)
        strat_cfg = config.get("strategies", {}).get("scalping", {})
        self.enabled = strat_cfg.get("enabled", True)
        self.weight = strat_cfg.get("weight", 1.0)
        self.timeframe = strat_cfg.get("timeframe", "M1")
        p = strat_cfg.get("params", {})
        self.fast_ma = p.get("fast_ma", 9)
        self.slow_ma = p.get("slow_ma", 21)
        self.rsi_period = p.get("rsi_period", 7)
        self.rsi_ob = p.get("rsi_overbought", 75)
        self.rsi_os = p.get("rsi_oversold", 25)
        self.min_profit_pips = p.get("min_profit_pips", 5)
        self.max_hold_bars = p.get("max_hold_bars", 10)
        self.min_volume = p.get("min_volume", 0.01)
        self.max_spread = config.get("trading", {}).get("max_spread", 30)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate fast MAs and RSI."""
        df["ma_fast"] = df["close"].rolling(window=self.fast_ma).mean()
        df["ma_slow"] = df["close"].rolling(window=self.slow_ma).mean()

        # RSI(7) — faster for scalping
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi"] = df["rsi"].fillna(50)

        # Volume smoothed
        df["vol_ma"] = df["tick_volume"].rolling(window=5).mean()

        # Price acceleration
        df["price_delta"] = df["close"].diff(3)

        return df

    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze for scalping opportunity."""
        if not self.enabled:
            return None

        if not self.check_spread(symbol, self.max_spread):
            return None

        df = self.get_rates(symbol, count=60)
        if df is None or len(df) < self.slow_ma + 5:
            return None

        close = df["close"].values
        ma_fast = df["ma_fast"].values
        ma_slow = df["ma_slow"].values
        rsi = df["rsi"].values
        vol_ma = df["vol_ma"].values
        price_delta = df["price_delta"].values

        current_price = close[-1]
        atr = self.compute_atr(df, period=7)  # shorter ATR for scalping

        # ── Micro-Trend Alignment ────────────────────────────────
        # Price must be actively moving in one direction
        bullish_momentum = price_delta[-1] > 0 and ma_fast[-1] > ma_slow[-1]
        bearish_momentum = price_delta[-1] < 0 and ma_fast[-1] < ma_slow[-1]

        # Volume confirmation
        vol_confirmed = vol_ma[-1] > 0 and close[-1] > 0  # basic check

        # ── RSI Momentum ─────────────────────────────────────────
        if bullish_momentum and vol_confirmed:
            # RSI rising from oversold or mid-range, but not overbought
            rsi_ok = (rsi[-1] > self.rsi_os and rsi[-1] < self.rsi_ob and
                      rsi[-1] > rsi[-2] > rsi[-3])
            if rsi_ok:
                # Micro-pullback entry: price just touched fast MA
                price_vs_fast = abs(current_price - ma_fast[-1]) / ma_fast[-1]
                if price_vs_fast < 0.001:  # near the fast MA
                    sl = current_price - atr * 1.0
                    tp = current_price + atr * 2.0
                    confidence = 0.55
                    if rsi[-1] < 40 and price_delta[-1] > price_delta[-2]:
                        confidence = 0.65
                    return Signal("BUY", symbol, current_price, sl, tp,
                                  confidence=confidence, strategy=self.name,
                                  timeframe=self.timeframe, volume=self.min_volume,
                                  reason=f"scalp_buy_rsi{rsi[-1]:.0f}")

        elif bearish_momentum and vol_confirmed:
            rsi_ok = (rsi[-1] < self.rsi_ob and rsi[-1] > self.rsi_os and
                      rsi[-1] < rsi[-2] < rsi[-3])
            if rsi_ok:
                price_vs_fast = abs(current_price - ma_fast[-1]) / ma_fast[-1]
                if price_vs_fast < 0.001:
                    sl = current_price + atr * 1.0
                    tp = current_price - atr * 2.0
                    confidence = 0.55
                    if rsi[-1] > 60 and price_delta[-1] < price_delta[-2]:
                        confidence = 0.65
                    return Signal("SELL", symbol, current_price, sl, tp,
                                  confidence=confidence, strategy=self.name,
                                  timeframe=self.timeframe, volume=self.min_volume,
                                  reason=f"scalp_sell_rsi{rsi[-1]:.0f}")

        return None
