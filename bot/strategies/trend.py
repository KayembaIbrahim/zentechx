"""Trend Following Strategy — medium-term trend with ADX confirmation.

Uses 200 EMA for major trend direction and 50 EMA for entries.
ADX confirms trend strength. Only trades in the direction of the major trend.
"""
import pandas as pd
import numpy as np
from typing import Optional
from bot.strategies.base import BaseStrategy, Signal
from bot.utils.logger import logger


class TrendStrategy(BaseStrategy):
    """Medium-term trend following with ADX strength filter."""

    def __init__(self, config: dict, connector):
        super().__init__("Trend", config, connector)
        strat_cfg = config.get("strategies", {}).get("trend", {})
        self.enabled = strat_cfg.get("enabled", True)
        self.weight = strat_cfg.get("weight", 1.0)
        self.timeframe = strat_cfg.get("timeframe", "M5")
        p = strat_cfg.get("params", {})
        self.trend_ema = p.get("trend_ema", 200)
        self.entry_ema = p.get("entry_ema", 50)
        self.adx_period = p.get("adx_period", 14)
        self.adx_threshold = p.get("adx_threshold", 25)
        self.atr_mult = p.get("atr_multiplier", 2.0)
        self.min_volume = p.get("min_volume", 0.01)
        self.max_spread = config.get("trading", {}).get("max_spread", 30)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs and ADX."""
        df["trend_ema"] = df["close"].ewm(span=self.trend_ema, adjust=False).mean()
        df["entry_ema"] = df["close"].ewm(span=self.entry_ema, adjust=False).mean()

        # ADX calculation using pandas
        high = df["high"]
        low = df["low"]
        close = df["close"]

        # True Range
        df["tr"] = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        # Directional Movements
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        df["plus_dm"] = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        df["minus_dm"] = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # Smoothed ATR and DMs
        df["atr"] = df["tr"].rolling(window=self.adx_period).mean()
        df["plus_di"] = 100 * (df["plus_dm"].rolling(window=self.adx_period).mean() / df["atr"].replace(0, np.nan))
        df["minus_di"] = 100 * (df["minus_dm"].rolling(window=self.adx_period).mean() / df["atr"].replace(0, np.nan))

        # DX and ADX
        di_sum = df["plus_di"] + df["minus_di"]
        di_diff = (df["plus_di"] - df["minus_di"]).abs()
        df["dx"] = 100 * di_diff / di_sum.replace(0, np.nan)
        df["adx"] = df["dx"].rolling(window=self.adx_period).mean()

        # Fill NaN
        df[["plus_di", "minus_di", "adx"]] = df[["plus_di", "minus_di", "adx"]].fillna(0)

        return df

    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze for trend-following entry."""
        if not self.enabled:
            return None

        if not self.check_spread(symbol, self.max_spread):
            return None

        df = self.get_rates(symbol, count=250)
        if df is None or len(df) < self.trend_ema + 10:
            return None

        close = df["close"].values
        trend_ema = df["trend_ema"].values
        entry_ema = df["entry_ema"].values
        adx = df["adx"].values
        plus_di = df["plus_di"].values
        minus_di = df["minus_di"].values

        atr = self.compute_atr(df)
        current_price = close[-1]

        # ── Major Trend ──────────────────────────────────────────
        above_trend = current_price > trend_ema[-1] and close[-10] > trend_ema[-10]
        below_trend = current_price < trend_ema[-1] and close[-10] < trend_ema[-10]

        # ── Trend Strength ───────────────────────────────────────
        strong_trend = adx[-1] > self.adx_threshold
        if not strong_trend:
            return None

        # ── Entry Signal ─────────────────────────────────────────
        if above_trend:
            near_entry_ema = abs(current_price - entry_ema[-1]) / entry_ema[-1] < 0.002
            di_bullish = plus_di[-1] > minus_di[-1]

            if near_entry_ema and di_bullish:
                sl = current_price - atr * self.atr_mult
                tp = current_price + atr * self.atr_mult * 2.0
                confidence = 0.65 + min(adx[-1] / 100, 0.25)
                return Signal("BUY", symbol, current_price, sl, tp,
                              confidence=min(confidence, 0.9),
                              strategy=self.name, timeframe=self.timeframe,
                              volume=self.min_volume,
                              reason=f"trend_buy_adx{adx[-1]:.0f}")

        elif below_trend:
            near_entry_ema = abs(current_price - entry_ema[-1]) / entry_ema[-1] < 0.002
            di_bearish = minus_di[-1] > plus_di[-1]

            if near_entry_ema and di_bearish:
                sl = current_price + atr * self.atr_mult
                tp = current_price - atr * self.atr_mult * 2.0
                confidence = 0.65 + min(adx[-1] / 100, 0.25)
                return Signal("SELL", symbol, current_price, sl, tp,
                              confidence=min(confidence, 0.9),
                              strategy=self.name, timeframe=self.timeframe,
                              volume=self.min_volume,
                              reason=f"trend_sell_adx{adx[-1]:.0f}")

        return None
