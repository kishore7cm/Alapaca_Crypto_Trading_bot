import logging

from config import MAX_POSITIONS

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, api, risk_pct: float = 0.05, max_positions: int = MAX_POSITIONS):
        self.api = api
        self.risk_pct = risk_pct
        self.max_positions = max_positions

    def get_portfolio_value(self) -> float:
        return float(self.api.get_account().portfolio_value)

    def get_open_positions(self) -> list:
        return self.api.list_positions()

    def position_count(self) -> int:
        return len(self.get_open_positions())

    def already_holding(self, symbol: str) -> bool:
        try:
            pos = self.api.get_position(symbol.replace("/", ""))
            return float(pos.qty) != 0
        except Exception:
            return False

    def trade_dollar_amount(self) -> float:
        return self.get_portfolio_value() * self.risk_pct

    def can_trade(self) -> bool:
        account = self.api.get_account()
        if account.trading_blocked:
            logger.warning("Account trading is blocked")
            return False
        if float(account.cash) < 100:
            logger.warning("Insufficient cash to trade")
            return False
        if self.position_count() >= self.max_positions:
            logger.info("Max positions reached (%d)", self.max_positions)
            return False
        return True
