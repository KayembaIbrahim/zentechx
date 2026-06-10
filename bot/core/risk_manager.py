"""Risk management — position sizing, daily limits, drawdown control."""
from datetime import date
from bot.utils.logger import logger


class RiskManager:
    """Enforces risk rules and computes safe position sizes."""

    def __init__(self, config: dict, connector):
        self.cfg = config.get("risk", {})
        self.connector = connector
        self._daily_start_balance = connector.get_balance()
        self._daily_loss_today = 0.0
        self._peak_equity = connector.get_equity()
        self._trades_today = 0
        self._today = date.today()

    def _reset_daily(self):
        """Reset daily counters if it's a new day."""
        today = date.today()
        if today != self._today:
            self._daily_start_balance = self.connector.get_balance()
            self._daily_loss_today = 0.0
            self._trades_today = 0
            self._today = today

    def can_trade(self) -> tuple[bool, str]:
        """Check if we're allowed to trade based on risk limits.
        
        Returns:
            (allowed: bool, reason: str)
        """
        self._reset_daily()
        balance = self.connector.get_balance()

        # Minimum balance check
        min_bal = self.cfg.get("min_account_balance", 10.0)
        if balance < min_bal:
            return False, f"Balance ${balance:.2f} below minimum ${min_bal:.2f}"

        # Max daily loss check
        max_loss_pct = self.cfg.get("max_daily_loss_pct", 5.0)
        max_loss = self._daily_start_balance * (max_loss_pct / 100.0)
        if self._daily_loss_today >= max_loss:
            return False, f"Daily loss limit reached (${self._daily_loss_today:.2f} / ${max_loss:.2f})"

        # Max drawdown check
        max_dd_pct = self.cfg.get("max_drawdown_pct", 15.0)
        equity = self.connector.get_equity()
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - equity) / self._peak_equity * 100
            if dd_pct > max_dd_pct:
                return False, f"Max drawdown exceeded ({dd_pct:.1f}% > {max_dd_pct}%)"

        # Max positions check
        max_pos = self.cfg.get("max_positions_total", 5)
        if self.connector.position_count() >= max_pos:
            return False, f"Max positions reached ({max_pos})"

        # Daily trade count soft-limit (safety)
        max_trades = self.cfg.get("max_trades_per_day", 50)
        if self._trades_today >= max_trades:
            return False, f"Max daily trades reached ({max_trades})"

        return True, "OK"

    def can_trade_symbol(self, symbol: str) -> tuple[bool, str]:
        """Check if we can open another position on a given symbol."""
        max_per_symbol = self.cfg.get("max_positions_per_symbol", 1)
        current = self.connector.position_count(symbol)
        if current >= max_per_symbol:
            return False, f"Max positions for {symbol} ({max_per_symbol})"
        return True, "OK"

    def calculate_volume(self, symbol: str, entry_price: float,
                         stop_loss: float, balance: float = None) -> float:
        """Calculate position size based on risk-per-trade.
        
        Uses percentage-based risk model:
            Volume = (Balance * risk_pct) / (|entry - SL| * lot_value)
        """
        if balance is None:
            balance = self.connector.get_balance()

        risk_pct = self.cfg.get("risk_per_trade_pct", 1.0) / 100.0
        risk_amount = balance * risk_pct

        # Get symbol info for pip/point value
        info = self.connector.get_symbol_info(symbol)
        if info is None:
            logger.warning(f"Cannot get symbol info for {symbol}, using min volume")
            return 0.01

        # Calculate SL distance in price units
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            logger.warning("SL distance is zero, using minimum volume")
            return 0.01

        # Standard lot = 100,000 units. Pip value varies by pair.
        # Simplified: for forex, 1 pip = 0.0001 (or 0.01 for JPY pairs)
        pip_value = 0.0001 if "JPY" not in symbol else 0.01
        point = info.get("point", pip_value)

        # Convert SL distance to pips
        sl_pips = sl_distance / point / 10 if "JPY" not in symbol else sl_distance / point

        if sl_pips <= 0:
            return 0.01

        # Standard pip value for 1 lot on a standard account: $10 per pip
        # This varies by pair but is a reasonable approximation
        pip_value_per_lot = 10.0

        lot_size = risk_amount / (sl_pips * pip_value_per_lot)

        # Round to standard lot sizes
        lot_size = max(0.01, round(lot_size / 0.01) * 0.01)
        lot_size = min(lot_size, 10.0)  # safety cap

        return lot_size

    def get_stop_loss_price(self, action: str, entry_price: float,
                            atr: float) -> float:
        """Calculate SL price based on ATR."""
        sl_pct = self.cfg.get("stop_loss_pct", 0.5) / 100.0
        atr_mult = 1.5

        # Use the larger of ATR-based or percentage-based distance
        atr_distance = atr * atr_mult
        pct_distance = entry_price * sl_pct
        distance = max(atr_distance, pct_distance)

        if action == "BUY":
            return entry_price - distance
        return entry_price + distance

    def get_take_profit_price(self, action: str, entry_price: float,
                              atr: float, risk_reward: float = 2.0) -> float:
        """Calculate TP price. Uses risk-reward ratio from SL distance."""
        if action == "BUY":
            sl_distance = entry_price * (self.cfg.get("stop_loss_pct", 0.5) / 100.0)
            return entry_price + sl_distance * risk_reward
        sl_distance = entry_price * (self.cfg.get("stop_loss_pct", 0.5) / 100.0)
        return entry_price - sl_distance * risk_reward

    def record_trade_result(self, pnl: float) -> None:
        """Record a completed trade's PnL for daily tracking."""
        self._trades_today += 1
        if pnl < 0:
            self._daily_loss_today += abs(pnl)
        # Update peak equity
        equity = self.connector.get_equity()
        self._peak_equity = max(self._peak_equity, equity)

    def get_daily_stats(self) -> dict:
        """Get daily trading statistics."""
        self._reset_daily()
        return {
            "start_balance": self._daily_start_balance,
            "current_balance": self.connector.get_balance(),
            "daily_loss": self._daily_loss_today,
            "trades_today": self._trades_today,
            "peak_equity": self._peak_equity,
        }

    def should_stop_target_reached(self) -> bool:
        """Check if account target has been reached."""
        target = self.cfg.get("target_account_balance", 1000.0)
        balance = self.connector.get_balance()
        if balance >= target:
            logger.info(f"🎯 Target reached! Balance ${balance:.2f} >= ${target:.2f}")
            return True
        return False

    def is_scaling_phase(self) -> bool:
        """Check if we're in the aggressive scaling phase (< $100)."""
        balance = self.connector.get_balance()
        return balance < 100.0
