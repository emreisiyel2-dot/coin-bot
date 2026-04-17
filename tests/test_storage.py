"""
TDD — Storage modülü testleri.

In-memory SQLite kullanılır: kurulum gerektirmez, her test izole.
"""
import pytest

from storage.database import Database, build_trade_rows
from execution.engine import Trade
from strategy.signals import Signal
import pandas as pd

# ─── Test sabit verileri ──────────────────────────────────────────────────────

RUN_ID = "run-test-001"
STRATEGY = "SwingStrategy"
SYM = "BTC/USDT"

SAMPLE_RUN = {
    "strategy_name": STRATEGY,
    "symbol": SYM,
    "started_at": "2024-01-01T00:00:00+00:00",
    "ended_at": "2024-01-10T00:00:00+00:00",
    "initial_balance": 10_000.0,
    "final_equity": 10_489.5,
    "realized_pnl": 489.5,
    "unrealized_pnl": 0.0,
    "trade_count": 1,
    "win_count": 1,
    "loss_count": 0,
}

SAMPLE_TRADE = {
    "run_id": RUN_ID,
    "symbol": SYM,
    "side": "LONG",
    "entry_price": 50_000.0,
    "exit_price": 55_000.0,
    "quantity": 0.02,
    "pnl": 489.5,
    "opened_at": "2024-01-01T00:00:00+00:00",
    "closed_at": "2024-01-05T00:00:00+00:00",
    "strategy_name": STRATEGY,
}


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


# ─── Başlatma ve Schema ───────────────────────────────────────────────────────

class TestInit:

    def test_database_initializes_without_error(self):
        db = Database(":memory:")
        db.close()

    def test_tables_exist_after_init(self, db):
        tables = db.list_tables()
        assert "trades" in tables
        assert "runs" in tables


# ─── Run (Özet) Kayıt ve Getirme ─────────────────────────────────────────────

class TestRun:

    def test_save_run_then_fetch(self, db):
        db.save_run(RUN_ID, SAMPLE_RUN)
        result = db.fetch_run(RUN_ID)
        assert result is not None
        assert result["run_id"] == RUN_ID

    def test_fetch_run_fields_match(self, db):
        db.save_run(RUN_ID, SAMPLE_RUN)
        result = db.fetch_run(RUN_ID)
        assert result["strategy_name"] == STRATEGY
        assert result["symbol"] == SYM
        assert pytest.approx(result["initial_balance"]) == 10_000.0
        assert pytest.approx(result["final_equity"]) == 10_489.5
        assert pytest.approx(result["realized_pnl"]) == 489.5
        assert result["trade_count"] == 1
        assert result["win_count"] == 1
        assert result["loss_count"] == 0

    def test_fetch_run_missing_returns_none(self, db):
        assert db.fetch_run("nonexistent-run") is None

    def test_fetch_all_runs_empty(self, db):
        assert db.fetch_all_runs() == []

    def test_fetch_all_runs_returns_all(self, db):
        db.save_run("run-1", SAMPLE_RUN)
        db.save_run("run-2", {**SAMPLE_RUN, "trade_count": 3})
        runs = db.fetch_all_runs()
        assert len(runs) == 2
        run_ids = {r["run_id"] for r in runs}
        assert "run-1" in run_ids
        assert "run-2" in run_ids

    def test_run_numeric_types(self, db):
        db.save_run(RUN_ID, SAMPLE_RUN)
        result = db.fetch_run(RUN_ID)
        assert isinstance(result["initial_balance"], float)
        assert isinstance(result["final_equity"], float)
        assert isinstance(result["realized_pnl"], float)
        assert isinstance(result["trade_count"], int)
        assert isinstance(result["win_count"], int)
        assert isinstance(result["loss_count"], int)


# ─── Trade Kayıt ve Getirme ───────────────────────────────────────────────────

class TestTrades:

    def test_save_trade_then_fetch(self, db):
        db.save_trades([SAMPLE_TRADE])
        trades = db.fetch_trades()
        assert len(trades) == 1

    def test_save_trade_fields_match(self, db):
        db.save_trades([SAMPLE_TRADE])
        t = db.fetch_trades()[0]
        assert t["run_id"] == RUN_ID
        assert t["symbol"] == SYM
        assert t["side"] == "LONG"
        assert pytest.approx(t["entry_price"]) == 50_000.0
        assert pytest.approx(t["exit_price"]) == 55_000.0
        assert pytest.approx(t["pnl"]) == 489.5
        assert t["strategy_name"] == STRATEGY

    def test_fetch_trades_empty_db_returns_empty_list(self, db):
        assert db.fetch_trades() == []

    def test_fetch_trades_filtered_by_run_id(self, db):
        trade_a = {**SAMPLE_TRADE, "run_id": "run-A"}
        trade_b = {**SAMPLE_TRADE, "run_id": "run-B"}
        db.save_trades([trade_a, trade_b])
        result = db.fetch_trades(run_id="run-A")
        assert len(result) == 1
        assert result[0]["run_id"] == "run-A"

    def test_multiple_trades_same_run_id(self, db):
        trade2 = {**SAMPLE_TRADE, "pnl": -200.0, "exit_price": 47_000.0}
        db.save_trades([SAMPLE_TRADE, trade2])
        result = db.fetch_trades(run_id=RUN_ID)
        assert len(result) == 2

    def test_trade_with_null_exit_price(self, db):
        """Hâlâ açık pozisyon: exit_price ve closed_at None olabilir."""
        open_trade = {**SAMPLE_TRADE, "exit_price": None, "closed_at": None, "pnl": None}
        db.save_trades([open_trade])
        result = db.fetch_trades()[0]
        assert result["exit_price"] is None
        assert result["closed_at"] is None
        assert result["pnl"] is None

    def test_trade_id_auto_assigned(self, db):
        db.save_trades([SAMPLE_TRADE])
        t = db.fetch_trades()[0]
        assert "id" in t
        assert t["id"] is not None

    def test_trade_numeric_types(self, db):
        db.save_trades([SAMPLE_TRADE])
        t = db.fetch_trades()[0]
        assert isinstance(t["entry_price"], float)
        assert isinstance(t["quantity"], float)
        assert isinstance(t["pnl"], float)


# ─── build_trade_rows yardımcısı ─────────────────────────────────────────────

class TestBuildTradeRows:

    def _make_trade(self, action, price, bar_index):
        return Trade(
            symbol=SYM, side="LONG", action=action,
            price=price, quantity=0.02,
            bar_index=bar_index,
            timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
            realized_pnl=500.0 if action == "CLOSE" else None,
        )

    def test_build_rows_from_open_close_pair(self):
        trades = [
            self._make_trade("OPEN",  50_000.0, 0),
            self._make_trade("CLOSE", 55_000.0, 5),
        ]
        rows = build_trade_rows(trades, RUN_ID, STRATEGY)
        assert len(rows) == 1
        row = rows[0]
        assert pytest.approx(row["entry_price"]) == 50_000.0
        assert pytest.approx(row["exit_price"]) == 55_000.0
        assert pytest.approx(row["pnl"]) == 500.0

    def test_build_rows_two_round_trips(self):
        trades = [
            self._make_trade("OPEN",  50_000.0, 0),
            self._make_trade("CLOSE", 55_000.0, 5),
            self._make_trade("OPEN",  54_000.0, 6),
            self._make_trade("CLOSE", 52_000.0, 9),
        ]
        rows = build_trade_rows(trades, RUN_ID, STRATEGY)
        assert len(rows) == 2

    def test_build_rows_no_close_gives_empty(self):
        trades = [self._make_trade("OPEN", 50_000.0, 0)]
        rows = build_trade_rows(trades, RUN_ID, STRATEGY)
        assert rows == []

    def test_build_rows_run_id_and_strategy_injected(self):
        trades = [
            self._make_trade("OPEN",  50_000.0, 0),
            self._make_trade("CLOSE", 55_000.0, 5),
        ]
        rows = build_trade_rows(trades, RUN_ID, STRATEGY)
        assert rows[0]["run_id"] == RUN_ID
        assert rows[0]["strategy_name"] == STRATEGY
