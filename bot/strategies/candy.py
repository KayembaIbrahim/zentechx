"""Candy Strategy — trend-pullback with EMA + RSI confluence.

The Candy Strategy is a popular retail trading approach that:
1. Uses fast/slow EMA to determine trend direction
2. Waits for pullbacks to the slow EMA for entry
3. Uses RSI to confirm pullback exhaustion
4. Places SL beyond the recent swing low/high
5. Targets using ATR multiples

This works exceptionally well on M1-M5 for scalping smaller accounts.
"""
import pandas as pd
import numpy as np
from typing import Optional
from bot.strategies.base import BaseStrategy, Signal
from bot.utils.logger import logger


class CandyStrategy(BaseStrategy):
    """Candy Strategy — trend-pullback on fast timeframes."""

    def __init__(self, config: dict, connector):
        super().__init__("Candy", config, connector)
        strat_cfg = config.get("strategies", {}).get("candy", {})
        self.enabled = strat_cfg.get("enabled", True)
        self.weight = strat_cfg.get("weight", 1.0)
        self.timeframe = strat_cfg.get("timeframe", "M1")
        p = strat_cfg.get("params", {})
        self.fast_ema = p.get("fast_ema", 5)
        self.slow_ema = p.get("slow_ema", 20)
        self.rsi_period = p.get("rsi_period", 14)
        self.rsi_ob = p.get("rsi_overbought", 70)
        self.rsi_os = p.get("rsi_oversold", 30)
        self.atr_mult = p.get("atr_multiplier", 1.5)
        self.min_volume = p.get("min_volume", 0.01)
        self.max_spread = config.get("trading", {}).get("max_spread", 30)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs and RSI."""
        df["ema_fast"] = df["close"].ewm(span=self.fast_ema, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow_ema, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).ewm(span=self.rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(span=self.rsi_period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi"] = df["rsi"].fillna(50)

        return df

    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze symbol for Candy Strategy entry."""
        if not self.enabled:
            return None

        if not self.check_spread(symbol, self.max_spread):
            return None

        df = self.get_rates(symbol, count=100)
        if df is None or len(df) < self.slow_ema + 5:
            return None

        # Price and indicator data
        close = df["close"].values
        ema_fast = df["ema_fast"].values
        ema_slow = df["ema_slow"].values
        rsi = df["rsi"].values

        atr = self.compute_atr(df)

        # ── Trend Determination ──────────────────────────────────
        # Uptrend: fast EMA above slow EMA, both rising
        uptrend = ema_fast[-1] > ema_slow[-1] and ema_fast[-3] > ema_slow[-3]
        # Downtrend: fast EMA below slow EMA, both falling
        downtrend = ema_fast[-1] < ema_slow[-1] and ema_fast[-3] < ema_slow[-3]

        if not uptrend and not downtrend:
            return None

        # ── Pullback Detection ───────────────────────────────────
        # In uptrend: price pulled back to or near slow EMA
        # In downtrend: price rallied to or near slow EMA
        current_price = close[-1]
        slow_ema_val = ema_slow[-1]
        distance_pct = abs(current_price - slow_ema_val) / slow_ema_val * 100

        # Price should be near the slow EMA (within 0.1-0.3%)
        near_ema = distance_pct < 0.3

        # ── Pullback Exhaustion via RSI ──────────────────────────
        if uptrend:
            # RSI should be oversold or near it (pullback in uptrend)
            rsi_exhausted = rsi[-1] < self.rsi_os + 10  # RSI < 40
            # Or RSI turning up from oversold
            rsi_turning = rsi[-1] > rsi[-2] and rsi[-2] < self.rsi_os + 10
            rsi_ok = rsi_exhausted or rsi_turning

            if near_ema and rsi_ok:
                sl = current_price - atr * self.atr_mult
                tp = current_price + atr * self.atr_mult * 2.0
                confidence = 0.6
                if rsi[-1] < self.rsi_os:
                    confidence = 0.75
                reason = f"uptrend_pullback_rsi{rsi[-1]:.0f}"
                return Signal("BUY", symbol, current_price, sl, tp,
                              confidence=confidence, strategy=self.name,
                              timeframe=self.timeframe, volume=self.min_volume,
                              reason=reason)

        elif downtrend:
            # RSI should be overbought or near it (rally in downtrend)
            rsi_exhausted = rsi[-1] > self.rsi_ob - 10  # RSI > 60
            rsi_turning = rsi[-1] < rsi[-2] and rsi[-2] > self.rsi_ob - 10
            rsi_ok = rsi_exhausted or rsi_turning

            if near_ema and rsi_ok:
                sl = current_price + atr * self.atr_mult
                tp = current_price - atr * self.atr_mult * 2.0
                confidence = 0.6
                if rsi[-1] > self.rsi_ob:
                    confidence = 0.75
                reason = f"downtrend_rally_rsi{rsi[-1]:.0f}"
                return Signal("SELL", symbol, current_price, sl, tp,
                              confidence=confidence, strategy=self.name,
                              timeframe=self.timeframe, volume=self.min_volume,
                              reason=reason)

        return None
