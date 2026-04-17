"""
RiskManager TDD testleri.

Tüm testler önce RED (implementasyon yok), sonra GREEN olmalı.
"""

import pytest
import pandas as pd

from portfolio.portfolio import Portfolio
from risk.risk_manager import RiskManager, RiskDecision


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def make_portfolio(cash: float = 10_000.0) -> Portfolio:
    return Portfolio(initial_cash=cash)


def make_risk_manager(
    portfolio: Portfolio | None = None,
    max_open_positions: int = 3,
    cooldown_bars: int = 5,
    daily_max_trades: int = 10,
    daily_max_loss_pct: float = 0.03,
) -> RiskManager:
    if portfolio is None:
        portfolio = make_portfolio()
    config = {
        "max_open_positions": max_open_positions,
        "cooldown_bars": cooldown_bars,
        "daily_max_trades": daily_max_trades,
        "daily_max_loss_pct": daily_max_loss_pct,
    }
    return RiskManager(portfolio=portfolio, config=config)


def ts(date: str = "2024-01-01", hour: int = 10) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hour:02d}:00:00")


# ── Test 1: max_open_positions ─────────────────────────────────────────────────

class TestMaxOpenPositions:
    def test_trade_allowed_when_below_limit(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.05)
        rm = make_risk_manager(portfolio=portfolio, max_open_positions=3)

        decision = rm.can_open("ETH/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is True

    def test_trade_blocked_when_at_limit(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.01)
        portfolio.open_long("ETH/USDT", 3_000.0, 0.1)
        rm = make_risk_manager(portfolio=portfolio, max_open_positions=2)

        decision = rm.can_open("BNB/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is False
        assert decision.code == "MAX_POS"

    def test_decision_has_reason_on_block(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.01)
        rm = make_risk_manager(portfolio=portfolio, max_open_positions=1)

        decision = rm.can_open("ETH/USDT", bar_index=0, bar_ts=ts())

        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0


# ── Test 2: Duplicate (aynı symbol tekrar) ────────────────────────────────────

class TestDuplicateTrade:
    def test_duplicate_symbol_blocked(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.01)
        rm = make_risk_manager(portfolio=portfolio)

        decision = rm.can_open("BTC/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is False
        assert decision.code == "DUPLICATE"

    def test_different_symbol_allowed_when_no_duplicate(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.01)
        rm = make_risk_manager(portfolio=portfolio, max_open_positions=3)

        decision = rm.can_open("ETH/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is True


# ── Test 3: Cooldown ──────────────────────────────────────────────────────────

class TestCooldown:
    def test_trade_blocked_within_cooldown(self):
        rm = make_risk_manager(cooldown_bars=5)
        rm.record_open("BTC/USDT", bar_index=10, bar_ts=ts())
        rm.record_close("BTC/USDT", pnl=50.0, bar_index=12, bar_ts=ts())

        # Bar 14: son kapanıştan sadece 2 bar geçti (< 5)
        decision = rm.can_open("BTC/USDT", bar_index=14, bar_ts=ts())

        assert decision.allowed is False
        assert decision.code == "COOLDOWN"

    def test_trade_allowed_after_cooldown(self):
        rm = make_risk_manager(cooldown_bars=5)
        rm.record_open("BTC/USDT", bar_index=10, bar_ts=ts())
        rm.record_close("BTC/USDT", pnl=50.0, bar_index=12, bar_ts=ts())

        # Bar 18: son kapanıştan 6 bar geçti (> 5)
        decision = rm.can_open("BTC/USDT", bar_index=18, bar_ts=ts())

        assert decision.allowed is True

    def test_cooldown_is_symbol_specific(self):
        rm = make_risk_manager(cooldown_bars=5)
        rm.record_open("BTC/USDT", bar_index=10, bar_ts=ts())
        rm.record_close("BTC/USDT", pnl=50.0, bar_index=12, bar_ts=ts())

        # ETH/USDT için cooldown yok
        decision = rm.can_open("ETH/USDT", bar_index=14, bar_ts=ts())

        assert decision.allowed is True


# ── Test 4: Günlük max loss limiti ────────────────────────────────────────────

class TestDailyMaxLoss:
    def test_trade_blocked_when_daily_loss_exceeded(self):
        portfolio = make_portfolio(cash=10_000.0)
        # daily_max_loss_pct=0.03 → 10_000 * 0.03 = 300 TL kayıp limiti
        rm = make_risk_manager(portfolio=portfolio, daily_max_loss_pct=0.03)

        # 310 TL realized kayıp — limiti aştı
        rm.record_open("BTC/USDT", bar_index=1, bar_ts=ts("2024-01-01"))
        rm.record_close("BTC/USDT", pnl=-310.0, bar_index=3, bar_ts=ts("2024-01-01"))

        decision = rm.can_open("ETH/USDT", bar_index=4, bar_ts=ts("2024-01-01"))

        assert decision.allowed is False
        assert decision.code == "DAILY_LOSS"

    def test_trade_allowed_when_loss_under_limit(self):
        portfolio = make_portfolio(cash=10_000.0)
        rm = make_risk_manager(portfolio=portfolio, daily_max_loss_pct=0.03)

        # 200 TL kayıp — limitin altında
        rm.record_open("BTC/USDT", bar_index=1, bar_ts=ts("2024-01-01"))
        rm.record_close("BTC/USDT", pnl=-200.0, bar_index=3, bar_ts=ts("2024-01-01"))

        decision = rm.can_open("ETH/USDT", bar_index=4, bar_ts=ts("2024-01-01"))

        assert decision.allowed is True

    def test_daily_loss_resets_next_day(self):
        portfolio = make_portfolio(cash=10_000.0)
        rm = make_risk_manager(portfolio=portfolio, daily_max_loss_pct=0.03)

        # Gün 1: limit aşıldı
        rm.record_open("BTC/USDT", bar_index=1, bar_ts=ts("2024-01-01"))
        rm.record_close("BTC/USDT", pnl=-400.0, bar_index=3, bar_ts=ts("2024-01-01"))

        # Gün 2: otomatik reset — trade serbest
        decision = rm.can_open("ETH/USDT", bar_index=50, bar_ts=ts("2024-01-02"))

        assert decision.allowed is True


# ── Test 5: Günlük trade limiti ───────────────────────────────────────────────

class TestDailyTradeLimit:
    def test_trade_blocked_when_daily_limit_reached(self):
        rm = make_risk_manager(daily_max_trades=3)
        day = "2024-01-01"

        for i in range(3):
            rm.record_open(f"SYM{i}", bar_index=i * 2, bar_ts=ts(day))
            rm.record_close(f"SYM{i}", pnl=10.0, bar_index=i * 2 + 1, bar_ts=ts(day))

        decision = rm.can_open("NEW/USDT", bar_index=10, bar_ts=ts(day))

        assert decision.allowed is False
        assert decision.code == "DAILY_TRADE_LIMIT"

    def test_trade_allowed_under_daily_limit(self):
        rm = make_risk_manager(daily_max_trades=5)
        day = "2024-01-01"

        rm.record_open("BTC/USDT", bar_index=1, bar_ts=ts(day))
        rm.record_close("BTC/USDT", pnl=20.0, bar_index=2, bar_ts=ts(day))

        decision = rm.can_open("ETH/USDT", bar_index=3, bar_ts=ts(day))

        assert decision.allowed is True

    def test_daily_trade_count_resets_next_day(self):
        rm = make_risk_manager(daily_max_trades=2)
        day1, day2 = "2024-01-01", "2024-01-02"

        for i in range(2):
            rm.record_open(f"SYM{i}", bar_index=i * 2, bar_ts=ts(day1))
            rm.record_close(f"SYM{i}", pnl=10.0, bar_index=i * 2 + 1, bar_ts=ts(day1))

        # Gün 2: sıfırlandı
        decision = rm.can_open("ETH/USDT", bar_index=20, bar_ts=ts(day2))

        assert decision.allowed is True


# ── Test 6: Risk veto → trade açılmıyor (Engine entegrasyon) ─────────────────

class TestRiskVeto:
    def test_risk_decision_structure(self):
        rm = make_risk_manager()
        decision = rm.can_open("BTC/USDT", bar_index=0, bar_ts=ts())

        assert hasattr(decision, "allowed")
        assert hasattr(decision, "reason")
        assert hasattr(decision, "code")

    def test_allowed_decision_has_empty_code(self):
        rm = make_risk_manager()
        decision = rm.can_open("BTC/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is True
        assert decision.code == ""

    def test_blocked_decision_has_non_empty_code(self):
        portfolio = make_portfolio()
        portfolio.open_long("BTC/USDT", 50_000.0, 0.01)
        rm = make_risk_manager(portfolio=portfolio, max_open_positions=1)

        decision = rm.can_open("ETH/USDT", bar_index=0, bar_ts=ts())

        assert decision.allowed is False
        assert decision.code != ""
        assert decision.reason != ""
