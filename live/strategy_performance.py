"""
Strategy Performance Tracker — observer mode.

Analyzes closed trades per strategy and produces suggested weights.
Does NOT modify allocation or trading behavior.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_SUGGESTION = 10


def compute_strategy_performance(trades: list[dict]) -> dict[str, dict[str, Any]]:
    """Compute per-strategy performance from closed trades.

    Returns dict keyed by strategy name with stats and optional suggested_weight.
    """
    # Collect closed trades per strategy
    by_strategy: dict[str, list[dict]] = {}
    for t in trades:
        if t.get("action") != "CLOSE":
            continue
        name = t.get("strategy", "unknown")
        by_strategy.setdefault(name, []).append(t)

    result: dict[str, dict[str, Any]] = {}

    for strat, closed in by_strategy.items():
        total = len(closed)
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = total - wins
        win_rate = wins / total if total > 0 else 0.0
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        avg_pnl = total_pnl / total if total > 0 else 0.0

        # Approximate max drawdown from cumulative PnL sequence
        max_dd = _approx_max_drawdown(closed)

        entry: dict[str, Any] = {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_pnl": round(avg_pnl, 4),
            "max_drawdown": round(max_dd, 4),
        }

        if total < MIN_TRADES_FOR_SUGGESTION:
            entry["status"] = "insufficient_data"
            logger.info("STRATEGY PERFORMANCE | %s | insufficient_data (trades=%d)",
                        strat, total)
        else:
            entry["status"] = "ok"
            entry["suggested_weight"] = _compute_suggested_weight(win_rate, total_pnl)
            logger.info(
                "STRATEGY PERFORMANCE | %s | trades=%d | wr=%.1f%% | pnl=%.2f | suggested=%.1f",
                strat, total, win_rate * 100, total_pnl, entry["suggested_weight"],
            )

        result[strat] = entry

    return result


def _approx_max_drawdown(trades: list[dict]) -> float:
    """Approximate max drawdown from sequential closed-trade PnLs."""
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.get("pnl", 0)
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_suggested_weight(win_rate: float, total_pnl: float) -> float:
    """Derive a suggested capital weight from win_rate and total_pnl.

    score = win_rate * 0.5 + normalized_pnl * 0.5
    normalized_pnl = sigmoid(total_pnl / 50)  — maps any pnl to 0..1
    Then: score >= 0.7 → 0.7, score >= 0.5 → 0.5, else → 0.3
    Clamped to [0.2, 0.8].
    """
    def _sigmoid(x: float) -> float:
        if x >= 0:
            return 1.0 - 1.0 / (1.0 + abs(x))
        return 1.0 / (1.0 + abs(x)) - 1.0

    normalized_pnl = (_sigmoid(total_pnl / 50.0) + 1.0) / 2.0  # 0..1
    score = win_rate * 0.5 + normalized_pnl * 0.5

    if score >= 0.7:
        weight = 0.7
    elif score >= 0.5:
        weight = 0.5
    else:
        weight = 0.3

    return round(max(0.2, min(0.8, weight)), 2)
