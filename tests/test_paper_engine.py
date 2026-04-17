"""
TDD — PaperEngine testleri.

Engine'in strategy + portfolio entegrasyonunu bar-by-bar doğru
orkestre ettiğini ve candle-close prensibini ihlal etmediğini doğrular.

MockStrategy → strateji mantığı burada test edilmez; sadece engine davranışı.
"""
import pandas as pd
import numpy as np
import pytest

from portfolio.portfolio import Portfolio
from strategy.base import BaseStrategy
from strategy.signals import Signal
from execution.engine import PaperEngine, RunSummary, Trade

INITIAL_CASH = 10_000.0
COMM = 0.001
SLIP = 0.0
SYM = "BTC/USDT"
PRICE = 50_000.0
SIZE_PCT = 0.10   # equity'nin %10'u


# ─── Test yardımcıları ────────────────────────────────────────────────────────

def _df(n: int, price: float = PRICE) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    prices = np.full(n, float(price))
    return pd.DataFrame({
        "timestamp": timestamps,
        "open":   prices * 0.999,
        "high":   prices * 1.001,
        "low":    prices * 0.998,
        "close":  prices,
        "volume": np.ones(n) * 100.0,
    })


def _df_with_prices(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    arr = np.array(prices, dtype=float)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open":   arr, "high": arr,
        "low":    arr, "close": arr,
        "volume": np.ones(n) * 100.0,
    })


class _FixedStrategy(BaseStrategy):
    """Sabit sinyal sekansı döner; liste bitince HOLD."""
    def __init__(self, signals: list[Signal]) -> None:
        self._iter = iter(signals)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return next(self._iter, Signal.HOLD)


class _SpyStrategy(BaseStrategy):
    """Kendisine gelen df_slice uzunluklarını kaydeder."""
    def __init__(self) -> None:
        self.slice_lengths: list[int] = []

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        self.slice_lengths.append(len(df))
        return Signal.HOLD


@pytest.fixture
def portfolio():
    return Portfolio(initial_cash=INITIAL_CASH, commission_pct=COMM, slippage_pct=SLIP)


# ─── BUY Davranışı ────────────────────────────────────────────────────────────

class TestBuySignal:

    def test_buy_opens_position(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY]), portfolio, SYM, SIZE_PCT)
        engine.run(_df(5))
        assert SYM in portfolio.positions

    def test_buy_records_open_trade(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        assert len([t for t in summary.trades if t.action == "OPEN"]) == 1

    def test_buy_trade_has_correct_price(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        trade = next(t for t in summary.trades if t.action == "OPEN")
        assert pytest.approx(trade.price) == PRICE

    def test_buy_trade_has_bar_index_zero(self, portfolio):
        """İlk bar'da BUY sinyali → bar_index == 0."""
        engine = PaperEngine(_FixedStrategy([Signal.BUY]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        trade = next(t for t in summary.trades if t.action == "OPEN")
        assert trade.bar_index == 0

    def test_repeated_buy_no_duplicate_position(self, portfolio):
        """BUY sinyali ardı ardına gelirse tek pozisyon açılır."""
        engine = PaperEngine(_FixedStrategy([Signal.BUY] * 5), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        open_trades = [t for t in summary.trades if t.action == "OPEN"]
        assert len(open_trades) == 1


# ─── HOLD Davranışı ───────────────────────────────────────────────────────────

class TestHoldSignal:

    def test_hold_produces_no_trades(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.HOLD] * 10), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(10))
        assert len(summary.trades) == 0

    def test_hold_leaves_cash_unchanged(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.HOLD] * 5), portfolio, SYM, SIZE_PCT)
        engine.run(_df(5))
        assert portfolio.cash == INITIAL_CASH


# ─── SELL Davranışı ───────────────────────────────────────────────────────────

class TestSellSignal:

    def test_sell_with_position_closes(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY, Signal.SELL]), portfolio, SYM, SIZE_PCT)
        engine.run(_df(5))
        assert SYM not in portfolio.positions

    def test_sell_with_position_records_close_trade(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY, Signal.SELL]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        close_trades = [t for t in summary.trades if t.action == "CLOSE"]
        assert len(close_trades) == 1

    def test_sell_close_trade_has_realized_pnl(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY, Signal.SELL]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        trade = next(t for t in summary.trades if t.action == "CLOSE")
        assert trade.realized_pnl is not None

    def test_sell_without_position_no_crash(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.SELL] * 3), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        assert len(summary.trades) == 0

    def test_sell_without_position_cash_unchanged(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.SELL]), portfolio, SYM, SIZE_PCT)
        engine.run(_df(5))
        assert portfolio.cash == INITIAL_CASH


# ─── RunSummary Doğruluğu ─────────────────────────────────────────────────────

class TestRunSummary:

    def test_summary_final_cash_no_trades(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.HOLD] * 5), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        assert summary.final_cash == INITIAL_CASH

    def test_summary_trade_count_counts_round_trips(self, portfolio):
        """Trade count = tamamlanmış round trip sayısı (CLOSE sayısı)."""
        engine = PaperEngine(
            _FixedStrategy([Signal.BUY, Signal.SELL, Signal.BUY, Signal.SELL]),
            portfolio, SYM, SIZE_PCT,
        )
        summary = engine.run(_df(10))
        assert summary.trade_count == 2

    def test_summary_win_loss_count(self, portfolio):
        """Kârlı kapanış → win, zararlı kapanış → loss."""
        # bar0: BUY @50000, bar1: SELL @55000 (kâr), bar2: BUY @55000, bar3: SELL @45000 (zarar)
        df = _df_with_prices([50_000.0, 55_000.0, 55_000.0, 45_000.0, 45_000.0])
        engine = PaperEngine(
            _FixedStrategy([Signal.BUY, Signal.SELL, Signal.BUY, Signal.SELL, Signal.HOLD]),
            portfolio, SYM, SIZE_PCT,
        )
        summary = engine.run(df)
        assert summary.win_count == 1
        assert summary.loss_count == 1

    def test_summary_realized_pnl_matches_portfolio(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY, Signal.SELL]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        assert summary.realized_pnl == portfolio.realized_pnl

    def test_summary_equity_matches_portfolio(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.BUY]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        assert summary.total_equity == portfolio.total_equity

    def test_summary_is_runsummary_instance(self, portfolio):
        engine = PaperEngine(_FixedStrategy([Signal.HOLD]), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(3))
        assert isinstance(summary, RunSummary)


# ─── Candle-Close / Lookahead Koruması ───────────────────────────────────────

class TestCandleClose:

    def test_no_lookahead_slice_grows_one_per_bar(self, portfolio):
        """Her bar için strategy'e giden df_slice bir öncekinden tam 1 bar fazladır."""
        spy = _SpyStrategy()
        engine = PaperEngine(spy, portfolio, SYM, SIZE_PCT)
        n = 10
        engine.run(_df(n))
        assert spy.slice_lengths == list(range(1, n + 1))

    def test_buy_at_bar_3_records_correct_bar_index(self, portfolio):
        """Bar 3'teki BUY sinyali trade'i bar_index=3 olarak kaydeder."""
        signals = [Signal.HOLD, Signal.HOLD, Signal.HOLD, Signal.BUY]
        engine = PaperEngine(_FixedStrategy(signals), portfolio, SYM, SIZE_PCT)
        summary = engine.run(_df(5))
        open_trades = [t for t in summary.trades if t.action == "OPEN"]
        assert len(open_trades) == 1
        assert open_trades[0].bar_index == 3

    def test_buy_at_bar_3_uses_bar_3_close_price(self, portfolio):
        """Bar 3'teki BUY, bar 3'ün close fiyatını kullanır."""
        prices = [50_000.0, 51_000.0, 52_000.0, 53_000.0, 54_000.0]
        df = _df_with_prices(prices)
        signals = [Signal.HOLD, Signal.HOLD, Signal.HOLD, Signal.BUY]
        engine = PaperEngine(_FixedStrategy(signals), portfolio, SYM, SIZE_PCT)
        summary = engine.run(df)
        trade = next(t for t in summary.trades if t.action == "OPEN")
        assert pytest.approx(trade.price) == 53_000.0  # bar 3'ün close'u
