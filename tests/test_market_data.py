import pytest
import pandas as pd
import ccxt
from unittest.mock import MagicMock, patch
from data.market_data import fetch_ohlcv, MarketDataError

RAW_BAR = lambda ts, o, h, l, c, v: [ts, o, h, l, c, v]

VALID_RAW = [
    RAW_BAR(1704067200000, 42000.0, 43000.0, 41000.0, 42500.0, 100.0),
    RAW_BAR(1704070800000, 42500.0, 44000.0, 42000.0, 43000.0, 120.0),
    RAW_BAR(1704074400000, 43000.0, 43500.0, 42500.0, 43200.0, 90.0),
]


def _mock_exchange(raw=VALID_RAW):
    ex = MagicMock()
    ex.fetch_ohlcv.return_value = raw
    return ex


def test_fetch_ohlcv_returns_standard_dataframe():
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange())
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert df.index.tolist() == list(range(len(df)))


def test_fetch_ohlcv_timestamps_are_utc_aware():
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange())
    for ts in df["timestamp"]:
        assert ts.tzinfo is not None
        assert str(ts.tzinfo) == "UTC"


def test_fetch_ohlcv_sorted_ascending():
    reversed_raw = list(reversed(VALID_RAW))
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange(reversed_raw))
    assert df["timestamp"].is_monotonic_increasing


def test_fetch_ohlcv_empty_response_raises():
    with pytest.raises(MarketDataError, match="Boş"):
        fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange([]))


def test_fetch_ohlcv_duplicate_timestamps_deduplicated():
    dup_raw = [
        RAW_BAR(1704067200000, 42000.0, 43000.0, 41000.0, 42500.0, 100.0),
        RAW_BAR(1704067200000, 42100.0, 43100.0, 41100.0, 42600.0, 110.0),  # duplicate
        RAW_BAR(1704070800000, 42500.0, 44000.0, 42000.0, 43000.0, 120.0),
    ]
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange(dup_raw))
    assert df["timestamp"].duplicated().sum() == 0
    assert len(df) == 2


def test_fetch_ohlcv_nan_rows_dropped():
    nan_raw = [
        RAW_BAR(1704067200000, 42000.0, 43000.0, 41000.0, None, 100.0),  # NaN close
        RAW_BAR(1704070800000, 42500.0, 44000.0, 42000.0, 43000.0, 120.0),
    ]
    df = fetch_ohlcv("BTC/USDT", "1h", exchange=_mock_exchange(nan_raw))
    assert df["close"].isna().sum() == 0
    assert len(df) == 1


def test_fetch_ohlcv_network_error_retries():
    ex = MagicMock()
    ex.fetch_ohlcv.side_effect = [
        ccxt.NetworkError("timeout"),
        ccxt.NetworkError("timeout"),
        VALID_RAW,
    ]
    with patch("data.market_data.time.sleep"):
        df = fetch_ohlcv("BTC/USDT", "1h", exchange=ex)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_fetch_ohlcv_network_error_exhausted_raises():
    ex = MagicMock()
    ex.fetch_ohlcv.side_effect = ccxt.NetworkError("persistent failure")
    with patch("data.market_data.time.sleep"):
        with pytest.raises(MarketDataError, match="Ağ"):
            fetch_ohlcv("BTC/USDT", "1h", exchange=ex)
