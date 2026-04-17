"""
TDD — SwingStrategy sinyal üretimi testleri.

Indicators (compute_ema, compute_rsi) ayrı test edilmiş durumdadır.
Burada sadece strateji mantığı test edilir: hangi koşulda hangi sinyal üretilir.
Bu nedenle indicator fonksiyonları mock'lanır.
"""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from strategy.signals import Signal
from strategy.swing import SwingStrategy

# Test konfigürasyonu — küçük EMA period, gerçekçi RSI thresholdları
_TEST_CFG = {
    "ema_period": 20,
    "rsi_period": 14,
    "rsi_oversold": 35.0,
    "rsi_midline": 60.0,
}

N = 250  # bar sayısı


def _df(n: int = N, base_price: float = 50000.0) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    prices = np.full(n, base_price)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open":   prices * 0.999,
        "high":   prices * 1.001,
        "low":    prices * 0.998,
        "close":  prices,
        "volume": np.ones(n) * 100.0,
    })


def _ema(n: int = N, value: float = 50000.0) -> pd.Series:
    return pd.Series(np.full(n, value, dtype=float))


def _rsi(n: int = N, value: float = 50.0) -> pd.Series:
    return pd.Series(np.full(n, value, dtype=float))


@pytest.fixture
def swing():
    return SwingStrategy(config=_TEST_CFG)


class TestSwingEntry:

    def test_long_entry_signal(self, swing):
        """close > EMA ve RSI oversold ise BUY."""
        df = _df(base_price=52000.0)  # 52000 > EMA(50000)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=30.0)):
            assert swing.generate_signal(df) == Signal.BUY

    def test_trend_filter_blocks_entry(self, swing):
        """close < EMA (downtrend) olduğunda RSI oversold olsa bile HOLD."""
        df = _df(base_price=48000.0)  # 48000 < EMA(50000)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=30.0)):
            assert swing.generate_signal(df) == Signal.HOLD

    def test_rsi_not_oversold_gives_hold(self, swing):
        """Trend bullish ama RSI oversold değil → HOLD."""
        df = _df(base_price=52000.0)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=50.0)):
            assert swing.generate_signal(df) == Signal.HOLD


class TestSwingExit:

    def test_exit_signal_rsi_above_midline(self, swing):
        """RSI midline üzerinde → SELL."""
        df = _df(base_price=52000.0)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=65.0)):
            assert swing.generate_signal(df) == Signal.SELL

    def test_exit_triggers_even_in_downtrend(self, swing):
        """Downtrend'de bile RSI midline üzerindeyse SELL (mevcut pozisyonu kapat)."""
        df = _df(base_price=48000.0)  # close < EMA
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=65.0)):
            assert swing.generate_signal(df) == Signal.SELL


class TestSwingHold:

    def test_no_signal_rsi_midrange(self, swing):
        """RSI ne oversold ne overbought → HOLD."""
        df = _df(base_price=52000.0)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=50.0)):
            assert swing.generate_signal(df) == Signal.HOLD

    def test_rsi_exactly_at_oversold_threshold(self, swing):
        """RSI tam eşikte (35.0) → HOLD (strict less-than)."""
        df = _df(base_price=52000.0)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=35.0)):
            assert swing.generate_signal(df) == Signal.HOLD

    def test_rsi_exactly_at_midline_threshold(self, swing):
        """RSI tam midline'da (60.0) → HOLD (strict greater-than)."""
        df = _df(base_price=52000.0)
        with patch("strategy.swing.compute_ema", return_value=_ema(value=50000.0)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(value=60.0)):
            assert swing.generate_signal(df) == Signal.HOLD


class TestSwingBarEvaluation:

    def test_signal_evaluated_on_last_bar(self, swing):
        """Sinyal son bara (index -1) göre üretilir, önceki barlara göre değil."""
        df = _df()
        ema_vals = np.full(N, 50000.0)
        rsi_vals = np.full(N, 50.0)   # tüm barlar HOLD bölgesinde
        rsi_vals[-1] = 30.0           # sadece son bar oversold

        df_close = df.copy()
        df_close["close"] = 52000.0   # close > EMA her bar

        with patch("strategy.swing.compute_ema", return_value=pd.Series(ema_vals)), \
             patch("strategy.swing.compute_rsi", return_value=pd.Series(rsi_vals)):
            assert swing.generate_signal(df_close) == Signal.BUY

    def test_previous_bars_do_not_affect_signal(self, swing):
        """Önceki barlarda BUY koşulu olsa bile son bar HOLD ise HOLD döner."""
        df = _df()
        ema_vals = np.full(N, 50000.0)
        rsi_vals = np.full(N, 30.0)   # önceki barlar oversold
        rsi_vals[-1] = 50.0           # son bar HOLD bölgesinde

        df_close = df.copy()
        df_close["close"] = 52000.0

        with patch("strategy.swing.compute_ema", return_value=pd.Series(ema_vals)), \
             patch("strategy.swing.compute_rsi", return_value=pd.Series(rsi_vals)):
            assert swing.generate_signal(df_close) == Signal.HOLD


class TestSwingValidation:

    def test_insufficient_data_raises(self, swing):
        """EMA period için yeterli bar yoksa ValueError."""
        df = _df(n=15)  # ema_period=20, 15 < 20 → hata
        with pytest.raises(ValueError, match="yeterli"):
            swing.generate_signal(df)

    def test_exactly_minimum_bars_does_not_raise(self, swing):
        """Tam minimum bar sayısıyla hata fırlatmamalı."""
        min_bars = max(_TEST_CFG["ema_period"], _TEST_CFG["rsi_period"] + 1)
        df = _df(n=min_bars)
        with patch("strategy.swing.compute_ema", return_value=_ema(n=min_bars)), \
             patch("strategy.swing.compute_rsi", return_value=_rsi(n=min_bars)):
            result = swing.generate_signal(df)
        assert result in (Signal.BUY, Signal.SELL, Signal.HOLD)
