import logging
from dataclasses import dataclass, field

import pandas as pd

from indicators.indicators import compute_atr
from portfolio.portfolio import Portfolio, PortfolioError
from risk.risk_manager import RiskManager
from strategy.base import BaseStrategy
from strategy.signals import Signal
from execution.simulator import ExecutionSimulator

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str               # "LONG"
    action: str             # "OPEN" | "CLOSE"
    price: float
    quantity: float
    bar_index: int
    timestamp: pd.Timestamp
    realized_pnl: float | None = None  # sadece CLOSE trade'de dolu


@dataclass
class RunSummary:
    trades: list[Trade]
    final_cash: float
    realized_pnl: float
    unrealized_pnl: float
    total_equity: float
    trade_count: int        # tamamlanmış round trip sayısı
    win_count: int
    loss_count: int


class PaperEngine:
    """
    Paper trading orkestratörü.

    Bar-by-bar simülasyon çalıştırır:
      1. df_slice'ı strategy'e verir (candle-close prensibi)
      2. Sinyale göre portfolio'yu günceller
      3. RunSummary döner

    Risk yönetimi, execution simülasyonu ve storage
    bu sınıfın kapsamı dışındadır — ilerleyen aşamalarda eklenir.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        symbol: str,
        position_size_pct: float = 0.15,
        take_profit_pct: float = 0.02,
        stop_loss_pct: float = 0.01,
        simulator: ExecutionSimulator | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self._strategy = strategy
        self._portfolio = portfolio
        self._symbol = symbol
        self._position_size_pct = position_size_pct
        self._take_profit_pct = take_profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._simulator = simulator
        self._risk_manager = risk_manager

    def run(self, df: pd.DataFrame) -> RunSummary:
        trades: list[Trade] = []

        for i in range(len(df)):
            # Candle-close prensibi: sadece kapanmış barlara bak
            df_slice = df.iloc[: i + 1]
            current_bar = df_slice.iloc[-1]
            current_price = float(current_bar["close"])
            current_ts = current_bar["timestamp"]

            # Unrealized PnL'i güncel fiyata çek
            self._portfolio.update_price(self._symbol, current_price)

            # TP/SL kontrolü — açık pozisyon varsa önce fiyat bazlı çıkışa bak
            self._check_tp_sl(current_price, current_ts, i, trades, df_slice)

            # Strategy sinyali al; yetersiz veri → skip
            try:
                signal = self._strategy.generate_signal(df_slice)
            except ValueError:
                logger.debug("Bar %d skip — yetersiz veri", i)
                continue

            if signal == Signal.BUY:
                self._handle_buy(current_price, current_ts, i, trades, df_slice)
            elif signal == Signal.SELL:
                self._handle_sell(current_price, current_ts, i, trades, df_slice)
            # HOLD → hiçbir şey yapma

        return self._build_summary(trades)

    # ── Sinyal İşleyiciler ────────────────────────────────────────────────────

    def _handle_buy(
        self,
        price: float,
        ts: pd.Timestamp,
        bar_index: int,
        trades: list[Trade],
        df_slice: pd.DataFrame | None = None,
    ) -> None:
        if self._risk_manager is not None:
            decision = self._risk_manager.can_open(self._symbol, bar_index=bar_index, bar_ts=ts)
            if not decision.allowed:
                logger.debug(
                    "BUY veto | %s | %s [%s]",
                    self._symbol, decision.reason, decision.code,
                )
                return

        if self._symbol in self._portfolio.positions:
            logger.debug("BUY skip — %s için açık pozisyon var", self._symbol)
            return

        quantity = self._compute_quantity(price)
        if quantity <= 0:
            logger.warning("BUY skip — hesaplanan miktar sıfır veya negatif")
            return

        fill_price = self._fill_price("BUY", price, quantity, df_slice)
        try:
            self._portfolio.open_long(self._symbol, fill_price, quantity)
        except PortfolioError as exc:
            logger.warning("BUY açılamadı: %s", exc)
            return

        if self._risk_manager is not None:
            self._risk_manager.record_open(self._symbol, bar_index=bar_index, bar_ts=ts)

        trades.append(Trade(
            symbol=self._symbol,
            side="LONG",
            action="OPEN",
            price=fill_price,
            quantity=quantity,
            bar_index=bar_index,
            timestamp=ts,
        ))
        logger.info("OPEN | bar=%d | %s | fill=%.2f | qty=%.6f", bar_index, self._symbol, fill_price, quantity)

    def _handle_sell(
        self,
        price: float,
        ts: pd.Timestamp,
        bar_index: int,
        trades: list[Trade],
        df_slice: pd.DataFrame | None = None,
    ) -> None:
        if self._symbol not in self._portfolio.positions:
            logger.debug("SELL skip — %s için açık pozisyon yok", self._symbol)
            return

        pos = self._portfolio.positions[self._symbol]
        fill_price = self._fill_price("SELL", price, pos.quantity, df_slice)
        try:
            realized_pnl = self._portfolio.close_long(self._symbol, fill_price)
        except PortfolioError as exc:
            logger.warning("SELL kapatılamadı: %s", exc)
            return

        if self._risk_manager is not None:
            self._risk_manager.record_close(
                self._symbol, pnl=realized_pnl, bar_index=bar_index, bar_ts=ts
            )

        trades.append(Trade(
            symbol=self._symbol,
            side="LONG",
            action="CLOSE",
            price=fill_price,
            quantity=pos.quantity,
            bar_index=bar_index,
            timestamp=ts,
            realized_pnl=realized_pnl,
        ))
        logger.info(
            "CLOSE | bar=%d | %s | fill=%.2f | pnl=%.2f",
            bar_index, self._symbol, fill_price, realized_pnl,
        )

    # ── Yardımcılar ───────────────────────────────────────────────────────────

    def _check_tp_sl(
        self,
        price: float,
        ts: pd.Timestamp,
        bar_index: int,
        trades: list[Trade],
        df_slice: pd.DataFrame | None = None,
    ) -> None:
        if self._symbol not in self._portfolio.positions:
            return
        pos = self._portfolio.positions[self._symbol]
        entry = pos.entry_price
        if price >= entry * (1 + self._take_profit_pct):
            logger.info(
                "TP HIT | bar=%d | %s | entry=%.2f | current=%.2f | +%.1f%%",
                bar_index, self._symbol, entry, price, self._take_profit_pct * 100,
            )
            self._handle_sell(price, ts, bar_index, trades, df_slice)
        elif price <= entry * (1 - self._stop_loss_pct):
            logger.info(
                "SL HIT | bar=%d | %s | entry=%.2f | current=%.2f | -%.1f%%",
                bar_index, self._symbol, entry, price, self._stop_loss_pct * 100,
            )
            self._handle_sell(price, ts, bar_index, trades, df_slice)

    def _fill_price(
        self,
        side: str,
        signal_price: float,
        quantity: float,
        df_slice: pd.DataFrame | None,
    ) -> float:
        if self._simulator is None or df_slice is None:
            return signal_price
        try:
            atr_series = compute_atr(df_slice, period=14)
            atr = float(atr_series.iloc[-1])
            if atr != atr:  # NaN kontrolü
                atr = signal_price * 0.02
        except (ValueError, Exception):
            atr = signal_price * 0.02
        volume = float(df_slice["volume"].iloc[-1])
        result = self._simulator.simulate_fill(side, signal_price, atr, volume, quantity)
        return result.effective_price

    def _compute_quantity(self, price: float) -> float:
        target_value = self._portfolio.total_equity * self._position_size_pct
        affordable = min(target_value, self._portfolio.cash)
        return affordable / price if price > 0 else 0.0

    def _build_summary(self, trades: list[Trade]) -> RunSummary:
        close_trades = [t for t in trades if t.action == "CLOSE"]
        win_count = sum(1 for t in close_trades if t.realized_pnl is not None and t.realized_pnl > 0)
        loss_count = sum(1 for t in close_trades if t.realized_pnl is not None and t.realized_pnl <= 0)

        return RunSummary(
            trades=trades,
            final_cash=self._portfolio.cash,
            realized_pnl=self._portfolio.realized_pnl,
            unrealized_pnl=self._portfolio.unrealized_pnl,
            total_equity=self._portfolio.total_equity,
            trade_count=len(close_trades),
            win_count=win_count,
            loss_count=loss_count,
        )
