import pandas as pd
import numpy as np
import pytest

from indicators.indicators import compute_ema, compute_rsi, compute_atr, compute_vwap


def _utc(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    return df


class TestComputeEma:

    def test_compute_ema_returns_series(self, sample_ohlcv):
        result = compute_ema(_utc(sample_ohlcv), period=20)
        assert isinstance(result, pd.Series)
        assert result.name == "ema_20"

    def test_compute_ema_warmup_nan(self, sample_ohlcv):
        result = compute_ema(_utc(sample_ohlcv), period=20)
        assert result.iloc[:19].isna().all()
        assert pd.notna(result.iloc[19])

    def test_compute_ema_insufficient_data_raises(self, sample_ohlcv):
        small = _utc(sample_ohlcv.iloc[:5].reset_index(drop=True))
        with pytest.raises(ValueError):
            compute_ema(small, period=20)

    def test_compute_ema_index_aligned(self, sample_ohlcv):
        df = _utc(sample_ohlcv)
        result = compute_ema(df, period=20)
        pd.testing.assert_index_equal(result.index, df.index)

    def test_compute_ema_dtype_float64(self, sample_ohlcv):
        result = compute_ema(_utc(sample_ohlcv), period=20)
        assert result.dtype == np.float64


class TestComputeRsi:

    def test_compute_rsi_returns_series(self, sample_ohlcv):
        result = compute_rsi(_utc(sample_ohlcv), period=14)
        assert isinstance(result, pd.Series)
        assert result.name == "rsi"

    def test_compute_rsi_range_0_to_100(self, sample_ohlcv):
        result = compute_rsi(_utc(sample_ohlcv), period=14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_compute_rsi_first_row_nan(self, sample_ohlcv):
        result = compute_rsi(_utc(sample_ohlcv), period=14)
        assert pd.isna(result.iloc[0])

    def test_compute_rsi_insufficient_data_raises(self, sample_ohlcv):
        small = _utc(sample_ohlcv.iloc[:5].reset_index(drop=True))
        with pytest.raises(ValueError):
            compute_rsi(small, period=14)

    def test_compute_rsi_index_aligned(self, sample_ohlcv):
        df = _utc(sample_ohlcv)
        result = compute_rsi(df, period=14)
        pd.testing.assert_index_equal(result.index, df.index)


class TestComputeAtr:

    def test_compute_atr_returns_series(self, sample_ohlcv):
        result = compute_atr(_utc(sample_ohlcv), period=14)
        assert isinstance(result, pd.Series)
        assert result.name == "atr"

    def test_compute_atr_non_negative(self, sample_ohlcv):
        result = compute_atr(_utc(sample_ohlcv), period=14)
        assert (result.dropna() >= 0).all()

    def test_compute_atr_first_row_nan(self, sample_ohlcv):
        result = compute_atr(_utc(sample_ohlcv), period=14)
        assert pd.isna(result.iloc[0])

    def test_compute_atr_insufficient_data_raises(self, sample_ohlcv):
        small = _utc(sample_ohlcv.iloc[:5].reset_index(drop=True))
        with pytest.raises(ValueError):
            compute_atr(small, period=14)

    def test_compute_atr_index_aligned(self, sample_ohlcv):
        df = _utc(sample_ohlcv)
        result = compute_atr(df, period=14)
        pd.testing.assert_index_equal(result.index, df.index)


class TestComputeVwap:

    def test_compute_vwap_returns_series(self, sample_ohlcv):
        result = compute_vwap(_utc(sample_ohlcv))
        assert isinstance(result, pd.Series)
        assert result.name == "vwap"

    def test_compute_vwap_no_nan_normal_data(self, sample_ohlcv):
        result = compute_vwap(_utc(sample_ohlcv))
        assert result.notna().all()

    def test_compute_vwap_zero_volume_gives_nan(self, sample_ohlcv):
        df = _utc(sample_ohlcv.copy())
        df.loc[2, "volume"] = 0.0
        result = compute_vwap(df)
        assert pd.isna(result.iloc[2])

    def test_compute_vwap_empty_df_raises(self):
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        with pytest.raises(ValueError):
            compute_vwap(empty)

    def test_compute_vwap_reasonable_range(self, sample_ohlcv):
        df = _utc(sample_ohlcv)
        result = compute_vwap(df)
        valid = result.dropna()
        assert (valid >= df["close"][valid.index] * 0.80).all()
        assert (valid <= df["close"][valid.index] * 1.20).all()

    def test_compute_vwap_index_aligned(self, sample_ohlcv):
        df = _utc(sample_ohlcv)
        result = compute_vwap(df)
        pd.testing.assert_index_equal(result.index, df.index)
