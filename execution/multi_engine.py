import logging
from dataclasses import dataclass, field

import pandas as pd

from execution.engine import Trade
from indicators.indicators import compute_atr
from portfolio.portfolio import Portfolio, PortfolioError
from risk.risk_manager import RiskManager
from scanner.opportunity_scanner import OpportunityScanner
from strategy.signals import Signal

logger = logging.getLogger(__name__)


@dataclass
class SymbolSummary:
    symbol: str
    trade_count: int        # round-trip sayısı
    realized_pnl: float
    win_count: int
    loss_count: int

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0


@dataclass
class ScannerStats:
    selection_counts: dict[str, int]   # scanner kaç kez seçti
    opened_counts: dict[str, int]      # seçildi + trade açıldı
    veto_counts: dict[str, int]        # seçildi ama risk veto etti

    def conversion_rate(self, symbol: str) -> float:
        selected = self.selection_counts.get(symbol, 0)
        opened = self.opened_counts.get(symbol, 0)
        return opened / selected if selected > 0 else 0.0

    def total_conversion_rate(self) -> float:
        total_selected = sum(self.selection_counts.values())
        total_opened = sum(self.opened_counts.values())
        return total_opened / total_selected if total_selected > 0 else 0.0


@dataclass
class MultiRunSummary:
    per_symbol: dict[str, SymbolSummary]
    trades: list[Trade]
    final_equity: float
    total_trade_count: int          # tüm round-trip'lerin toplamı
    max_concurrent_positions: int   # simülasyon boyunca gözlenen max
    scanner_stats: ScannerStats = field(default_factory=lambda: ScannerStats({}, {}, {}))


class MultiSymbolEngine:
    """
    Multi-symbol paper trading orkestratörü.

    Her bar'da:
      1. Açık pozisyonlarda SELL sinyali kontrol edilir → kapatılır
      2. Yeni fırsatlar scanner ile taranır (BUY sinyali + skor)
      3. Top-N sıralanır, RiskManager onaylayanlar açılır

    symbol_strategies: {symbol: (strategy_instance, df)}
    Tüm DataFrames eşit uzunlukta (aligned) olmalıdır.
    """

    def __init__(
        self,
        symbol_strategies: dict,  # {symbol: (strategy, df)}
        portfolio: Portfolio,
        risk_manager: RiskManager,
        scanner: OpportunityScanner,
        position_size_pct: float = 0.10,
        take_profit_pct: float = 0.02,
        stop_loss_pct: float = 0.01,
        simulator=None,
    ) -> None:
        self._symbol_strategies = symbol_strategies
        self._portfolio = portfolio
        self._risk_manager = risk_manager
        self._scanner = scanner
        self._position_size_pct = position_size_pct
        self._take_profit_pct = take_profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._simulator = simulator

    def run(self) -> MultiRunSummary:
        if not self._symbol_strategies:
            return self._build_summary([], 0)

        # Tüm DF'lerin bar sayısını al (en kısa olana göre)
        bar_count = min(len(df) for _, df in self._symbol_strategies.values())

        trades: list[Trade] = []
        max_concurrent = 0
        selection_counts: dict[str, int] = {}
        opened_counts: dict[str, int] = {}
        veto_counts: dict[str, int] = {}

        for i in range(bar_count):
            # Açık pozisyonları güncelle
            for symbol, (_, df) in self._symbol_strategies.items():
                bar = df.iloc[i]
                self._portfolio.update_price(symbol, float(bar["close"]))

            # 1. TP/SL + SELL sinyali kontrol — açık pozisyonları kapat
            for symbol in list(self._portfolio.positions.keys()):
                if symbol not in self._symbol_strategies:
                    continue
                strategy, df = self._symbol_strategies[symbol]
                df_slice = df.iloc[: i + 1]
                bar_ts = df_slice.iloc[-1]["timestamp"]
                price = float(df_slice.iloc[-1]["close"])

                # Fiyat bazlı çıkış önce kontrol edilir
                if self._tp_sl_triggered(symbol, price, df_slice, i, bar_ts, trades):
                    continue

                try:
                    signal = strategy.generate_signal(df_slice)
                except ValueError:
                    continue
                if signal == Signal.SELL:
                    self._close_position(symbol, df_slice, i, bar_ts, trades)

            # 2. BUY fırsatları tara
            candidates = {}
            for symbol, (strategy, df) in self._symbol_strategies.items():
                if symbol in self._portfolio.positions:
                    continue  # zaten açık
                df_slice = df.iloc[: i + 1]
                candidates[symbol] = (strategy, df_slice)

            if candidates:
                opportunities = self._scanner.scan(candidates)

                for opp in opportunities:
                    symbol = opp.symbol
                    selection_counts[symbol] = selection_counts.get(symbol, 0) + 1

                    strategy, df = self._symbol_strategies[symbol]
                    df_slice = df.iloc[: i + 1]
                    bar = df_slice.iloc[-1]
                    bar_ts = bar["timestamp"]
                    price = float(bar["close"])

                    decision = self._risk_manager.can_open(symbol, bar_index=i, bar_ts=bar_ts)
                    if not decision.allowed:
                        veto_counts[symbol] = veto_counts.get(symbol, 0) + 1
                        logger.debug(
                            "MultiEngine veto | %s | %s [%s]",
                            symbol, decision.reason, decision.code,
                        )
                        continue

                    opened_before = len(trades)
                    self._open_position(symbol, price, i, bar_ts, df_slice, trades)
                    if len(trades) > opened_before:
                        opened_counts[symbol] = opened_counts.get(symbol, 0) + 1

            concurrent = len(self._portfolio.positions)
            if concurrent > max_concurrent:
                max_concurrent = concurrent

        scanner_stats = ScannerStats(
            selection_counts=selection_counts,
            opened_counts=opened_counts,
            veto_counts=veto_counts,
        )
        return self._build_summary(trades, max_concurrent, scanner_stats)

    # ── İşlem açma / kapama ───────────────────────────────────────────────────

    def _open_position(
        self,
        symbol: str,
        price: float,
        bar_index: int,
        bar_ts: pd.Timestamp,
        df_slice: pd.DataFrame,
        trades: list[Trade],
    ) -> None:
        qty = self._compute_quantity(price)
        if qty <= 0:
            return
        fill_price = self._fill_price("BUY", price, qty, df_slice)
        try:
            self._portfolio.open_long(symbol, fill_price, qty)
        except PortfolioError as exc:
            logger.warning("MultiEngine OPEN başarısız: %s", exc)
            return

        self._risk_manager.record_open(symbol, bar_index=bar_index, bar_ts=bar_ts)
        trades.append(Trade(
            symbol=symbol, side="LONG", action="OPEN",
            price=fill_price, quantity=qty,
            bar_index=bar_index, timestamp=bar_ts,
        ))
        logger.info("OPEN | bar=%d | %s | fill=%.4f | qty=%.6f", bar_index, symbol, fill_price, qty)

    def _close_position(
        self,
        symbol: str,
        df_slice: pd.DataFrame,
        bar_index: int,
        bar_ts: pd.Timestamp,
        trades: list[Trade],
    ) -> None:
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return
        price = float(df_slice.iloc[-1]["close"])
        fill_price = self._fill_price("SELL", price, pos.quantity, df_slice)
        try:
            realized_pnl = self._portfolio.close_long(symbol, fill_price)
        except PortfolioError as exc:
            logger.warning("MultiEngine CLOSE başarısız: %s", exc)
            return

        self._risk_manager.record_close(symbol, pnl=realized_pnl, bar_index=bar_index, bar_ts=bar_ts)
        trades.append(Trade(
            symbol=symbol, side="LONG", action="CLOSE",
            price=fill_price, quantity=pos.quantity,
            bar_index=bar_index, timestamp=bar_ts,
            realized_pnl=realized_pnl,
        ))
        logger.info("CLOSE | bar=%d | %s | fill=%.4f | pnl=%.2f", bar_index, symbol, fill_price, realized_pnl)

    # ── Yardımcılar ───────────────────────────────────────────────────────────

    def _tp_sl_triggered(
        self,
        symbol: str,
        price: float,
        df_slice: pd.DataFrame,
        bar_index: int,
        bar_ts: pd.Timestamp,
        trades: list[Trade],
    ) -> bool:
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return False
        entry = pos.entry_price
        if price >= entry * (1 + self._take_profit_pct):
            logger.info(
                "TP HIT | bar=%d | %s | entry=%.4f | current=%.4f | +%.1f%%",
                bar_index, symbol, entry, price, self._take_profit_pct * 100,
            )
            self._close_position(symbol, df_slice, bar_index, bar_ts, trades)
            return True
        if price <= entry * (1 - self._stop_loss_pct):
            logger.info(
                "SL HIT | bar=%d | %s | entry=%.4f | current=%.4f | -%.1f%%",
                bar_index, symbol, entry, price, self._stop_loss_pct * 100,
            )
            self._close_position(symbol, df_slice, bar_index, bar_ts, trades)
            return True
        return False

    def _compute_quantity(self, price: float) -> float:
        target = self._portfolio.total_equity * self._position_size_pct
        affordable = min(target, self._portfolio.cash)
        return affordable / price if price > 0 else 0.0

    def _fill_price(self, side: str, price: float, qty: float, df_slice: pd.DataFrame) -> float:
        if self._simulator is None:
            return price
        try:
            atr_series = compute_atr(df_slice, period=14)
            atr = float(atr_series.iloc[-1])
            if atr != atr:
                atr = price * 0.02
        except Exception:
            atr = price * 0.02
        volume = float(df_slice["volume"].iloc[-1])
        return self._simulator.simulate_fill(side, price, atr, volume, qty).effective_price

    def _build_summary(self, trades: list[Trade], max_concurrent: int, scanner_stats: ScannerStats | None = None) -> MultiRunSummary:
        # Per-symbol agregasyon
        per_symbol: dict[str, SymbolSummary] = {}
        for symbol in self._symbol_strategies:
            sym_closes = [t for t in trades if t.symbol == symbol and t.action == "CLOSE"]
            wins  = sum(1 for t in sym_closes if t.realized_pnl is not None and t.realized_pnl > 0)
            losses = sum(1 for t in sym_closes if t.realized_pnl is not None and t.realized_pnl <= 0)
            pnl   = sum(t.realized_pnl for t in sym_closes if t.realized_pnl is not None)
            per_symbol[symbol] = SymbolSummary(
                symbol=symbol,
                trade_count=len(sym_closes),
                realized_pnl=pnl,
                win_count=wins,
                loss_count=losses,
            )

        total_trades = sum(s.trade_count for s in per_symbol.values())

        return MultiRunSummary(
            per_symbol=per_symbol,
            trades=trades,
            final_equity=self._portfolio.total_equity,
            total_trade_count=total_trades,
            max_concurrent_positions=max_concurrent,
            scanner_stats=scanner_stats or ScannerStats({}, {}, {}),
        )
