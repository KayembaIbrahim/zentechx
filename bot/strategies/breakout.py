"""Breakout Strategy — price and volume breakouts from consolidation.

Detects when price breaks above/below a range with increased volume.
Uses ATR for stop placement and recent range for target.
"""
import pandas as pd
import numpy as np
from typing import Optional
from bot.strategies.base import BaseStrategy, Signal
from bot.utils.logger import logger


class BreakoutStrategy(BaseStrategy):
    """Breakout trading from consolidation ranges."""

    def __init__(self, config: dict, connector):
        super().__init__("Breakout", config, connector)
        strat_cfg = config.get("strategies", {}).get("breakout", {})
        self.enabled = strat_cfg.get("enabled", True)
        self.weight = strat_cfg.get("weight", 0.7)
        self.timeframe = strat_cfg.get("timeframe", "M5")
        p = strat_cfg.get("params", {})
        self.lookback = p.get("lookback", 20)
        self.breakout_mult = p.get("breakout_multiplier", 0.5)
        self.volume_threshold = p.get("volume_threshold", 1.5)
        self.atr_mult = p.get("atr_multiplier", 2.0)
        self.min_volume = p.get("min_volume", 0.01)
        self.max_spread = config.get("trading", {}).get("max_spread", 30)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate range levels and volume metrics."""
        # Rolling highs and lows for range detection
        df["range_high"] = df["high"].rolling(window=self.lookback).max()
        df["range_low"] = df["low"].rolling(window=self.lookback).min()
        df["range_mid"] = (df["range_high"] + df["range_low"]) / 2
        df["range_pct"] = ((df["range_high"] - df["range_low"]) / df["range_mid"]) * 100

        # Volume metrics
        df["vol_ma"] = df["tick_volume"].rolling(window=self.lookback).mean()
        df["vol_ratio"] = df["tick_volume"] / df["vol_ma"].replace(0, np.nan)

        # Price position within the range
        df["range_position"] = ((df["close"] - df["range_low"]) /
                                (df["range_high"] - df["range_low"]).replace(0, np.nan))

        return df

    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze for breakout opportunity."""
        if not self.enabled:
            return None

        if not self.check_spread(symbol, self.max_spread):
            return None

        df = self.get_rates(symbol, count=80)
        if df is None or len(df) < self.lookback + 5:
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        range_high = df["range_high"].values
        range_low = df["range_low"].values
        range_pct = df["range_pct"].values
        vol_ratio = df["vol_ratio"].values
        vol_ma = df["vol_ma"].values

        atr = self.compute_atr(df)
        current_price = close[-1]

        # ── Consolidation Check ──────────────────────────────────
        # Range should be reasonably tight (< 2% for forex)
        recent_range = range_pct[-1]
        if recent_range > 2.0:
            return None

        # Price should be near the range boundary (within 0.1%)
        near_high = current_price >= range_high[-2] * 0.999
        near_low = current_price <= range_low[-2] * 1.001

        # ── Volume Confirmation ──────────────────────────────────
        volume_surge = vol_ratio[-1] > self.volume_threshold
        if not volume_surge:
            return None

        # ── Breakout Detection ───────────────────────────────────
        if near_high and volume_surge:
            # Bullish breakout
            sl = current_price - atr * self.atr_mult
            # Target: range height projection
            range_height = range_high[-2] - range_low[-2]
            tp = current_price + range_height * 1.0
            confidence = 0.6
            if vol_ratio[-1] > 2.0:
                confidence = 0.7
            return Signal("BUY", symbol, current_price, sl, tp,
                          confidence=confidence, strategy=self.name,
                          timeframe=self.timeframe, volume=self.min_volume,
                          reason=f"breakout_buy_vol{vol_ratio[-1]:.1f}x")

        elif near_low and volume_surge:
            # Bearish breakout
            sl = current_price + atr * self.atr_mult
            range_height = range_high[-2] - range_low[-2]
            tp = current_price - range_height * 1.0
            confidence = 0.6
            if vol_ratio[-1] > 2.0:
                confidence = 0.7
            return Signal("SELL", symbol, current_price, sl, tp,
                          confidence=confidence, strategy=self.name,
                          timeframe=self.timeframe, volume=self.min_volume,
                          reason=f"breakout_sell_vol{vol_ratio[-1]:.1f}x")

        return None
