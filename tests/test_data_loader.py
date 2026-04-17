"""
DataLoader TDD testleri.

fetch_ohlcv_range: pagination, duplicate koruması, sonsuz döngü guard.
DataLoader.load: multi-symbol çekme, timestamp alignment, kayıp raporu.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.data_loader import DataLoader, AlignmentReport, DataLoaderError
from data.market_data import fetch_ohlcv_range, MarketDataError


# ── Sahte exchange ────────────────────────────────────────────────────────────

def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)


def _make_candles(start: str, n: int, step_min: int = 60) -> list:
    """OHLCV listesi üretir: [timestamp_ms, o, h, l, c, v]"""
    base = pd.Timestamp(start, tz="UTC")
    result = []
    for i in range(n):
        ts_ms = int((base + pd.Timedelta(minutes=step_min * i)).timestamp() * 1000)
        result.append([ts_ms, 100.0, 101.0, 99.0, 100.0, 1_000_000.0])
    return result


class FakeExchange:
    """Her çağrıda sıradaki chunk'ı döner. Çağrı log'u tutar."""

    def __init__(self, chunks: list[list]):
        self._chunks = list(chunks)
        self.call_count = 0
        self.call_args: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        self.call_count += 1
        self.call_args.append({"symbol": symbol, "timeframe": timeframe,
                               "since": since, "limit": limit})
        if not self._chunks:
            return []
        return self._chunks.pop(0)


class InfiniteExchange:
    """Her zaman aynı chunk'ı döner → sonsuz döngü riski."""

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        return _make_candles("2024-01-01", limit, step_min=60)


# ── fetch_ohlcv_range testleri ────────────────────────────────────────────────

class TestFetchOhlcvRange:

    def test_returns_dataframe(self):
        chunk = _make_candles("2024-01-01", 100)
        ex = FakeExchange([chunk, []])
        df = fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=_ms("2024-01-05"),
            exchange=ex,
        )
        assert isinstance(df, pd.DataFrame)

    def test_has_ohlcv_columns(self):
        chunk = _make_candles("2024-01-01", 50)
        ex = FakeExchange([chunk, []])
        df = fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=_ms("2024-01-05"),
            exchange=ex,
        )
        assert set(["timestamp", "open", "high", "low", "close", "volume"]).issubset(df.columns)

    def test_pagination_makes_multiple_calls(self):
        """500'den fazla bar varsa birden fazla API çağrısı yapılmalı."""
        chunk1 = _make_candles("2024-01-01 00:00", 500)
        chunk2 = _make_candles("2024-01-21 20:00", 200)
        ex = FakeExchange([chunk1, chunk2, []])
        fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=_ms("2024-02-01"),
            exchange=ex,
        )
        assert ex.call_count >= 2

    def test_duplicate_candles_removed(self):
        """İki chunk'ta çakışan timestamp'ler deduplicate edilmeli."""
        chunk1 = _make_candles("2024-01-01 00:00", 10)
        chunk2 = _make_candles("2024-01-01 08:00", 10)  # son 2 bar örtüşüyor
        ex = FakeExchange([chunk1, chunk2, []])
        df = fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=_ms("2024-01-02"),
            exchange=ex,
        )
        assert df["timestamp"].is_unique

    def test_result_sorted_ascending(self):
        chunk = _make_candles("2024-01-01", 20)
        ex = FakeExchange([chunk, []])
        df = fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=_ms("2024-01-05"),
            exchange=ex,
        )
        assert df["timestamp"].is_monotonic_increasing

    def test_infinite_loop_guard(self):
        """Exchange aynı since'i tekrar döndürürse MaxIterations hatası fırlatılmalı."""
        ex = InfiniteExchange()
        with pytest.raises(MarketDataError, match="max_iterations"):
            fetch_ohlcv_range(
                "BTC/USDT", "1h",
                since=_ms("2024-01-01"),
                until=_ms("2025-01-01"),  # 1 yıl — normalde bitmez
                exchange=ex,
                max_iterations=5,         # test için küçük limit
            )

    def test_bars_beyond_until_trimmed(self):
        """'until' tarihinden sonraki barlar kesilmeli."""
        until_ts = _ms("2024-01-01 10:00")
        chunk = _make_candles("2024-01-01 00:00", 20)  # 20 saat
        ex = FakeExchange([chunk, []])
        df = fetch_ohlcv_range(
            "BTC/USDT", "1h",
            since=_ms("2024-01-01"),
            until=until_ts,
            exchange=ex,
        )
        last_ts_ms = int(df["timestamp"].iloc[-1].timestamp() * 1000)
        assert last_ts_ms <= until_ts


# ── DataLoader alignment testleri ─────────────────────────────────────────────

def _make_aligned_df(start: str, n: int, freq: str = "1h") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.0, "volume": 1_000_000.0,
    })


class TestDataLoaderAlignment:

    def test_aligned_symbols_no_bar_loss(self):
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 100),
            "ETH/USDT": _make_aligned_df("2024-01-01", 100),
        }
        result, report = loader._align(dfs)
        assert report.bars_lost == 0

    def test_misaligned_symbols_report_bar_loss(self):
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 100),
            "ETH/USDT": _make_aligned_df("2024-01-05", 50),   # daha kısa
        }
        result, report = loader._align(dfs)
        assert report.bars_lost > 0

    def test_alignment_report_identifies_narrowing_symbol(self):
        """Hangi symbol veri daraltmasına neden olduğu raporlanmalı."""
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 100),  # daha uzun
            "ETH/USDT": _make_aligned_df("2024-01-01", 50),   # aynı başlangıç, daha kısa
        }
        result, report = loader._align(dfs)
        assert "ETH/USDT" in report.narrowing_symbols

    def test_aligned_output_all_same_length(self):
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 100),
            "ETH/USDT": _make_aligned_df("2024-01-05", 80),
        }
        result, _ = loader._align(dfs)
        lengths = [len(df) for df in result.values()]
        assert len(set(lengths)) == 1  # hepsi eşit uzunlukta

    def test_alignment_report_has_per_symbol_bar_counts(self):
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 100),
            "ETH/USDT": _make_aligned_df("2024-01-01", 80),
        }
        result, report = loader._align(dfs)
        assert "BTC/USDT" in report.symbol_bar_counts
        assert "ETH/USDT" in report.symbol_bar_counts

    def test_single_symbol_no_alignment_needed(self):
        loader = DataLoader.__new__(DataLoader)
        dfs = {"BTC/USDT": _make_aligned_df("2024-01-01", 50)}
        result, report = loader._align(dfs)
        assert len(result["BTC/USDT"]) == 50
        assert report.bars_lost == 0

    def test_empty_intersection_raises(self):
        """Hiç ortak timestamp yoksa hata fırlatılmalı."""
        loader = DataLoader.__new__(DataLoader)
        dfs = {
            "BTC/USDT": _make_aligned_df("2024-01-01", 24),
            "ETH/USDT": _make_aligned_df("2024-02-01", 24),  # farklı ay
        }
        with pytest.raises(DataLoaderError, match="ortak"):
            loader._align(dfs)


# ── AlignmentReport yapısı ────────────────────────────────────────────────────

class TestAlignmentReport:

    def test_has_required_fields(self):
        report = AlignmentReport(
            bars_lost=10,
            original_bars={"BTC/USDT": 100},
            aligned_bars=90,
            symbol_bar_counts={"BTC/USDT": 90},
            narrowing_symbols=["ETH/USDT"],
        )
        assert hasattr(report, "bars_lost")
        assert hasattr(report, "narrowing_symbols")
        assert hasattr(report, "symbol_bar_counts")

    def test_print_report_no_crash(self):
        report = AlignmentReport(
            bars_lost=5,
            original_bars={"BTC/USDT": 100, "ETH/USDT": 95},
            aligned_bars=95,
            symbol_bar_counts={"BTC/USDT": 95, "ETH/USDT": 95},
            narrowing_symbols=["ETH/USDT"],
        )
        report.print_summary()  # crash olmamalı
