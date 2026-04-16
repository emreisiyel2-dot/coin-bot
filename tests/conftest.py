import pandas as pd
import numpy as np
import pytest


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """250 bar OHLCV verisi — EMA-200 warmup için yeterli. OHLCV kanonik kuralı sağlanır."""
    np.random.seed(42)
    n = 250
    close = 50000 + np.cumsum(np.random.randn(n) * 100)
    low  = close - np.abs(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 100)
    open_ = np.clip(close - np.random.randn(n) * 50, low, high)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": np.random.uniform(100, 500, n),
    })
    return df
