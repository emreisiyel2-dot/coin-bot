import logging
import sqlite3
from typing import Any

from execution.engine import Trade

logger = logging.getLogger(__name__)

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT NOT NULL,
    initial_balance REAL NOT NULL,
    final_equity    REAL NOT NULL,
    realized_pnl    REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    trade_count     INTEGER NOT NULL,
    win_count       INTEGER NOT NULL,
    loss_count      INTEGER NOT NULL
);
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    entry_price   REAL    NOT NULL,
    exit_price    REAL,
    quantity      REAL    NOT NULL,
    pnl           REAL,
    opened_at     TEXT    NOT NULL,
    closed_at     TEXT,
    strategy_name TEXT    NOT NULL
);
"""


class Database:
    """
    SQLite tabanlı kalıcı depolama.

    Sadece persist / fetch yapar — strateji veya engine mantığı yoktur.
    Test için db_path=":memory:" kullanılır.
    """

    def __init__(self, db_path: str = "paper_trading.db") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()
        logger.info("Database hazır | path=%s", db_path)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_RUNS)
            self._conn.execute(_CREATE_TRADES)

    def list_tables(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        )
        return [row["name"] for row in cursor.fetchall()]

    # ── Run Özet ──────────────────────────────────────────────────────────────

    def save_run(self, run_id: str, data: dict) -> None:
        sql = """
        INSERT OR REPLACE INTO runs
            (run_id, strategy_name, symbol, started_at, ended_at,
             initial_balance, final_equity, realized_pnl, unrealized_pnl,
             trade_count, win_count, loss_count)
        VALUES
            (:run_id, :strategy_name, :symbol, :started_at, :ended_at,
             :initial_balance, :final_equity, :realized_pnl, :unrealized_pnl,
             :trade_count, :win_count, :loss_count);
        """
        with self._conn:
            self._conn.execute(sql, {"run_id": run_id, **data})
        logger.info("save_run | run_id=%s", run_id)

    def fetch_run(self, run_id: str) -> dict | None:
        cursor = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?;", (run_id,)
        )
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None

    def fetch_all_runs(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM runs ORDER BY ended_at DESC;")
        return [_row_to_dict(r) for r in cursor.fetchall()]

    # ── Trades ────────────────────────────────────────────────────────────────

    def save_trades(self, rows: list[dict]) -> None:
        sql = """
        INSERT INTO trades
            (run_id, symbol, side, entry_price, exit_price,
             quantity, pnl, opened_at, closed_at, strategy_name)
        VALUES
            (:run_id, :symbol, :side, :entry_price, :exit_price,
             :quantity, :pnl, :opened_at, :closed_at, :strategy_name);
        """
        with self._conn:
            self._conn.executemany(sql, rows)
        logger.info("save_trades | %d satır eklendi", len(rows))

    def fetch_trades(self, run_id: str | None = None) -> list[dict]:
        if run_id is not None:
            cursor = self._conn.execute(
                "SELECT * FROM trades WHERE run_id = ? ORDER BY id;", (run_id,)
            )
        else:
            cursor = self._conn.execute("SELECT * FROM trades ORDER BY id;")
        return [_row_to_dict(r) for r in cursor.fetchall()]

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()
        logger.debug("Database bağlantısı kapatıldı | path=%s", self._db_path)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def build_trade_rows(
    trades: list[Trade],
    run_id: str,
    strategy_name: str,
) -> list[dict]:
    """
    Engine'in Trade listesini (OPEN + CLOSE çiftleri) DB satırlarına dönüştürür.

    Her tamamlanan round trip (OPEN→CLOSE) için bir satır üretir.
    Kapatılmamış (hâlâ açık) pozisyonlar dahil edilmez.
    """
    pending: dict[str, Trade] = {}  # symbol → OPEN trade
    rows: list[dict] = []

    for t in trades:
        if t.action == "OPEN":
            pending[t.symbol] = t
        elif t.action == "CLOSE":
            open_t = pending.pop(t.symbol, None)
            rows.append({
                "run_id":        run_id,
                "symbol":        t.symbol,
                "side":          t.side,
                "entry_price":   open_t.price if open_t else None,
                "exit_price":    t.price,
                "quantity":      t.quantity,
                "pnl":           t.realized_pnl,
                "opened_at":     str(open_t.timestamp) if open_t else None,
                "closed_at":     str(t.timestamp),
                "strategy_name": strategy_name,
            })

    return rows
