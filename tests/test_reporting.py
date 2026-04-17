"""
TDD — Reporting modülü testleri.

compute_metrics saf fonksiyondur — DB bağımlılığı yoktur.
Testler doğrudan trade listesi geçerek çalışır.
"""
import pytest
from reporting.report import compute_metrics, ReportMetrics, metrics_to_dict

INIT_BALANCE = 10_000.0


def _trade(
    pnl: float,
    symbol: str = "BTC/USDT",
    opened_at: str = "2024-01-01T10:00:00+00:00",
    closed_at: str = "2024-01-02T10:00:00+00:00",  # 24 saat sonra
) -> dict:
    return {
        "id": 1,
        "run_id": "test-run",
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 50_000.0,
        "exit_price": 55_000.0,
        "quantity": 0.02,
        "pnl": pnl,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "strategy_name": "SwingStrategy",
    }


# ─── Temel Metrikler ──────────────────────────────────────────────────────────

class TestBasicMetrics:

    def test_empty_trades_returns_zero_metrics(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.total_trades == 0
        assert m.win_count == 0
        assert m.loss_count == 0
        assert m.total_pnl == 0.0
        assert m.win_rate == 0.0

    def test_empty_trades_no_crash(self):
        m = compute_metrics([], INIT_BALANCE)
        assert isinstance(m, ReportMetrics)

    def test_single_win_trade(self):
        m = compute_metrics([_trade(500.0)], INIT_BALANCE)
        assert m.total_trades == 1
        assert m.win_count == 1
        assert m.loss_count == 0
        assert pytest.approx(m.total_pnl) == 500.0

    def test_single_loss_trade(self):
        m = compute_metrics([_trade(-300.0)], INIT_BALANCE)
        assert m.win_count == 0
        assert m.loss_count == 1
        assert pytest.approx(m.total_pnl) == -300.0

    def test_win_rate_two_wins_one_loss(self):
        trades = [_trade(400.0), _trade(200.0), _trade(-150.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.win_rate) == 2 / 3

    def test_win_rate_all_losses(self):
        trades = [_trade(-100.0), _trade(-200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.win_rate == 0.0

    def test_win_rate_all_wins(self):
        trades = [_trade(100.0), _trade(200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.win_rate == 1.0

    def test_avg_pnl_per_trade(self):
        trades = [_trade(300.0), _trade(-100.0), _trade(200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.avg_pnl_per_trade) == 400.0 / 3

    def test_max_win(self):
        trades = [_trade(100.0), _trade(500.0), _trade(200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.max_win) == 500.0

    def test_max_loss(self):
        trades = [_trade(-50.0), _trade(-300.0), _trade(-100.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.max_loss) == -300.0

    def test_max_win_no_wins_returns_zero(self):
        trades = [_trade(-100.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.max_win == 0.0

    def test_max_loss_no_losses_returns_zero(self):
        trades = [_trade(100.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.max_loss == 0.0


# ─── Profit Factor ───────────────────────────────────────────────────────────

class TestProfitFactor:

    def test_profit_factor_basic(self):
        # gross_win = 600, gross_loss = 200 → pf = 3.0
        trades = [_trade(400.0), _trade(200.0), _trade(-200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.profit_factor) == 3.0

    def test_profit_factor_no_losses_is_inf(self):
        trades = [_trade(100.0), _trade(200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.profit_factor == float("inf")

    def test_profit_factor_no_wins_is_zero(self):
        trades = [_trade(-100.0), _trade(-200.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.profit_factor == 0.0

    def test_profit_factor_empty_trades(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.profit_factor == 0.0


# ─── Equity Curve ve Max Drawdown ────────────────────────────────────────────

class TestEquityAndDrawdown:

    def test_equity_curve_starts_with_initial_balance(self):
        m = compute_metrics([_trade(500.0)], INIT_BALANCE)
        assert m.equity_curve[0] == INIT_BALANCE

    def test_equity_curve_grows_with_profit(self):
        m = compute_metrics([_trade(500.0)], INIT_BALANCE)
        assert m.equity_curve[-1] == INIT_BALANCE + 500.0

    def test_equity_curve_length(self):
        trades = [_trade(100.0), _trade(200.0), _trade(-50.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        # başlangıç + her trade sonrası
        assert len(m.equity_curve) == 4

    def test_equity_curve_empty_trades(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.equity_curve == [INIT_BALANCE]

    def test_max_drawdown_basic(self):
        """
        Equity: 10000 → 12000 → 11000 → 13000 → 9000 → 11000
        Peak: 13000, trough: 9000 → max_drawdown = -4000
        """
        trades = [
            _trade(2000.0,  closed_at="2024-01-02T00:00:00+00:00"),
            _trade(-1000.0, closed_at="2024-01-03T00:00:00+00:00"),
            _trade(2000.0,  closed_at="2024-01-04T00:00:00+00:00"),
            _trade(-4000.0, closed_at="2024-01-05T00:00:00+00:00"),
            _trade(2000.0,  closed_at="2024-01-06T00:00:00+00:00"),
        ]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.max_drawdown) == -4000.0

    def test_max_drawdown_no_drawdown(self):
        """Sürekli artan equity → drawdown sıfır."""
        trades = [_trade(100.0), _trade(200.0), _trade(300.0)]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.max_drawdown == 0.0

    def test_max_drawdown_empty_trades(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.max_drawdown == 0.0


# ─── Davranış Metrikleri ─────────────────────────────────────────────────────

class TestBehaviorMetrics:

    def test_trades_by_hour_correct(self):
        trades = [
            _trade(100.0, opened_at="2024-01-01T09:00:00+00:00"),
            _trade(200.0, opened_at="2024-01-01T09:30:00+00:00"),
            _trade(300.0, opened_at="2024-01-01T14:00:00+00:00"),
        ]
        m = compute_metrics(trades, INIT_BALANCE)
        assert m.trades_by_hour.get(9, 0) == 2
        assert m.trades_by_hour.get(14, 0) == 1

    def test_trades_by_hour_empty(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.trades_by_hour == {}

    def test_avg_duration_hours_24h(self):
        """opened_at → closed_at farkı 24 saat → avg = 24.0"""
        trades = [
            _trade(100.0,
                   opened_at="2024-01-01T00:00:00+00:00",
                   closed_at="2024-01-02T00:00:00+00:00"),
        ]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.avg_duration_hours) == 24.0

    def test_avg_duration_hours_multiple(self):
        """12h + 36h → avg = 24h"""
        trades = [
            _trade(100.0,
                   opened_at="2024-01-01T00:00:00+00:00",
                   closed_at="2024-01-01T12:00:00+00:00"),
            _trade(200.0,
                   opened_at="2024-01-02T00:00:00+00:00",
                   closed_at="2024-01-03T12:00:00+00:00"),
        ]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.avg_duration_hours) == 24.0

    def test_avg_duration_empty(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.avg_duration_hours == 0.0

    def test_symbol_pnl_single_symbol(self):
        trades = [_trade(300.0, symbol="BTC/USDT"), _trade(200.0, symbol="BTC/USDT")]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.symbol_pnl["BTC/USDT"]) == 500.0

    def test_symbol_pnl_multiple_symbols(self):
        trades = [
            _trade(400.0, symbol="BTC/USDT"),
            _trade(-100.0, symbol="ETH/USDT"),
        ]
        m = compute_metrics(trades, INIT_BALANCE)
        assert pytest.approx(m.symbol_pnl["BTC/USDT"]) == 400.0
        assert pytest.approx(m.symbol_pnl["ETH/USDT"]) == -100.0

    def test_symbol_pnl_empty(self):
        m = compute_metrics([], INIT_BALANCE)
        assert m.symbol_pnl == {}


# ─── JSON Export ──────────────────────────────────────────────────────────────

class TestJsonExport:

    def test_metrics_to_dict_serializable(self):
        import json
        m = compute_metrics([_trade(300.0), _trade(-100.0)], INIT_BALANCE)
        d = metrics_to_dict(m)
        # JSON serializable olmalı — hata fırlatmamalı
        json.dumps(d)

    def test_metrics_to_dict_has_required_keys(self):
        m = compute_metrics([_trade(300.0)], INIT_BALANCE)
        d = metrics_to_dict(m)
        for key in ("total_trades", "win_rate", "total_pnl", "max_drawdown",
                    "profit_factor", "equity_curve"):
            assert key in d

    def test_metrics_to_dict_hour_keys_are_strings(self):
        """JSON object key'leri string olmalı."""
        trades = [_trade(100.0, opened_at="2024-01-01T09:00:00+00:00")]
        m = compute_metrics(trades, INIT_BALANCE)
        d = metrics_to_dict(m)
        for key in d["trades_by_hour"]:
            assert isinstance(key, str)
