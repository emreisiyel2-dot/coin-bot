"""
Integration smoke test: market_data çıktısı → indicators modülüne sorunsuz girer mi?
"""
import pandas as pd
from unittest.mock import MagicMock

from data.market_data import fetch_ohlcv
from indicators.indicators import compute_ema, compute_rsi, compute_atr, compute_vwap

# 20+ bar — EMA warmup için yeterli
_RAW_BARS = [
    [1704067200000 + i * 3600000, 42000.0 + i * 10, 43000.0 + i * 10,
     41000.0 + i * 10, 42500.0 + i * 10, 100.0 + i]
    for i in range(30)
]


def _mock_exchange():
    ex = MagicMock()
    ex.fetch_ohlcv.return_value = _RAW_BARS
    return ex


def test_market_data_output_feeds_indicators():
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange())

    # Kolon ve dtype doğrulaması
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]"

    # Indicators — hiç exception fırlatmamalı
    ema = compute_ema(df, period=20)
    rsi = compute_rsi(df, period=14)
    atr = compute_atr(df, period=14)
    vwap = compute_vwap(df)

    # Index uyumu
    assert ema.index.equals(df.index)
    assert rsi.index.equals(df.index)
    assert atr.index.equals(df.index)
    assert vwap.index.equals(df.index)

    # NaN olmayan değerler sayısal ve sonlu
    for series in [ema, rsi, atr, vwap]:
        valid = series.dropna()
        assert len(valid) > 0
        assert valid.apply(lambda x: pd.notna(x) and x == x).all()
