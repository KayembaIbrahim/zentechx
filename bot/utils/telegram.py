"""Telegram notification sender for trade alerts."""
import requests
from bot.utils.logger import logger


class TelegramNotifier:
    """Send trade notifications via Telegram bot."""

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if self.enabled:
            self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str) -> bool:
        """Send a plain message."""
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"Telegram send failed: {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    def send_trade(self, action: str, symbol: str, volume: float,
                   price: float, sl: float, tp: float, pnl: float = 0,
                   balance: float = 0) -> None:
        """Send a formatted trade notification."""
        emoji_map = {
            "BUY": "🟢", "SELL": "🔴",
            "CLOSE_BUY": "🔽", "CLOSE_SELL": "🔼",
        }
        emoji = emoji_map.get(action, "⚪")
        lines = [
            f"{emoji} <b>{action}</b> {symbol}",
            f"• Volume: {volume}",
            f"• Price: {price:.5f}",
            f"• SL: {sl:.5f} | TP: {tp:.5f}",
        ]
        if pnl:
            sign = "+" if pnl >= 0 else ""
            lines.append(f"• PnL: {sign}{pnl:.2f}")
        if balance:
            lines.append(f"• Balance: ${balance:.2f}")
        self.send("\n".join(lines))

    def send_error(self, error_msg: str) -> None:
        """Send an error alert."""
        self.send(f"❌ <b>Error:</b> {error_msg}")

    def send_report(self, text: str) -> None:
        """Send a daily or summary report."""
        self.send(f"📊 <b>Report</b>\n{text}")
