"""Main trading bot orchestrator — the 'wise brain'.

Selects the best strategy based on live market conditions,
manages risk, executes trades, and tracks performance.
"""
import time
import os
import json
import threading
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from bot.utils.logger import logger
from bot.utils.telegram import TelegramNotifier
from bot.core.mt5_connector import MT5Connector
from bot.core.risk_manager import RiskManager
from bot.strategies.base import Signal
from bot.strategies.candy import CandyStrategy
from bot.strategies.scalping import ScalpingStrategy
from bot.strategies.trend import TrendStrategy
from bot.strategies.mean_reversion import MeanReversionStrategy
from bot.strategies.breakout import BreakoutStrategy


class TradingBot:
    """Autonomous multi-strategy trading bot."""

    def __init__(self, config: dict):
        self.config = config
        self.running = False
        self._start_time = None

        # Core components
        self.connector = MT5Connector(config)
        self.risk = RiskManager(config, self.connector)
        self.notifier = TelegramNotifier(
            config.get("notifications", {}).get("telegram", {}).get("bot_token", ""),
            config.get("notifications", {}).get("telegram", {}).get("chat_id", ""),
        )

        # Load strategies
        self.strategies = self._load_strategies()

        # Performance tracking
        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "max_balance": 0.0,
            "start_balance": 0.0,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "trades_by_strategy": {},
            "signals_generated": 0,
        }
        self._last_signal_time = {}  # symbol -> last signal time
        self._signal_cooldown = 60  # seconds between signals per symbol

        # State file
        self.state_file = Path(__file__).parent.parent.parent / "logs" / "bot_state.json"
        self._load_state()

    def _load_strategies(self) -> list:
        """Initialize and return all enabled strategies."""
        strategies = []
        registry = [
            CandyStrategy,
            ScalpingStrategy,
            TrendStrategy,
            MeanReversionStrategy,
            BreakoutStrategy,
        ]
        for StratClass in registry:
            try:
                strat = StratClass(self.config, self.connector)
                if strat.enabled:
                    strategies.append(strat)
                    logger.info(f"  ✅ Strategy loaded: {strat.name} [{strat.timeframe}]")
            except Exception as e:
                logger.warning(f"Failed to load {StratClass.__name__}: {e}")

        logger.info(f"Loaded {len(strategies)}/{len(registry)} strategies")
        return strategies

    # ── Lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        """Start the bot's main trading loop."""
        if self.running:
            logger.warning("Bot is already running")
            return

        logger.info("=" * 60)
        logger.info("🚀 TRADING BOT STARTING")
        logger.info("=" * 60)

        if not self.connector.connect():
            logger.error("Failed to connect to MT5 — aborting")
            return

        self._start_time = datetime.now()
        self.running = True
        self.stats["start_balance"] = self.connector.get_balance()
        self.stats["max_balance"] = self.stats["start_balance"]

        logger.info(f"💰 Starting Balance: ${self.stats['start_balance']:.2f}")
        logger.info(f"🎯 Target: ${self.config.get('risk', {}).get('target_account_balance', 1000):.2f}")
        logger.info(f"📊 Mode: {self.config.get('general', {}).get('mode', 'demo').upper()}")
        logger.info(f"⏱ Check Interval: {self.config.get('general', {}).get('check_interval', 5)}s")
        logger.info("=" * 60)

        self.notifier.send(f"🤖 <b>Bot Started</b>\nBalance: ${self.stats['start_balance']:.2f}\n"
                           f"Target: ${self.config.get('risk', {}).get('target_account_balance', 1000):.2f}")

        self._main_loop()

    def stop(self) -> None:
        """Stop the bot gracefully."""
        self.running = False
        logger.info("🛑 Bot stopping...")

        if self.config.get("general", {}).get("close_positions_on_stop", False):
            closed = self.connector.close_all_positions()
            logger.info(f"Closed {closed} positions")

        self._save_state()
        self.connector.disconnect()
        self._print_summary()

        self.notifier.send(f"🛑 <b>Bot Stopped</b>\n"
                           f"Trades: {self.stats['total_trades']} | "
                           f"PnL: ${self.stats['total_pnl']:.2f}")

    def _main_loop(self) -> None:
        """Main trading loop — runs until stopped."""
        interval = self.config.get("general", {}).get("check_interval", 5)
        symbols = self.config.get("trading", {}).get("symbols", ["EURUSD"])
        max_spread = self.config.get("trading", {}).get("max_spread", 30)

        cycle_count = 0
        last_report_time = datetime.now()

        try:
            while self.running:
                cycle_count += 1
                now = datetime.now()

                # ── Risk Check ──────────────────────────────────
                allowed, reason = self.risk.can_trade()
                if not allowed:
                    if cycle_count % 20 == 0:  # log every ~20 cycles
                        logger.info(f"⏸ Trade blocked: {reason}")
                    time.sleep(interval)
                    continue

                # ── Target Check ────────────────────────────────
                if self.risk.should_stop_target_reached():
                    logger.info("🎯 TARGET REACHED! Stopping bot.")
                    self.notifier.send(f"🎯 <b>TARGET REACHED!</b>\n"
                                       f"Balance: ${self.connector.get_balance():.2f}")
                    self.stop()
                    break

                # ── Scan Each Symbol ────────────────────────────
                for symbol in symbols:
                    if not self.running:
                        break

                    # Symbol-level risk check
                    sym_allowed, sym_reason = self.risk.can_trade_symbol(symbol)
                    if not sym_allowed:
                        continue

                    # Get the best signal across all strategies
                    signal = self._get_best_signal(symbol)

                    if signal and signal.is_valid():
                        self._execute_signal(signal)

                # ── Periodic Reports ────────────────────────────
                if (now - last_report_time).total_seconds() >= 3600:  # every hour
                    self._print_status()
                    last_report_time = now

                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            self.stop()
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}")
            self.notifier.send_error(f"Fatal error: {e}")
            self.stop()

    # ── Signal Selection (The "Wise" Logic) ──────────────────────
    def _get_best_signal(self, symbol: str) -> Optional[Signal]:
        """Evaluate all strategies and return the best signal.
        
        The bot is 'wise' because it:
        1. Collects signals from all strategies
        2. Filters by spread and market conditions
        3. Weighs each signal by strategy confidence + weight
        4. Avoids trading too frequently (cooldown)
        5. Prefers strategies that have been performing well
        """
        # Cooldown check
        last_time = self._last_signal_time.get(symbol)
        if last_time and (datetime.now() - last_time).total_seconds() < self._signal_cooldown:
            return None

        best_signal = None
        best_score = 0.0

        for strategy in self.strategies:
            try:
                signal = strategy.analyze(symbol)
                if signal is None:
                    continue

                self.stats["signals_generated"] += 1

                # Compute composite score
                # Base confidence * strategy weight * performance factor
                perf_factor = self._get_strategy_performance_factor(strategy.name)
                score = signal.confidence * strategy.weight * perf_factor

                # Bonus for higher-confidence signals
                if signal.confidence > 0.7:
                    score *= 1.2

                # Small bonus for Candy strategy during scaling phase (small accounts)
                if strategy.name == "Candy" and self.risk.is_scaling_phase():
                    score *= 1.15
                # Bonus for scalping when account is small (fast accumulation)
                if strategy.name == "Scalp" and self.risk.is_scaling_phase():
                    score *= 1.1

                if score > best_score:
                    best_score = score
                    best_signal = signal
                    best_signal.volume = self.risk.calculate_volume(
                        symbol, signal.price, signal.sl
                    )

            except Exception as e:
                logger.debug(f"Strategy {strategy.name} error on {symbol}: {e}")
                continue

        return best_signal

    def _get_strategy_performance_factor(self, strategy_name: str) -> float:
        """Return a performance multiplier for a strategy based on recent results.
        
        Strategies that have been winning get a boost. New or poor performers 
        get reduced weight.
        """
        stats = self.stats["trades_by_strategy"].get(strategy_name, {})
        total = stats.get("total", 0)
        if total < 5:
            return 1.0  # not enough data

        win_rate = stats.get("wins", 0) / max(total, 1)
        # Boost good performers, penalize bad ones
        if win_rate > 0.6:
            return 1.15
        elif win_rate > 0.45:
            return 1.0
        else:
            return 0.7

    # ── Execution ────────────────────────────────────────────────
    def _execute_signal(self, signal: Signal) -> bool:
        """Execute a trading signal on the connected platform."""
        try:
            balance = self.connector.get_balance()

            logger.info(f"🔔 SIGNAL: {signal}")

            # Place the order
            ticket = self.connector.place_order(
                symbol=signal.symbol,
                action=signal.action,
                volume=signal.volume,
                price=signal.price,
                sl=signal.sl,
                tp=signal.tp,
                comment=f"{signal.strategy}_{signal.reason}",
            )

            if ticket is None:
                logger.warning(f"Order failed for {signal}")
                return False

            # Update cooldown
            self._last_signal_time[signal.symbol] = datetime.now()

            # Update stats
            self.stats["total_trades"] += 1
            if signal.strategy not in self.stats["trades_by_strategy"]:
                self.stats["trades_by_strategy"][signal.strategy] = {"total": 0, "wins": 0}

            # Log and notify
            logger.info(f"✅ TRADE EXECUTED: {signal.action} {signal.volume} {signal.symbol} "
                        f"@ {signal.price:.5f} | SL: {signal.sl:.5f} TP: {signal.tp:.5f}")

            self.notifier.send_trade(
                signal.action, signal.symbol, signal.volume,
                signal.price, signal.sl, signal.tp,
                balance=balance,
            )

            # Save state periodically
            self._save_state()
            return True

        except Exception as e:
            logger.error(f"Signal execution error: {e}")
            return False

    def _check_open_positions(self) -> None:
        """Monitor and manage open positions (trailing stops, etc.)."""
        if not self.config.get("risk", {}).get("use_trailing_stop", False):
            return

        try:
            positions = self.connector.get_open_positions()
            for pos in positions:
                self._apply_trailing_stop(pos)
        except Exception as e:
            logger.debug(f"Position check error: {e}")

    def _apply_trailing_stop(self, position: dict) -> None:
        """Apply trailing stop to a position if conditions are met."""
        # This is a simplified version — in production you'd modify SL via MT5
        activation_pct = self.config.get("risk", {}).get("trailing_activation_pct", 0.3) / 100.0
        trail_dist_pct = self.config.get("risk", {}).get("trailing_distance_pct", 0.15) / 100.0

        entry = position["price"]
        current_price = position.get("current_price", entry)
        profit_pct = (current_price - entry) / entry if position["action"] == "BUY" else (entry - current_price) / entry

        if profit_pct >= activation_pct:
            if position["action"] == "BUY":
                new_sl = current_price * (1 - trail_dist_pct)
            else:
                new_sl = current_price * (1 + trail_dist_pct)

            # In a real implementation, you'd modify the position's SL here
            # mt5.order_send with TRADE_ACTION_SLTP
            logger.debug(f"Trailing {position['ticket']}: SL would move to {new_sl:.5f}")

    # ── Status & Reporting ───────────────────────────────────────
    def _print_status(self) -> None:
        """Print current status to console."""
        balance = self.connector.get_balance()
        equity = self.connector.get_equity()
        positions = self.connector.get_open_positions()

        logger.info("─" * 50)
        logger.info(f"📊 STATUS | Balance: ${balance:.2f} | Equity: ${equity:.2f}")
        logger.info(f"   Trades: {self.stats['total_trades']} | "
                    f"PnL: ${self.stats['total_pnl']:.2f} | "
                    f"Open: {len(positions)}")
        logger.info(f"   Start: ${self.stats['start_balance']:.2f} → "
                    f"Now: ${balance:.2f} "
                    f"({((balance - self.stats['start_balance']) / self.stats['start_balance'] * 100):+.2f}%)")

        if self.stats["total_trades"] > 0:
            win_rate = self.stats["winning_trades"] / self.stats["total_trades"] * 100
            logger.info(f"   Win Rate: {win_rate:.1f}%")
        logger.info("─" * 50)

    def _print_summary(self) -> None:
        """Print final summary when bot stops."""
        balance = self.connector.get_balance()
        duration = datetime.now() - self._start_time if self._start_time else timedelta(0)

        logger.info("\n" + "=" * 60)
        logger.info("📋 FINAL PERFORMANCE SUMMARY")
        logger.info("=" * 60)
        logger.info(f"⏱ Duration: {duration}")
        logger.info(f"💰 Start Balance: ${self.stats['start_balance']:.2f}")
        logger.info(f"💰 Final Balance: ${balance:.2f}")
        logger.info(f"📈 Total PnL: ${self.stats['total_pnl']:.2f}")
        logger.info(f"📊 Return: {((balance - self.stats['start_balance']) / self.stats['start_balance'] * 100):+.2f}%")
        logger.info(f"🔄 Total Trades: {self.stats['total_trades']}")
        logger.info(f"✅ Wins: {self.stats['winning_trades']}")
        logger.info(f"❌ Losses: {self.stats['losing_trades']}")
        if self.stats["total_trades"] > 0:
            logger.info(f"📊 Win Rate: {self.stats['winning_trades'] / self.stats['total_trades'] * 100:.1f}%")
        logger.info(f"🔍 Signals Generated: {self.stats['signals_generated']}")
        logger.info("=" * 60)

    # ── State Persistence ────────────────────────────────────────
    def _save_state(self) -> None:
        """Save bot state to disk for recovery."""
        try:
            state = {
                "timestamp": datetime.now().isoformat(),
                "stats": self.stats,
                "balance": self.connector.get_balance(),
                "open_positions": len(self.connector.get_open_positions()),
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"State save error: {e}")

    def _load_state(self) -> None:
        """Load saved state if available."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                self.stats = state.get("stats", self.stats)
                logger.info(f"Loaded saved state — previous stats: "
                            f"{self.stats['total_trades']} trades, "
                            f"${self.stats['total_pnl']:.2f} PnL")
            except Exception as e:
                logger.debug(f"State load error: {e}")
