"""Mean Reversion Strategy — Bollinger Bands + RSI for reversal entries.

Works best in ranging/choppy markets when price reverts to the mean.
Enters when price touches BB extremes and RSI confirms exhaustion.
"""
import pandas as pd
import numpy as np
from typing import Optional
from bot.strategies.base import BaseStrategy, Signal
from bot.utils.logger import logger


class MeanReversionStrategy(BaseStrategy):
    """Mean reversion using Bollinger Bands and RSI."""

    def __init__(self, config: dict, connector):
        super().__init__("MeanRev", config, connector)
        strat_cfg = config.get("strategies", {}).get("mean_reversion", {})
        self.enabled = strat_cfg.get("enabled", True)
        self.weight = strat_cfg.get("weight", 0.5)
        self.timeframe = strat_cfg.get("timeframe", "M5")
        p = strat_cfg.get("params", {})
        self.bb_period = p.get("bb_period", 20)
        self.bb_std = p.get("bb_std", 2.0)
        self.rsi_period = p.get("rsi_period", 14)
        self.rsi_ob = p.get("rsi_overbought", 75)
        self.rsi_os = p.get("rsi_oversold", 25)
        self.atr_mult = p.get("atr_multiplier", 1.0)
        self.min_volume = p.get("min_volume", 0.01)
        self.max_spread = config.get("trading", {}).get("max_spread", 30)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Bollinger Bands and RSI."""
        # Bollinger Bands
        df["bb_mid"] = df["close"].rolling(window=self.bb_period).mean()
        bb_std = df["close"].rolling(window=self.bb_period).std()
        df["bb_upper"] = df["bb_mid"] + bb_std * self.bb_std
        df["bb_lower"] = df["bb_mid"] - bb_std * self.bb_std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["bb_pct"] = ((df["close"] - df["bb_lower"]) /
                        (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan))

        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi"] = df["rsi"].fillna(50)

        return df

    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze for mean reversion opportunity."""
        if not self.enabled:
            return None

        if not self.check_spread(symbol, self.max_spread):
            return None

        df = self.get_rates(symbol, count=100)
        if df is None or len(df) < self.bb_period + 5:
            return None

        close = df["close"].values
        bb_upper = df["bb_upper"].values
        bb_lower = df["bb_lower"].values
        bb_mid = df["bb_mid"].values
        bb_width = df["bb_width"].values
        rsi = df["rsi"].values

        atr = self.compute_atr(df)
        current_price = close[-1]

        # ── Range Detection ──────────────────────────────────────
        # BB width should be stable (not expanding rapidly — that's a breakout)
        width_stable = bb_width[-1] < bb_width[-5] * 1.1 if len(bb_width) > 5 else True

        if not width_stable:
            return None

        # ── Overextended Detection ───────────────────────────────
        touched_upper = current_price >= bb_upper[-2] * 0.999
        touched_lower = current_price <= bb_lower[-2] * 1.001

        # ── Reversal Signals ─────────────────────────────────────
        if touched_upper and rsi[-1] > self.rsi_ob:
            # Price at upper band + RSI overbought = short
            sl = current_price + atr * self.atr_mult
            tp = bb_mid[-1]
            confidence = 0.55
            if rsi[-1] > self.rsi_ob + 5:
                confidence = 0.65
            return Signal("SELL", symbol, current_price, sl, tp,
                          confidence=confidence, strategy=self.name,
                          timeframe=self.timeframe, volume=self.min_volume,
                          reason=f"mr_sell_bb{rsi[-1]:.0f}")

        elif touched_lower and rsi[-1] < self.rsi_os:
            # Price at lower band + RSI oversold = long
            sl = current_price - atr * self.atr_mult
            tp = bb_mid[-1]
            confidence = 0.55
            if rsi[-1] < self.rsi_os - 5:
                confidence = 0.65
            return Signal("BUY", symbol, current_price, sl, tp,
                          confidence=confidence, strategy=self.name,
                          timeframe=self.timeframe, volume=self.min_volume,
                          reason=f"mr_buy_bb{rsi[-1]:.0f}")

        return None
