"""
OpportunityScanner TDD testleri.

Strateji bağımsız; scanner'ın kendi filtre/skor mantığını test eder.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.signals import Signal
from scanner.opportunity_scanner import Opportunity, OpportunityScanner


# ── Test DataFrame fabrikası ──────────────────────────────────────────────────

def make_df(
    n: int = 250,
    close: float = 50_000.0,
    rsi_target: float | None = None,
    volume: float = 2_000_000.0,
    atr_pct: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """Kontrollü OHLCV DataFrame üretir."""
    np.random.seed(seed)
    closes = np.full(n, close, dtype=float)
    # Hafif gürültü — ATR hesaplaması için gerçekçi high/low
    noise = np.random.randn(n) * close * 0.001
    closes = closes + noise
    high = closes * (1 + atr_pct / 2)
    low  = closes * (1 - atr_pct / 2)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open":   closes,
        "high":   high,
        "low":    low,
        "close":  closes,
        "volume": np.full(n, volume),
    })


def make_scanner(
    min_atr_pct: float = 0.003,
    min_volume: float = 500_000.0,
    volume_window: int = 5,
    spread_penalty_threshold: float = 0.002,
    rsi_weight: float = 0.50,
    atr_weight: float = 0.30,
    volume_weight: float = 0.20,
    top_n: int = 3,
) -> OpportunityScanner:
    config = {
        "min_atr_pct": min_atr_pct,
        "min_volume": min_volume,
        "volume_window": volume_window,
        "spread_penalty_threshold": spread_penalty_threshold,
        "rsi_weight": rsi_weight,
        "atr_weight": atr_weight,
        "volume_weight": volume_weight,
        "top_n": top_n,
    }
    return OpportunityScanner(config=config)


# ── Sahte strateji (signal kontrolü için) ─────────────────────────────────────

class AlwaysBuyStrategy:
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return Signal.BUY


class AlwaysHoldStrategy:
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return Signal.HOLD


class AlwaysSellStrategy:
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return Signal.SELL


# ── Test 1: BUY olmayan sinyaller eleniyor ────────────────────────────────────

class TestSignalFilter:
    def test_hold_signal_returns_empty(self):
        scanner = make_scanner()
        df = make_df()
        candidates = {"BTC/USDT": (AlwaysHoldStrategy(), df)}

        result = scanner.scan(candidates)

        assert result == []

    def test_sell_signal_returns_empty(self):
        scanner = make_scanner()
        df = make_df()
        candidates = {"BTC/USDT": (AlwaysSellStrategy(), df)}

        result = scanner.scan(candidates)

        assert result == []

    def test_buy_signal_returns_opportunity(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert len(result) == 1
        assert result[0].symbol == "BTC/USDT"
        assert result[0].signal == Signal.BUY


# ── Test 2: Volume filtresi ───────────────────────────────────────────────────

class TestVolumeFilter:
    def test_below_min_volume_filtered_out(self):
        scanner = make_scanner(min_volume=1_000_000.0)
        # close=100, volume=5 → usdt_volume=500 < 1_000_000
        df = make_df(close=100.0, volume=5.0)
        candidates = {"LOW/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert result == []

    def test_above_min_volume_passes(self):
        scanner = make_scanner(min_volume=500_000.0)
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert len(result) == 1

    def test_rolling_avg_volume_used_not_single_bar(self):
        """Rolling avg volume, tek bar'dan daha güvenilir."""
        scanner = make_scanner(min_volume=1_000_000.0, volume_window=5)
        n = 50
        volumes = np.full(n, 2_000_000.0)
        volumes[-1] = 100.0  # Son bar çok düşük ama 5-bar ortalama yüksek
        df = make_df(n=n, volume=2_000_000.0, atr_pct=0.01)
        df["volume"] = volumes

        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}
        result = scanner.scan(candidates)

        assert len(result) == 1  # rolling avg geçiyor


# ── Test 3: ATR / volatilite filtresi ────────────────────────────────────────

class TestAtrFilter:
    def test_below_min_atr_pct_filtered_out(self):
        scanner = make_scanner(min_atr_pct=0.01)
        df = make_df(atr_pct=0.001, volume=2_000_000.0)  # çok düşük volatilite
        candidates = {"FLAT/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert result == []

    def test_above_min_atr_pct_passes(self):
        scanner = make_scanner(min_atr_pct=0.003)
        df = make_df(atr_pct=0.02, volume=2_000_000.0)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert len(result) == 1


# ── Test 4: RSI derinlik skoru ────────────────────────────────────────────────

class TestRsiScore:
    def test_score_between_0_and_1(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert 0.0 <= result[0].score <= 1.0

    def test_score_breakdown_has_rsi_key(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert "rsi_depth" in result[0].score_breakdown

    def test_score_breakdown_has_atr_key(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert "atr" in result[0].score_breakdown

    def test_score_breakdown_has_volume_key(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        candidates = {"BTC/USDT": (AlwaysBuyStrategy(), df)}

        result = scanner.scan(candidates)

        assert "volume" in result[0].score_breakdown


# ── Test 5: Spread / maliyet cezası ──────────────────────────────────────────

class TestSpreadPenalty:
    def test_high_spread_reduces_score(self):
        scanner_low  = make_scanner(spread_penalty_threshold=0.002)
        scanner_high = make_scanner(spread_penalty_threshold=0.002)
        df = make_df(volume=2_000_000.0, atr_pct=0.01)

        # Düşük spread
        result_low  = scanner_low.scan({"BTC/USDT": (AlwaysBuyStrategy(), df)},
                                        spread_overrides={"BTC/USDT": 0.0005})
        # Yüksek spread
        result_high = scanner_high.scan({"BTC/USDT": (AlwaysBuyStrategy(), df)},
                                         spread_overrides={"BTC/USDT": 0.005})

        assert result_low[0].score > result_high[0].score

    def test_score_breakdown_has_spread_penalty_key(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        result = scanner.scan(
            {"BTC/USDT": (AlwaysBuyStrategy(), df)},
            spread_overrides={"BTC/USDT": 0.001},
        )

        assert "spread_penalty" in result[0].score_breakdown


# ── Test 6: Sıralama ve top_n ─────────────────────────────────────────────────

class TestRankingAndTopN:
    def test_results_sorted_by_score_descending(self):
        scanner = make_scanner(top_n=10)
        # İki farklı ATR → farklı skor
        df_high_vol = make_df(volume=5_000_000.0, atr_pct=0.02, seed=1)
        df_low_vol  = make_df(volume=1_000_000.0, atr_pct=0.005, seed=2)
        candidates = {
            "LOW/USDT":  (AlwaysBuyStrategy(), df_low_vol),
            "HIGH/USDT": (AlwaysBuyStrategy(), df_high_vol),
        }

        result = scanner.scan(candidates)

        assert len(result) == 2
        assert result[0].score >= result[1].score

    def test_top_n_limits_results(self):
        scanner = make_scanner(top_n=2)
        candidates = {
            f"COIN{i}/USDT": (AlwaysBuyStrategy(), make_df(volume=2_000_000.0, atr_pct=0.01, seed=i))
            for i in range(5)
        }

        result = scanner.scan(candidates)

        assert len(result) <= 2

    def test_empty_universe_returns_empty(self):
        scanner = make_scanner()
        result = scanner.scan({})
        assert result == []


# ── Test 7: Opportunity yapısı ───────────────────────────────────────────────

class TestOpportunityStructure:
    def test_opportunity_has_required_fields(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        result = scanner.scan({"BTC/USDT": (AlwaysBuyStrategy(), df)})

        opp = result[0]
        assert hasattr(opp, "symbol")
        assert hasattr(opp, "signal")
        assert hasattr(opp, "score")
        assert hasattr(opp, "score_breakdown")
        assert hasattr(opp, "reason")

    def test_reason_is_non_empty_string(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        result = scanner.scan({"BTC/USDT": (AlwaysBuyStrategy(), df)})

        assert isinstance(result[0].reason, str)
        assert len(result[0].reason) > 0

    def test_score_clamped_between_0_and_1(self):
        scanner = make_scanner()
        df = make_df(volume=2_000_000.0, atr_pct=0.01)
        result = scanner.scan({"BTC/USDT": (AlwaysBuyStrategy(), df)})

        assert 0.0 <= result[0].score <= 1.0
