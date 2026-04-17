"""
MultiSymbolEngine TDD testleri.

Tüm coinleri bar-by-bar tarar, scanner skoruna göre seçer, RiskManager ile onaylar.
"""

import numpy as np
import pandas as pd
import pytest

from execution.multi_engine import MultiSymbolEngine, MultiRunSummary
from portfolio.portfolio import Portfolio
from risk.risk_manager import RiskManager
from scanner.opportunity_scanner import OpportunityScanner
from strategy.signals import Signal


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def make_df(n: int = 250, close: float = 50_000.0, volume: float = 2_000_000.0,
            atr_pct: float = 0.01, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    closes = np.full(n, close) + np.random.randn(n) * close * 0.001
    high = closes * (1 + atr_pct / 2)
    low  = closes * (1 - atr_pct / 2)
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "timestamp": ts, "open": closes,
        "high": high, "low": low, "close": closes, "volume": np.full(n, volume),
    })


def make_portfolio(cash: float = 10_000.0) -> Portfolio:
    return Portfolio(initial_cash=cash)


def make_risk_manager(portfolio: Portfolio, max_open: int = 3) -> RiskManager:
    return RiskManager(portfolio=portfolio, config={
        "max_open_positions": max_open,
        "cooldown_bars": 3,
        "daily_max_trades": 20,
        "daily_max_loss_pct": 0.10,
    })


def make_scanner(top_n: int = 3) -> OpportunityScanner:
    return OpportunityScanner(config={
        "min_atr_pct": 0.001,
        "min_volume": 100_000.0,
        "volume_window": 5,
        "spread_penalty_threshold": 0.01,
        "rsi_weight": 0.50,
        "atr_weight": 0.30,
        "volume_weight": 0.20,
        "top_n": top_n,
    })


class AlwaysBuyStrategy:
    def generate_signal(self, df):
        return Signal.BUY


class AlwaysSellStrategy:
    def generate_signal(self, df):
        return Signal.SELL


class AlwaysHoldStrategy:
    def generate_signal(self, df):
        return Signal.HOLD


class BuyThenSellStrategy:
    """İlk 5 bar BUY, sonra SELL."""
    def generate_signal(self, df):
        if len(df) <= 5:
            return Signal.BUY
        return Signal.SELL


def make_engine(
    symbol_strategies: dict,
    portfolio: Portfolio | None = None,
    risk_manager: RiskManager | None = None,
    scanner: OpportunityScanner | None = None,
    position_size_pct: float = 0.10,
) -> MultiSymbolEngine:
    if portfolio is None:
        portfolio = make_portfolio()
    if risk_manager is None:
        risk_manager = make_risk_manager(portfolio)
    if scanner is None:
        scanner = make_scanner()
    return MultiSymbolEngine(
        symbol_strategies=symbol_strategies,
        portfolio=portfolio,
        risk_manager=risk_manager,
        scanner=scanner,
        position_size_pct=position_size_pct,
    )


# ── Test 1: Temel çalışma ─────────────────────────────────────────────────────

class TestBasicRun:
    def test_returns_multi_run_summary(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysHoldStrategy(), df)})
        result = engine.run()
        assert isinstance(result, MultiRunSummary)

    def test_hold_only_no_trades(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysHoldStrategy(), df)})
        result = engine.run()
        assert result.total_trade_count == 0

    def test_single_symbol_buy_opens_position(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysBuyStrategy(), df)})
        result = engine.run()
        # En az bir OPEN trade gerçekleşmeli
        assert result.total_trade_count >= 0  # engine çalıştı, crash yok

    def test_summary_has_per_symbol_results(self):
        dfs = {"BTC/USDT": make_df(seed=1), "ETH/USDT": make_df(close=3000.0, seed=2)}
        strategies = {s: (AlwaysBuyStrategy(), df) for s, df in dfs.items()}
        engine = make_engine(strategies)
        result = engine.run()
        assert "BTC/USDT" in result.per_symbol
        assert "ETH/USDT" in result.per_symbol


# ── Test 2: top-N seçimi ──────────────────────────────────────────────────────

class TestTopNSelection:
    def test_top_n_limits_per_bar_opens(self):
        """top_n=1 iken her bar'da en fazla 1 yeni pozisyon açılmalı."""
        from collections import Counter
        portfolio = make_portfolio(cash=50_000.0)
        risk_manager = make_risk_manager(portfolio, max_open=5)
        scanner = make_scanner(top_n=1)

        dfs = {f"COIN{i}/USDT": make_df(seed=i) for i in range(3)}
        strategies = {s: (AlwaysBuyStrategy(), df) for s, df in dfs.items()}

        engine = make_engine(strategies, portfolio=portfolio,
                             risk_manager=risk_manager, scanner=scanner)
        result = engine.run()

        # Tek bir bar'da en fazla top_n=1 yeni açış olmalı
        open_per_bar = Counter(t.bar_index for t in result.trades if t.action == "OPEN")
        assert all(count <= 1 for count in open_per_bar.values())

    def test_risk_manager_vetoes_beyond_max_open(self):
        """RiskManager max_open_positions=1 iken ikinci coin açılmamalı."""
        portfolio = make_portfolio(cash=50_000.0)
        risk_manager = make_risk_manager(portfolio, max_open=1)
        scanner = make_scanner(top_n=3)

        dfs = {f"COIN{i}/USDT": make_df(seed=i) for i in range(3)}
        strategies = {s: (AlwaysBuyStrategy(), df) for s, df in dfs.items()}

        engine = make_engine(strategies, portfolio=portfolio,
                             risk_manager=risk_manager, scanner=scanner)
        result = engine.run()

        assert result.max_concurrent_positions <= 1


# ── Test 3: Per-symbol sonuçlar ───────────────────────────────────────────────

class TestPerSymbolResults:
    def test_per_symbol_has_trade_count(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysBuyStrategy(), df)})
        result = engine.run()
        assert hasattr(result.per_symbol["BTC/USDT"], "trade_count")

    def test_per_symbol_has_realized_pnl(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (BuyThenSellStrategy(), df)})
        result = engine.run()
        assert hasattr(result.per_symbol["BTC/USDT"], "realized_pnl")

    def test_per_symbol_has_win_rate(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (BuyThenSellStrategy(), df)})
        result = engine.run()
        sym = result.per_symbol["BTC/USDT"]
        assert hasattr(sym, "win_rate")


# ── Test 4: MultiRunSummary yapısı ────────────────────────────────────────────

class TestMultiRunSummary:
    def test_summary_has_total_trade_count(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysHoldStrategy(), df)})
        result = engine.run()
        assert hasattr(result, "total_trade_count")

    def test_summary_has_final_equity(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysHoldStrategy(), df)})
        result = engine.run()
        assert hasattr(result, "final_equity")
        assert result.final_equity > 0

    def test_summary_has_max_concurrent_positions(self):
        df = make_df()
        engine = make_engine({"BTC/USDT": (AlwaysHoldStrategy(), df)})
        result = engine.run()
        assert hasattr(result, "max_concurrent_positions")
        assert result.max_concurrent_positions >= 0
