import json
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ReportMetrics:
    # Temel
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    total_pnl: float
    avg_pnl_per_trade: float
    max_win: float
    max_loss: float
    # Performans
    profit_factor: float
    max_drawdown: float
    equity_curve: list
    # Davranış
    trades_by_hour: dict
    avg_duration_hours: float
    symbol_pnl: dict


def compute_metrics(trades: list, initial_balance: float) -> ReportMetrics:
    if not trades:
        return ReportMetrics(
            total_trades=0, win_count=0, loss_count=0,
            win_rate=0.0, total_pnl=0.0, avg_pnl_per_trade=0.0,
            max_win=0.0, max_loss=0.0,
            profit_factor=0.0, max_drawdown=0.0,
            equity_curve=[initial_balance],
            trades_by_hour={}, avg_duration_hours=0.0, symbol_pnl={},
        )

    pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    total_pnl = sum(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    equity_curve = _equity_curve(trades, initial_balance)
    max_drawdown = _max_drawdown(equity_curve)

    return ReportMetrics(
        total_trades=total,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_count / total if total > 0 else 0.0,
        total_pnl=total_pnl,
        avg_pnl_per_trade=total_pnl / total if total > 0 else 0.0,
        max_win=max(wins) if wins else 0.0,
        max_loss=min(losses) if losses else 0.0,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        equity_curve=equity_curve,
        trades_by_hour=_trades_by_hour(trades),
        avg_duration_hours=_avg_duration(trades),
        symbol_pnl=_symbol_pnl(trades),
    )


def _equity_curve(trades: list, initial_balance: float) -> list:
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at") or "")
    curve = [initial_balance]
    equity = initial_balance
    for t in sorted_trades:
        if t.get("pnl") is not None:
            equity += t["pnl"]
            curve.append(equity)
    return curve


def _max_drawdown(curve: list) -> float:
    if len(curve) < 2:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for val in curve:
        if val > peak:
            peak = val
        dd = val - peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _trades_by_hour(trades: list) -> dict:
    counts: dict = {}
    for t in trades:
        opened = t.get("opened_at")
        if opened:
            hour = datetime.fromisoformat(opened).hour
            counts[hour] = counts.get(hour, 0) + 1
    return counts


def _avg_duration(trades: list) -> float:
    durations = []
    for t in trades:
        if t.get("opened_at") and t.get("closed_at"):
            open_dt = datetime.fromisoformat(t["opened_at"])
            close_dt = datetime.fromisoformat(t["closed_at"])
            durations.append((close_dt - open_dt).total_seconds() / 3600)
    return sum(durations) / len(durations) if durations else 0.0


def _symbol_pnl(trades: list) -> dict:
    result: dict = {}
    for t in trades:
        sym = t.get("symbol", "")
        pnl = t.get("pnl") or 0.0
        result[sym] = result.get(sym, 0.0) + pnl
    return result


def metrics_to_dict(m: ReportMetrics) -> dict:
    return {
        "total_trades": m.total_trades,
        "win_count": m.win_count,
        "loss_count": m.loss_count,
        "win_rate": m.win_rate,
        "total_pnl": m.total_pnl,
        "avg_pnl_per_trade": m.avg_pnl_per_trade,
        "max_win": m.max_win,
        "max_loss": m.max_loss,
        "profit_factor": m.profit_factor if m.profit_factor != float("inf") else "inf",
        "max_drawdown": m.max_drawdown,
        "equity_curve": m.equity_curve,
        "trades_by_hour": {str(k): v for k, v in m.trades_by_hour.items()},
        "avg_duration_hours": m.avg_duration_hours,
        "symbol_pnl": m.symbol_pnl,
    }


def format_report(m: ReportMetrics, run_data: dict | None = None) -> str:
    pf = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "∞"
    run_line = ""
    if run_data:
        run_line = (
            f"  Strateji  : {run_data.get('strategy_name', '-')}\n"
            f"  Sembol    : {run_data.get('symbol', '-')}\n"
            f"  Başlangıç : {run_data.get('started_at', '-')}\n"
            f"  Bitiş     : {run_data.get('ended_at', '-')}\n"
        )

    top_hour = ""
    if m.trades_by_hour:
        peak_h = max(m.trades_by_hour, key=m.trades_by_hour.get)
        top_hour = f"  En yoğun saat : {peak_h:02d}:xx ({m.trades_by_hour[peak_h]} işlem)\n"

    sym_lines = "\n".join(
        f"    {sym}: {pnl:+.2f} USDT"
        for sym, pnl in m.symbol_pnl.items()
    )

    return (
        f"\n{'═'*54}\n"
        f"  PAPER TRADING RAPORU\n"
        f"{'═'*54}\n"
        f"{run_line}"
        f"\n── Temel ──────────────────────────────────────────\n"
        f"  Toplam işlem  : {m.total_trades}\n"
        f"  Kazanan       : {m.win_count}  |  Kaybeden: {m.loss_count}\n"
        f"  Win rate      : {m.win_rate*100:.1f}%\n"
        f"  Toplam PnL    : {m.total_pnl:+.2f} USDT\n"
        f"  Ortalama PnL  : {m.avg_pnl_per_trade:+.2f} USDT\n"
        f"  Max kazanç    : {m.max_win:+.2f} USDT\n"
        f"  Max kayıp     : {m.max_loss:+.2f} USDT\n"
        f"\n── Performans ──────────────────────────────────────\n"
        f"  Profit factor : {pf}\n"
        f"  Max drawdown  : {m.max_drawdown:+.2f} USDT\n"
        f"  Başlangıç EQ  : {m.equity_curve[0]:,.2f} USDT\n"
        f"  Bitiş EQ      : {m.equity_curve[-1]:,.2f} USDT\n"
        f"\n── Davranış ────────────────────────────────────────\n"
        f"  Ort. süre     : {m.avg_duration_hours:.1f} saat\n"
        f"{top_hour}"
        f"\n── Sembol PnL ──────────────────────────────────────\n"
        f"{sym_lines}\n"
        f"{'═'*54}\n"
    )


class Reporter:
    """Storage'dan veri çekip rapor üreten orkestratör."""

    def __init__(self, db) -> None:
        self._db = db

    def compute(self, run_id: str) -> ReportMetrics:
        trades = self._db.fetch_trades(run_id=run_id)
        run = self._db.fetch_run(run_id)
        initial_balance = run["initial_balance"] if run else 0.0
        return compute_metrics(trades, initial_balance)

    def print_run(self, run_id: str) -> None:
        metrics = self.compute(run_id)
        run = self._db.fetch_run(run_id)
        print(format_report(metrics, run))

    def export_json(self, run_id: str, output_path: str) -> None:
        metrics = self.compute(run_id)
        with open(output_path, "w") as f:
            json.dump(metrics_to_dict(metrics), f, indent=2)
        logger.info("JSON export: %s", output_path)

    def export_csv(self, run_id: str, output_path: str) -> None:
        import csv
        trades = self._db.fetch_trades(run_id=run_id)
        if not trades:
            return
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)
        logger.info("CSV export: %s", output_path)
