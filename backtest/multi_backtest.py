"""
Multi-symbol backtest runner.

Kullanım:
    python -m backtest.runner --strategy swing --days 90
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from execution.multi_engine import MultiRunSummary, MultiSymbolEngine, SymbolSummary
from portfolio.portfolio import Portfolio
from reporting.report import compute_metrics
from risk.risk_manager import RiskManager
from scanner.opportunity_scanner import OpportunityScanner
from storage.database import Database, build_trade_rows

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    summary: MultiRunSummary
    initial_cash: float
    strategy_name: str
    symbols: list[str]
    run_id: str

    @property
    def total_return_pct(self) -> float:
        return (self.summary.final_equity - self.initial_cash) / self.initial_cash * 100

    @property
    def per_symbol_table(self) -> list[dict]:
        rows = []
        for sym, s in self.summary.per_symbol.items():
            sel = self.summary.scanner_stats.selection_counts.get(sym, 0)
            rows.append({
                "symbol":       sym,
                "trades":       s.trade_count,
                "realized_pnl": round(s.realized_pnl, 2),
                "win_rate":     round(s.win_rate * 100, 1),
                "wins":         s.win_count,
                "losses":       s.loss_count,
                "scanner_sel":  sel,
                "veto":         self.summary.scanner_stats.veto_counts.get(sym, 0),
                "conv_rate":    round(self.summary.scanner_stats.conversion_rate(sym) * 100, 1),
            })
        rows.sort(key=lambda r: r["realized_pnl"], reverse=True)
        return rows

    def best_symbol(self) -> str | None:
        t = self.per_symbol_table
        return t[0]["symbol"] if t else None

    def worst_symbol(self) -> str | None:
        t = self.per_symbol_table
        return t[-1]["symbol"] if t else None

    def print_summary(self) -> None:
        close_trades = [t for t in self.summary.trades if t.action == "CLOSE"]
        trade_dicts = [
            {"pnl": t.realized_pnl, "symbol": t.symbol,
             "opened_at": str(t.timestamp), "closed_at": str(t.timestamp)}
            for t in close_trades
        ]
        m = compute_metrics(trade_dicts, self.initial_cash)

        pnls = [t.realized_pnl for t in close_trades if t.realized_pnl is not None]
        wins  = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        avg_win  = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        wr = m.win_rate
        expectancy = wr * avg_win + (1 - wr) * avg_loss

        # Exit reason dökümü
        reasons: dict[str, int] = {}
        for t in close_trades:
            r = t.exit_reason or "UNKNOWN"
            reasons[r] = reasons.get(r, 0) + 1

        pf = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "∞"
        print("\n" + "═" * 62)
        print(f"  MULTI-SYMBOL BACKTEST — {self.strategy_name.upper()}")
        print("═" * 62)
        print(f"  Semboller      : {', '.join(self.symbols)}")
        print(f"  Başlangıç      : {self.initial_cash:,.2f} USDT")
        print(f"  Final equity   : {self.summary.final_equity:,.2f} USDT")
        print(f"  Toplam getiri  : {self.total_return_pct:+.2f}%")
        print(f"  Net PnL        : {m.total_pnl:+.2f} USDT")
        print(f"  Trade sayısı   : {self.summary.total_trade_count}")
        print(f"  Win rate       : {m.win_rate * 100:.1f}%")
        print(f"  Profit factor  : {pf}")
        print(f"  Max drawdown   : {m.max_drawdown:+.2f} USDT")
        print(f"  Max eş zamanlı : {self.summary.max_concurrent_positions}")
        print()
        print(f"  Avg win        : {avg_win:+.2f} USDT")
        print(f"  Avg loss       : {avg_loss:+.2f} USDT")
        print(f"  Expectancy     : {expectancy:+.2f} USDT/trade")
        print()
        print(f"  Exit dökümü    :", end="")
        for r, cnt in sorted(reasons.items()):
            print(f"  {r}={cnt}", end="")
        print()
        print()
        print(f"  Scanner dönüşüm oranı: {self.summary.scanner_stats.total_conversion_rate() * 100:.1f}%")
        print()
        print(f"  {'Symbol':<14} {'Trade':>5} {'PnL':>9} {'Win%':>6} {'W':>3} {'L':>3} {'Seçildi':>7} {'Veto':>5} {'Conv%':>6}")
        print("  " + "-" * 58)
        for row in self.per_symbol_table:
            print(
                f"  {row['symbol']:<14} {row['trades']:>5} "
                f"{row['realized_pnl']:>9.2f} {row['win_rate']:>5.1f}% "
                f"{row['wins']:>3} {row['losses']:>3} "
                f"{row['scanner_sel']:>7} {row['veto']:>5} {row['conv_rate']:>5.1f}%"
            )
        best, worst = self.best_symbol(), self.worst_symbol()
        print()
        print(f"  En iyi  coin: {best}")
        print(f"  En kötü coin: {worst}")
        print("═" * 62 + "\n")


class MultiBacktest:
    def __init__(
        self,
        strategy_factory,
        strategy_name: str = "unknown",
        initial_cash: float = 10_000.0,
        position_size_pct: float = 0.10,
        take_profit_pct: float = 0.02,
        stop_loss_pct: float = 0.01,
        partial_tp1_pct: float = 0.0,
        partial_tp1_size: float = 0.5,
        trailing_stop_pct: float = 0.0,
        trailing_activate_pct: float = 0.0,
        scanner_config: dict | None = None,
        risk_config: dict | None = None,
        simulator=None,
        db: Database | None = None,
    ) -> None:
        self._strategy_factory = strategy_factory
        self._strategy_name = strategy_name
        self._initial_cash = initial_cash
        self._position_size_pct = position_size_pct
        self._take_profit_pct = take_profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._partial_tp1_pct = partial_tp1_pct
        self._partial_tp1_size = partial_tp1_size
        self._trailing_stop_pct = trailing_stop_pct
        self._trailing_activate_pct = trailing_activate_pct
        self._scanner_config = scanner_config or {}
        self._risk_config = risk_config or {}
        self._simulator = simulator
        self._db = db

    def run(self, data_dict: dict[str, pd.DataFrame]) -> BacktestResult:
        if not data_dict:
            raise ValueError("data_dict boş olamaz")

        symbols = list(data_dict.keys())
        run_id = str(uuid.uuid4())[:8]
        started_at = datetime.now(timezone.utc).isoformat()

        portfolio = Portfolio(initial_cash=self._initial_cash)
        risk_manager = RiskManager(portfolio=portfolio, config=self._risk_config)
        scanner = OpportunityScanner(config=self._scanner_config)

        symbol_strategies = {
            symbol: (self._strategy_factory(), df)
            for symbol, df in data_dict.items()
        }

        engine = MultiSymbolEngine(
            symbol_strategies=symbol_strategies,
            portfolio=portfolio,
            risk_manager=risk_manager,
            scanner=scanner,
            position_size_pct=self._position_size_pct,
            take_profit_pct=self._take_profit_pct,
            stop_loss_pct=self._stop_loss_pct,
            partial_tp1_pct=self._partial_tp1_pct,
            partial_tp1_size=self._partial_tp1_size,
            trailing_stop_pct=self._trailing_stop_pct,
            trailing_activate_pct=self._trailing_activate_pct,
            simulator=self._simulator,
        )

        logger.info("MultiBacktest başlıyor | %d symbol | cash=%.2f", len(data_dict), self._initial_cash)
        summary = engine.run()
        ended_at = datetime.now(timezone.utc).isoformat()
        logger.info("MultiBacktest tamamlandı | trades=%d | equity=%.2f",
                    summary.total_trade_count, summary.final_equity)

        result = BacktestResult(
            summary=summary,
            initial_cash=self._initial_cash,
            strategy_name=self._strategy_name,
            symbols=symbols,
            run_id=run_id,
        )

        if self._db is not None:
            self._save_to_db(result, summary, run_id, started_at, ended_at, symbols)

        return result

    def _save_to_db(
        self,
        result: BacktestResult,
        summary: MultiRunSummary,
        run_id: str,
        started_at: str,
        ended_at: str,
        symbols: list[str],
    ) -> None:
        close_trades = [t for t in summary.trades if t.action == "CLOSE"]
        wins = sum(1 for t in close_trades if t.realized_pnl is not None and t.realized_pnl > 0)
        losses = len(close_trades) - wins
        realized_pnl = sum(t.realized_pnl for t in close_trades if t.realized_pnl is not None)

        # symbol listesini JSON serialize et — ileride sorgulanabilsin
        symbol_json = json.dumps(symbols)

        self._db.save_run(run_id, {
            "strategy_name": self._strategy_name,
            "symbol":        symbol_json,
            "started_at":    started_at,
            "ended_at":      ended_at,
            "initial_balance": self._initial_cash,
            "final_equity":  summary.final_equity,
            "realized_pnl":  realized_pnl,
            "unrealized_pnl": 0.0,
            "trade_count":   summary.total_trade_count,
            "win_count":     wins,
            "loss_count":    losses,
        })

        rows = build_trade_rows(summary.trades, run_id, self._strategy_name)
        if rows:
            self._db.save_trades(rows)

        logger.info("Backtest DB'ye kaydedildi | run_id=%s", run_id)
