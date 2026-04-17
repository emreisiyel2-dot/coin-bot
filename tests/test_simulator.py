"""
TDD — ExecutionSimulator testleri.

Gerçekçi emir dolumu: ATR-bazlı slippage, spread, maker/taker fee.
"""
import pytest
from execution.simulator import ExecutionSimulator, SimulatorConfig, ExecutionResult

PRICE = 50_000.0
ATR = 500.0        # fiyatın %1'i
VOLUME = 1000.0
QTY = 0.02


@pytest.fixture
def sim():
    cfg = SimulatorConfig(
        taker_fee_pct=0.001,
        maker_fee_pct=0.0008,
        spread_pct=0.0004,          # %0.04 toplam, her yön %0.02
        atr_slippage_factor=0.05,   # ATR'ın %5'i slippage
        volume_impact_factor=0.01,
        use_taker=True,
    )
    return ExecutionSimulator(cfg)


# ─── Temel Fill Davranışı ─────────────────────────────────────────────────────

class TestFillDirection:

    def test_buy_fill_price_above_signal(self, sim):
        """BUY: slippage + spread nedeniyle fill fiyatı signal fiyatından yüksek olmalı."""
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        assert result.effective_price > PRICE

    def test_sell_fill_price_below_signal(self, sim):
        """SELL: slippage + spread nedeniyle fill fiyatı signal fiyatından düşük olmalı."""
        result = sim.simulate_fill("SELL", PRICE, ATR, VOLUME, QTY)
        assert result.effective_price < PRICE

    def test_result_is_execution_result(self, sim):
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        assert isinstance(result, ExecutionResult)

    def test_fee_is_positive(self, sim):
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        assert result.fee > 0

    def test_slippage_pct_is_non_negative(self, sim):
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        assert result.slippage_pct >= 0


# ─── ATR-bazlı Volatilite Etkisi ─────────────────────────────────────────────

class TestVolatilitySlippage:

    def test_higher_atr_more_slippage_buy(self, sim):
        """Daha yüksek ATR → daha geniş slippage → daha yüksek fill fiyatı."""
        low_vol  = sim.simulate_fill("BUY", PRICE, 100.0,  VOLUME, QTY)
        high_vol = sim.simulate_fill("BUY", PRICE, 2000.0, VOLUME, QTY)
        assert high_vol.effective_price > low_vol.effective_price

    def test_higher_atr_more_slippage_sell(self, sim):
        low_vol  = sim.simulate_fill("SELL", PRICE, 100.0,  VOLUME, QTY)
        high_vol = sim.simulate_fill("SELL", PRICE, 2000.0, VOLUME, QTY)
        assert high_vol.effective_price < low_vol.effective_price

    def test_zero_atr_only_spread_applied(self, sim):
        """ATR=0 → sadece spread etkisi kalır."""
        result = sim.simulate_fill("BUY", PRICE, 0.0, VOLUME, QTY)
        half_spread = sim._config.spread_pct / 2
        expected = PRICE * (1 + half_spread)
        assert pytest.approx(result.effective_price, rel=1e-6) == expected


# ─── Volume İmpact ────────────────────────────────────────────────────────────

class TestVolumeImpact:

    def test_larger_volume_less_slippage(self, sim):
        """Daha derin piyasa (yüksek volume) → daha düşük volume impact."""
        thin  = sim.simulate_fill("BUY", PRICE, ATR, 10.0,     QTY)
        deep  = sim.simulate_fill("BUY", PRICE, ATR, 100_000.0, QTY)
        assert deep.effective_price < thin.effective_price

    def test_larger_quantity_more_slippage(self, sim):
        """Daha büyük emir → daha fazla volume impact."""
        small = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, 0.001)
        large = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, 10.0)
        assert large.effective_price > small.effective_price


# ─── Spread ──────────────────────────────────────────────────────────────────

class TestSpread:

    def test_spread_cost_is_non_negative(self, sim):
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        assert result.spread_cost >= 0

    def test_zero_spread_config(self):
        """Spread sıfır olunca fiyata katkısı olmamalı."""
        cfg = SimulatorConfig(
            taker_fee_pct=0.001, maker_fee_pct=0.0008,
            spread_pct=0.0,
            atr_slippage_factor=0.0,
            volume_impact_factor=0.0,
            use_taker=True,
        )
        s = ExecutionSimulator(cfg)
        result = s.simulate_fill("BUY", PRICE, 0.0, VOLUME, QTY)
        assert pytest.approx(result.effective_price) == PRICE

    def test_buy_and_sell_symmetric_spread(self, sim):
        """ATR=0 ve vol_impact=0 iken BUY ve SELL spread maliyetleri simetrik olmalı."""
        cfg = SimulatorConfig(
            taker_fee_pct=0.001, maker_fee_pct=0.0008,
            spread_pct=0.0004,
            atr_slippage_factor=0.0,
            volume_impact_factor=0.0,
            use_taker=True,
        )
        s = ExecutionSimulator(cfg)
        buy_result  = s.simulate_fill("BUY",  PRICE, 0.0, VOLUME, QTY)
        sell_result = s.simulate_fill("SELL", PRICE, 0.0, VOLUME, QTY)
        buy_premium  = buy_result.effective_price  - PRICE
        sell_discount = PRICE - sell_result.effective_price
        assert pytest.approx(buy_premium, rel=1e-9) == sell_discount


# ─── Fee Modeli ───────────────────────────────────────────────────────────────

class TestFeeModel:

    def test_taker_fee_applied_by_default(self, sim):
        result = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, QTY)
        expected_fee = result.effective_price * QTY * sim._config.taker_fee_pct
        assert pytest.approx(result.fee, rel=1e-9) == expected_fee

    def test_maker_fee_lower_than_taker(self):
        cfg_taker = SimulatorConfig(taker_fee_pct=0.001, maker_fee_pct=0.0008,
                                    spread_pct=0.0, atr_slippage_factor=0.0,
                                    volume_impact_factor=0.0, use_taker=True)
        cfg_maker = SimulatorConfig(taker_fee_pct=0.001, maker_fee_pct=0.0008,
                                    spread_pct=0.0, atr_slippage_factor=0.0,
                                    volume_impact_factor=0.0, use_taker=False)
        taker_fee = ExecutionSimulator(cfg_taker).simulate_fill("BUY", PRICE, 0.0, VOLUME, QTY).fee
        maker_fee = ExecutionSimulator(cfg_maker).simulate_fill("BUY", PRICE, 0.0, VOLUME, QTY).fee
        assert maker_fee < taker_fee

    def test_fee_scales_with_quantity(self, sim):
        small = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, 0.01)
        large = sim.simulate_fill("BUY", PRICE, ATR, VOLUME, 0.10)
        assert large.fee > small.fee


# ─── Engine Entegrasyonu (smoke) ──────────────────────────────────────────────

class TestEngineIntegration:

    def test_engine_with_simulator_no_crash(self):
        """Engine + simulator birlikte çalıştığında hata fırlatmamalı."""
        import numpy as np
        import pandas as pd
        from portfolio.portfolio import Portfolio
        from strategy.base import BaseStrategy
        from strategy.signals import Signal
        from execution.engine import PaperEngine
        from execution.simulator import ExecutionSimulator, SimulatorConfig

        class _BuyHold(BaseStrategy):
            def __init__(self):
                self._called = False
            def generate_signal(self, df):
                if not self._called:
                    self._called = True
                    return Signal.BUY
                return Signal.HOLD

        n = 30
        timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        prices = np.linspace(50000, 51000, n)
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": prices, "high": prices * 1.001,
            "low": prices * 0.999, "close": prices,
            "volume": np.ones(n) * 500.0,
        })

        portfolio = Portfolio(10_000.0)
        sim = ExecutionSimulator(SimulatorConfig())
        engine = PaperEngine(_BuyHold(), portfolio, "BTC/USDT", 0.10, simulator=sim)
        summary = engine.run(df)
        assert len([t for t in summary.trades if t.action == "OPEN"]) == 1

    def test_engine_with_simulator_buy_price_higher_than_close(self):
        """Simulator aktifken fill fiyatı close fiyatından yüksek olmalı (BUY)."""
        import numpy as np
        import pandas as pd
        from portfolio.portfolio import Portfolio
        from strategy.base import BaseStrategy
        from strategy.signals import Signal
        from execution.engine import PaperEngine
        from execution.simulator import ExecutionSimulator, SimulatorConfig

        class _OneBuy(BaseStrategy):
            def __init__(self): self._done = False
            def generate_signal(self, df):
                if not self._done:
                    self._done = True
                    return Signal.BUY
                return Signal.HOLD

        n = 30
        ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        prices = np.full(n, 50_000.0)
        df = pd.DataFrame({
            "timestamp": ts,
            "open": prices, "high": prices * 1.002,
            "low": prices * 0.998, "close": prices,
            "volume": np.ones(n) * 500.0,
        })

        portfolio = Portfolio(10_000.0)
        sim = ExecutionSimulator(SimulatorConfig())
        engine = PaperEngine(_OneBuy(), portfolio, "BTC/USDT", 0.10, simulator=sim)
        engine.run(df)

        pos = portfolio.positions.get("BTC/USDT")
        assert pos is not None
        # Simulator nedeniyle entry_price close'dan yüksek olmalı
        assert pos.entry_price > 50_000.0
