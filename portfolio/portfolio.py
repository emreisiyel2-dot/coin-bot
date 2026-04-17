import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class PortfolioError(Exception):
    pass


@dataclass
class Position:
    symbol: str
    side: str           # "LONG" | "SHORT" (SHORT ileride eklenecek)
    quantity: float
    entry_price: float
    entry_cost: float   # quantity * entry_price * (1 + commission + slippage)
    current_price: float = field(default=0.0)

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.entry_cost


class Portfolio:
    """
    Sanal portföy — nakit, açık pozisyonlar ve PnL takibi.

    Şimdilik sadece LONG desteklenir.
    SHORT için open_short / close_short metotları ileride eklenebilir
    — mevcut yapı bunu engellemez.
    """

    def __init__(
        self,
        initial_cash: float,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.0005,
    ) -> None:
        self._cash: float = initial_cash
        self._commission_pct = commission_pct
        self._slippage_pct = slippage_pct
        self._positions: dict[str, Position] = {}
        self._realized_pnl: float = 0.0
        self._current_prices: dict[str, float] = {}

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def total_equity(self) -> float:
        position_value = sum(p.market_value for p in self._positions.values())
        return self._cash + position_value

    # ── İşlemler ──────────────────────────────────────────────────────────────

    def open_long(self, symbol: str, price: float, quantity: float) -> None:
        """Long pozisyon aç. Komisyon + slippage giriş maliyetine eklenir."""
        if symbol in self._positions:
            raise PortfolioError(
                f"open_long: {symbol} için zaten açık pozisyon var — "
                "önce mevcut pozisyonu kapat."
            )

        entry_cost = quantity * price * (1 + self._commission_pct + self._slippage_pct)

        if entry_cost > self._cash:
            raise PortfolioError(
                f"open_long: Yetersiz nakit — "
                f"gerekli {entry_cost:.2f}, mevcut {self._cash:.2f}"
            )

        self._cash -= entry_cost
        self._positions[symbol] = Position(
            symbol=symbol,
            side="LONG",
            quantity=quantity,
            entry_price=price,
            entry_cost=entry_cost,
            current_price=price,
        )
        self._current_prices[symbol] = price

        logger.info(
            "OPEN LONG | %s | qty=%.6f | price=%.2f | cost=%.2f | cash=%.2f",
            symbol, quantity, price, entry_cost, self._cash,
        )

    def close_long(self, symbol: str, price: float) -> float:
        """Long pozisyonu kapat. Realized PnL döner."""
        if symbol not in self._positions:
            raise PortfolioError(f"close_long: Pozisyon bulunamadı — {symbol}")

        pos = self._positions[symbol]
        exit_proceeds = pos.quantity * price * (1 - self._commission_pct - self._slippage_pct)
        trade_pnl = exit_proceeds - pos.entry_cost

        self._cash += exit_proceeds
        self._realized_pnl += trade_pnl
        del self._positions[symbol]

        logger.info(
            "CLOSE LONG | %s | qty=%.6f | exit_price=%.2f | pnl=%.2f | cash=%.2f",
            symbol, pos.quantity, price, trade_pnl, self._cash,
        )

        return trade_pnl

    def reduce_long(self, symbol: str, price: float, quantity: float) -> float:
        """Long pozisyonun bir kısmını kapat. Realized PnL döner."""
        if symbol not in self._positions:
            raise PortfolioError(f"reduce_long: Pozisyon bulunamadı — {symbol}")
        pos = self._positions[symbol]
        if quantity >= pos.quantity:
            return self.close_long(symbol, price)

        fraction = quantity / pos.quantity
        partial_cost = pos.entry_cost * fraction
        exit_proceeds = quantity * price * (1 - self._commission_pct - self._slippage_pct)
        trade_pnl = exit_proceeds - partial_cost

        self._cash += exit_proceeds
        self._realized_pnl += trade_pnl
        pos.quantity -= quantity
        pos.entry_cost -= partial_cost

        logger.info(
            "REDUCE LONG | %s | qty=%.6f | exit_price=%.2f | pnl=%.2f | remaining=%.6f",
            symbol, quantity, price, trade_pnl, pos.quantity,
        )
        return trade_pnl

    def update_price(self, symbol: str, price: float) -> None:
        """Açık pozisyonun güncel fiyatını güncelle (unrealized PnL yansır)."""
        self._current_prices[symbol] = price
        if symbol in self._positions:
            self._positions[symbol].current_price = price
            logger.debug("PRICE UPDATE | %s | price=%.2f", symbol, price)
