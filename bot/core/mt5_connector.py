"""MetaTrader 5 connector — handles connection, account info, and order execution."""
import time
import pandas as pd
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from bot.utils.logger import logger

# Try to import MT5; provide fallback for systems without it
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed — using demo/simulation mode")


# Timeframe constants (fallback values when MT5 not available)
if MT5_AVAILABLE:
    TIMEFRAMES_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
else:
    TIMEFRAMES_MAP = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }


class MT5Connector:
    """Interface for MetaTrader 5 trading operations."""

    TIMEFRAMES = TIMEFRAMES_MAP

    def __init__(self, config: dict):
        self.cfg = config.get("mt5", {})
        self.trading_cfg = config.get("trading", {})
        self._connected = False
        self.account_info = None
        self._demo_mode = not MT5_AVAILABLE
        # Start with $30 demo balance to simulate flipping a small account
        self._sim_balance = 30.0
        self._sim_equity = 30.0
        self._sim_positions = []

        if MT5_AVAILABLE and self.cfg.get("login", 0) == 0:
            self._demo_mode = True
            logger.info("No MT5 credentials provided — running in DEMO simulation mode")

    def connect(self) -> bool:
        """Connect to MT5 terminal."""
        if self._demo_mode:
            logger.info(f"🤖 DEMO MODE — simulated trading (starting balance: ${self._sim_balance:.2f})")
            self._connected = True
            self.account_info = {
                "login": 0,
                "balance": self._sim_balance,
                "equity": self._sim_equity,
                "margin_free": self._sim_balance,
                "currency": "USD",
                "name": "Demo Account",
                "server": "Simulator",
                "leverage": 100,
            }
            return True

        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 package not installed")
            return False

        try:
            mt5_path = self.cfg.get("path") or None
            if mt5_path:
                initialized = mt5.initialize(path=mt5_path, timeout=self.cfg.get("timeout", 60000))
            else:
                initialized = mt5.initialize(timeout=self.cfg.get("timeout", 60000))

            if not initialized:
                logger.error(f"MT5 init failed: {mt5.last_error()}")
                return False

            login = self.cfg.get("login", 0)
            password = self.cfg.get("password", "")
            server = self.cfg.get("server", "")

            if login and password and server:
                authorized = mt5.login(login, password=password, server=server)
                if not authorized:
                    logger.error(f"MT5 login failed: {mt5.last_error()}")
                    mt5.shutdown()
                    return False

            self._connected = True
            self._update_account_info()
            logger.info(f"✅ Connected to MT5 — Account: {self.account_info.get('name', 'N/A')}, "
                        f"Balance: ${self.account_info.get('balance', 0):.2f}")
            return True

        except Exception as e:
            logger.error(f"MT5 connection error: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MT5."""
        if self._connected and MT5_AVAILABLE and not self._demo_mode:
            mt5.shutdown()
        self._connected = False
        logger.info("Disconnected from MT5")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Account Info ────────────────────────────────────────────
    def _update_account_info(self) -> None:
        """Refresh account info from MT5."""
        if self._demo_mode:
            return
        try:
            info = mt5.account_info()
            if info:
                self.account_info = {
                    "login": info.login,
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin_free": info.margin_free,
                    "currency": info.currency,
                    "name": info.name,
                    "server": info.server,
                    "leverage": info.leverage,
                }
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")

    def get_balance(self) -> float:
        """Get current account balance."""
        if self._demo_mode:
            return self._sim_balance
        if self.account_info:
            return self.account_info.get("balance", 0.0)
        return 0.0

    def get_equity(self) -> float:
        """Get current equity."""
        if self._demo_mode:
            return self._sim_equity
        if self.account_info:
            return self.account_info.get("equity", 0.0)
        return 0.0

    def get_peak_equity(self) -> float:
        """Get peak equity."""
        return max(self.get_equity(), self.get_balance())

    # ── Market Data ─────────────────────────────────────────────
    def get_rates(self, symbol: str, timeframe: str, count: int = 100) -> Optional[pd.DataFrame]:
        """Fetch historical rates as a pandas DataFrame."""
        tf = self.TIMEFRAMES.get(timeframe, 1)

        if self._demo_mode:
            return self._generate_simulated_rates(symbol, count)

        if not MT5_AVAILABLE:
            return self._generate_simulated_rates(symbol, count)

        try:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                logger.warning(f"No rates for {symbol} {timeframe}")
                return None

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            return df
        except Exception as e:
            logger.error(f"Failed to get rates for {symbol}: {e}")
            return None

    def _generate_simulated_rates(self, symbol: str, count: int) -> pd.DataFrame:
        """Generate semi-realistic simulated price data with trends and pullbacks."""
        import numpy as np
        np.random.seed(hash(symbol + str(time.time() // 300)) % (2**31))

        base_price = {"EURUSD": 1.0850, "GBPUSD": 1.2650,
                      "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)

        now = int(time.time())
        times = [now - (count - i) * 60 for i in range(count)]

        # Create trending data with pullbacks (more realistic)
        prices = []
        price = base_price
        trend = np.random.choice([-1, 1]) * 0.0001
        for i in range(count):
            # Occasionally change trend direction
            if np.random.random() < 0.05:
                trend = np.random.choice([-1, 1]) * 0.0001
            # Pullback every ~15 bars
            if i > 0 and i % 15 == 0:
                price -= trend * 8 * np.random.random()
            else:
                price += trend + np.random.normal(0, 0.0003)
            if i > 0 and i % 25 == 0:
                # Strong directional move
                price += trend * 5
            prices.append(max(price, base_price * 0.9))

        df = pd.DataFrame({
            "time": pd.to_datetime(times, unit="s"),
            "open": prices,
            "high": [p * (1 + abs(np.random.normal(0, 0.0008))) for p in prices],
            "low": [p * (1 - abs(np.random.normal(0, 0.0008))) for p in prices],
            "close": prices,
            "tick_volume": [max(1, int(np.random.gamma(3, 100))) for _ in range(count)],
            "spread": [np.random.randint(1, 5) for _ in range(count)],
        })
        return df

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Get symbol information."""
        if self._demo_mode or not MT5_AVAILABLE:
            return {
                "symbol": symbol,
                "spread": 2,
                "digits": 5,
                "point": 0.00001 if "JPY" not in symbol else 0.001,
                "trade_mode": 0,
            }
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            "symbol": info.name,
            "spread": info.spread,
            "digits": info.digits,
            "point": info.point,
            "trade_mode": info.trade_mode,
        }

    # ── Order Execution ─────────────────────────────────────────
    def place_order(self, symbol: str, action: str, volume: float,
                    price: float = 0.0, sl: float = 0.0, tp: float = 0.0,
                    deviation: int = 10, comment: str = "") -> Optional[int]:
        """Place a market order. Returns ticket number or None."""
        if self._demo_mode:
            return self._sim_place_order(symbol, action, volume, price, sl, tp, comment)

        if not self._connected or not MT5_AVAILABLE:
            logger.error("Not connected to MT5")
            return None

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price if price > 0 else (
                mt5.symbol_info_tick(symbol).ask if action == "BUY"
                else mt5.symbol_info_tick(symbol).bid
            ),
            "sl": sl,
            "tp": tp,
            "deviation": deviation,
            "magic": self.trading_cfg.get("magic_number", 20240609),
            "comment": comment or self.trading_cfg.get("comment", "AIBot"),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        try:
            result = mt5.order_send(request)
            if result and result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"Order failed: {result.comment} (retcode={result.retcode})")
                return None
            if result:
                logger.info(f"✅ Order placed: {action} {volume} {symbol} @ ticket {result.order}")
                return result.order
            logger.error("Order send returned None")
            return None
        except Exception as e:
            logger.error(f"Order exception: {e}")
            return None

    def _sim_place_order(self, symbol: str, action: str, volume: float,
                         price: float, sl: float, tp: float, comment: str) -> int:
        """Simulated order placement."""
        import random
        ticket = random.randint(10000, 99999)
        pos = {
            "ticket": ticket,
            "symbol": symbol,
            "action": action,
            "volume": volume,
            "price": price or self._sim_price(symbol),
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "time": datetime.now(),
        }
        self._sim_positions.append(pos)
        # Simulate balance change (small random walk)
        pnl_change = random.uniform(-1.0, 2.0) * volume * 100
        self._sim_balance = max(0.01, self._sim_balance + pnl_change)
        self._sim_equity = self._sim_balance
        logger.info(f"🧪 [DEMO] Order {ticket}: {action} {volume} {symbol} "
                    f"@ {pos['price']:.5f} | Balance: ${self._sim_balance:.2f}")
        return ticket

    def _sim_price(self, symbol: str) -> float:
        """Get a simulated current price for a symbol."""
        base = {"EURUSD": 1.0850, "GBPUSD": 1.2650,
                "USDJPY": 152.50, "XAUUSD": 2350.0}.get(symbol, 1.0)
        import random
        return base + random.uniform(-0.01, 0.01)

    def close_position(self, ticket: int, deviation: int = 10) -> bool:
        """Close an open position by ticket number."""
        if self._demo_mode:
            before = len(self._sim_positions)
            self._sim_positions = [p for p in self._sim_positions if p.get("ticket") != ticket]
            if len(self._sim_positions) < before:
                logger.info(f"🧪 [DEMO] Position {ticket} closed")
                return True
            return False

        if not MT5_AVAILABLE:
            return False

        try:
            pos = mt5.positions_get(ticket=ticket)
            if not pos or len(pos) == 0:
                return False
            pos = pos[0]

            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": ticket,
                "price": close_price,
                "deviation": deviation,
                "magic": self.trading_cfg.get("magic_number", 20240609),
                "comment": "close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Position {ticket} closed")
                return True
            return False
        except Exception as e:
            logger.error(f"Close exception: {e}")
            return False

    def close_all_positions(self) -> int:
        """Close all open positions."""
        positions = self.get_open_positions()
        closed = 0
        for pos in positions:
            if self.close_position(pos["ticket"]):
                closed += 1
        return closed

    # ── Position Management ─────────────────────────────────────
    def get_open_positions(self, symbol: str = "") -> List[Dict]:
        """Get open positions, optionally filtered by symbol."""
        if self._demo_mode:
            positions = self._sim_positions
            if symbol:
                positions = [p for p in positions if p["symbol"] == symbol]
            return [
                {
                    "ticket": p["ticket"],
                    "symbol": p["symbol"],
                    "action": p["action"],
                    "volume": p["volume"],
                    "price": p["price"],
                    "sl": p.get("sl", 0),
                    "tp": p.get("tp", 0),
                    "profit": 0.0,
                    "comment": p.get("comment", ""),
                }
                for p in positions
            ]

        if not MT5_AVAILABLE or not self._connected:
            return []

        try:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "action": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": p.volume,
                    "price": p.price_open,
                    "sl": p.sl or 0.0,
                    "tp": p.tp or 0.0,
                    "profit": p.profit,
                    "swap": p.swap,
                    "comment": p.comment,
                    "time": datetime.fromtimestamp(p.time),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def position_count(self, symbol: str = "") -> int:
        """Count open positions."""
        return len(self.get_open_positions(symbol))
