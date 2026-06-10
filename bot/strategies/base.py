"""Base class for all trading strategies."""
from abc import ABC, abstractmethod
from typing import Optional, Tuple
import pandas as pd
from bot.utils.logger import logger


class Signal:
    """Trading signal produced by a strategy."""

    def __init__(self, action: str, symbol: str, price: float,
                 sl: float, tp: float, confidence: float = 0.5,
                 strategy: str = "", timeframe: str = "M1",
                 volume: float = 0.01, reason: str = ""):
        self.action = action          # "BUY" or "SELL"
        self.symbol = symbol
        self.price = price
        self.sl = sl
        self.tp = tp
        self.confidence = min(max(confidence, 0.0), 1.0)
        self.strategy = strategy
        self.timeframe = timeframe
        self.volume = volume
        self.reason = reason

    def __repr__(self) -> str:
        return (f"[{self.strategy}] {self.action} {self.symbol} @ {self.price:.5f} "
                f"SL:{self.sl:.5f} TP:{self.tp:.5f} (conf:{self.confidence:.1%})")

    def is_valid(self) -> bool:
        """Check if the signal has all required fields."""
        if not self.action or not self.symbol:
            return False
        if self.price <= 0 or self.sl <= 0 or self.tp <= 0:
            return False
        if self.confidence <= 0:
            return False
        return True


class BaseStrategy(ABC):
    """Abstract base strategy that all strategies must implement."""

    def __init__(self, name: str, config: dict, connector):
        self.name = name
        self.cfg = config
        self.connector = connector
        self.enabled = True
        self.weight = 1.0
        self.timeframe = "M1"
        self.params = {}

    @abstractmethod
    def analyze(self, symbol: str) -> Optional[Signal]:
        """Analyze a symbol and return a trading signal or None.
        
        Args:
            symbol: Trading instrument symbol
            
        Returns:
            Signal object if a trade should be made, None otherwise
        """
        pass

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators. Override for strategy-specific indicators.
        
        Args:
            df: Raw OHLCV DataFrame
            
        Returns:
            DataFrame with added indicator columns
        """
        return df

    def get_rates(self, symbol: str, count: int = 100) -> Optional[pd.DataFrame]:
        """Fetch and prepare rate data."""
        df = self.connector.get_rates(symbol, self.timeframe, count)
        if df is None or len(df) < count // 2:
            return None
        return self.calculate_indicators(df)

    def check_spread(self, symbol: str, max_spread: int = 30) -> bool:
        """Check if spread is acceptable."""
        info = self.connector.get_symbol_info(symbol)
        if info is None:
            return False
        if max_spread > 0 and info.get("spread", 0) > max_spread:
            return False
        return True

    def compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Compute Average True Range from OHLC data."""
        import numpy as np
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(abs(high[1:] - close[:-1]),
                                   abs(low[1:] - close[:-1])))
        atr = np.mean(tr[-period:]) if len(tr) >= period else np.mean(tr)
        return max(atr, 1e-10)
