"""
Trade Quality Analyzer — reads logs, reports trade performance.

Usage:
    python -m tools.analyze_trades
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"


def _read_jsonl(filename: str) -> list[dict]:
    path = LOGS_DIR / filename
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().strip().splitlines() if l.strip()]


def _parse_ts(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _build_trade(close_log: dict, open_log: dict | None) -> dict:
    """Build a paired trade dict from close + open log entries."""
    close_strat = close_log.get("strategy", "unknown")
    open_strat = (open_log or {}).get("strategy", close_strat)
    entry_price = (open_log or {}).get("price")
    exit_price = close_log.get("price")

    return {
        "symbol": close_log.get("symbol", ""),
        "strategy": close_strat,
        "true_entry_strategy": open_strat,
        "entry_strategy": close_log.get("entry_strategy"),
        "exit_runner": close_log.get("exit_runner"),
        "action": close_log.get("action", ""),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl": close_log.get("pnl"),
        "pnl_pct": close_log.get("pnl_pct"),
        "reason": close_log.get("reason", ""),
        "holding_minutes": close_log.get("holding_minutes"),
        "entry_time": (open_log or {}).get("time") or (open_log or {}).get("ts"),
        "exit_time": close_log.get("time") or close_log.get("ts"),
        "size_usdt": (open_log or {}).get("size_usdt"),
        "quantity": close_log.get("quantity") or (open_log or {}).get("quantity"),
        "open_bot": (open_log or {}).get("bot", ""),
        "close_bot": close_log.get("bot", ""),
        "legacy_log_repaired": False,
    }


def _repair_legacy(p: dict) -> None:
    """Infer missing fields and fix wrong strategy attribution in legacy trades."""
    repairs: list[str] = []
    true_strat = p.get("true_entry_strategy")
    logged_strat = p.get("strategy")

    # Fix strategy attribution: if open says momentum but close says rsi_reversion
    if true_strat and true_strat != logged_strat:
        p["strategy"] = true_strat
        repairs.append(f"strategy: {logged_strat} → {true_strat}")

    if not p.get("entry_strategy") and true_strat:
        p["entry_strategy"] = true_strat
        p["inferred"] = True
        repairs.append("entry_strategy inferred from open log")

    if not p.get("exit_runner") and p.get("close_bot"):
        p["exit_runner"] = p["close_bot"]
        p["inferred"] = True
        repairs.append("exit_runner inferred from close bot name")

    entry_price = p.get("entry_price")
    exit_price = p.get("exit_price")
    pnl = p.get("pnl")

    # Infer pnl_pct
    if p.get("pnl_pct") is None and entry_price and exit_price and entry_price > 0:
        p["pnl_pct"] = round((exit_price - entry_price) / entry_price * 100, 4)
        p["inferred"] = True
        repairs.append("pnl_pct calculated from prices")

    # Infer holding_minutes
    if p.get("holding_minutes") is None:
        entry_dt = _parse_ts(p.get("entry_time"))
        exit_dt = _parse_ts(p.get("exit_time"))
        if entry_dt and exit_dt:
            p["holding_minutes"] = round((exit_dt - entry_dt).total_seconds() / 60.0, 1)
            p["inferred"] = True
            repairs.append("holding_minutes calculated from timestamps")

    # Infer entry_price from pnl if missing
    if not entry_price and pnl is not None and exit_price:
        qty = p.get("quantity")
        if qty and qty > 0:
            p["entry_price"] = round((exit_price * qty - (pnl or 0)) / qty, 4)
            p["inferred"] = True
            repairs.append("entry_price inferred from pnl+exit_price+qty")

    if repairs:
        p["legacy_log_repaired"] = True
        p["repairs"] = repairs


def analyze() -> dict:
    trades_raw = _read_jsonl("trades.jsonl")

    # Pair OPEN → CLOSE by symbol, preserving order
    open_stack: dict[str, dict] = {}
    paired: list[dict] = []

    for t in trades_raw:
        sym = t.get("symbol", "")
        action = t.get("action", "")
        if action == "OPEN":
            open_stack[sym] = t
        elif action in ("CLOSE", "PARTIAL_CLOSE"):
            entry = open_stack.pop(sym, None)
            paired.append(_build_trade(t, entry))

    # Reprocess legacy trades where strategy was wrong
    for p in paired:
        _repair_legacy(p)

    # Global stats
    closes = [p for p in paired if p["action"] == "CLOSE"]
    wins = [p for p in closes if (p.get("pnl") or 0) > 0]
    losses = [p for p in closes if (p.get("pnl") or 0) <= 0]
    total_pnl = sum(p.get("pnl", 0) for p in closes)

    sl_count = len([p for p in closes if "stop_loss" in (p.get("reason") or "").lower()])
    tp_count = len([p for p in closes if "take_profit" in (p.get("reason") or "").lower()])
    trail_count = len([p for p in closes if "trailing" in (p.get("reason") or "").lower()])
    time_count = len([p for p in closes if "time_exit" in (p.get("reason") or "").lower()])

    global_stats = {
        "completed_trades": len(closes),
        "partial_closes": len([p for p in paired if p["action"] == "PARTIAL_CLOSE"]),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closes) * 100, 1) if closes else 0.0,
        "total_pnl": round(total_pnl, 4),
        "avg_pnl": round(total_pnl / len(closes), 4) if closes else 0.0,
        "stop_loss_count": sl_count,
        "take_profit_count": tp_count,
        "trailing_stop_count": trail_count,
        "time_exit_count": time_count,
    }

    # Per-strategy stats
    by_strategy: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "trades_list": []
    })
    for p in closes:
        s = p["strategy"]
        by_strategy[s]["trades"] += 1
        by_strategy[s]["pnl"] += p.get("pnl", 0)
        if (p.get("pnl") or 0) > 0:
            by_strategy[s]["wins"] += 1
        else:
            by_strategy[s]["losses"] += 1
        by_strategy[s]["trades_list"].append(p)

    by_strategy_out = {}
    for strat, data in by_strategy.items():
        by_strategy_out[strat] = {
            "trades": data["trades"],
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": round(data["wins"] / data["trades"] * 100, 1) if data["trades"] else 0.0,
            "total_pnl": round(data["pnl"], 4),
            "avg_pnl": round(data["pnl"] / data["trades"], 4) if data["trades"] else 0.0,
        }

    report = {
        "global": global_stats,
        "by_strategy": by_strategy_out,
        "trades": paired,
    }

    return report


def print_summary(report: dict) -> None:
    g = report["global"]
    trades = report.get("trades", [])

    print("\n" + "=" * 66)
    print("  TRADE QUALITY REPORT")
    print("=" * 66)
    print(f"  Completed trades:  {g['completed_trades']}")
    print(f"  Partial closes:    {g['partial_closes']}")
    print(f"  Wins / Losses:     {g['wins']} / {g['losses']}")
    print(f"  Win rate:          {g['win_rate']}%")
    print(f"  Total PnL:         {g['total_pnl']:+.4f} USDT")
    print(f"  Avg PnL:           {g['avg_pnl']:+.4f} USDT")
    print(f"  Exit reasons:      SL={g['stop_loss_count']}  TP={g['take_profit_count']}  "
          f"Trail={g['trailing_stop_count']}  Time={g['time_exit_count']}")

    print(f"\n  {'─' * 62}")
    print(f"  PER-STRATEGY")
    print(f"  {'─' * 62}")
    for strat, data in report.get("by_strategy", {}).items():
        print(f"  {strat:30s}  trades={data['trades']}  "
              f"WR={data['win_rate']}%  PnL={data['total_pnl']:+.4f}")

    print(f"\n  {'─' * 62}")
    print(f"  TRADE DETAIL")
    print(f"  {'─' * 62}")
    for t in trades:
        pnl = t.get("pnl") or 0
        icon = "+" if pnl > 0 else "-"
        repaired = " [repaired]" if t.get("legacy_log_repaired") else ""
        print(f"  {icon} {t['symbol']:<12} {t['strategy']:<22} "
              f"entry={t.get('entry_price', '?')}  exit={t.get('exit_price', '?')}  "
              f"pnl={pnl:+.4f}  reason={t.get('reason')}  "
              f"hold={t.get('holding_minutes', '?')}min"
              f"{repaired}")
        if t.get("repairs"):
            for r in t["repairs"]:
                print(f"    ↳ {r}")

    # Observations
    print(f"\n  {'─' * 62}")
    print(f"  OBSERVATIONS")
    print(f"  {'─' * 62}")
    for t in trades:
        entry = t.get("entry_price")
        exit_p = t.get("exit_price")
        if entry and exit_p and (pnl := (t.get("pnl") or 0)) < 0:
            print(f"  - {t['symbol']} lost {abs(pnl):.4f} USDT via {t.get('reason')}. "
                  f"Entry {entry} → Exit {exit_p}. "
                  f"Strategy: {t['strategy']}")

    if not trades:
        print("  No completed trades yet.")

    print("=" * 66 + "\n")


def main() -> None:
    report = analyze()
    print_summary(report)
    out_path = LOGS_DIR / "trade_quality_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Saved → {out_path}")


if __name__ == "__main__":
    main()
