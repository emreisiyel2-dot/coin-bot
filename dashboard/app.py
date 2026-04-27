"""
Paper Trading Dashboard — local read-only web interface.

Kullanım:
    python -m dashboard.app            # http://localhost:8080
    python -m dashboard.app --port 9000
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

APPROVED_SYMBOLS  = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT"]
LIVE_DIR          = Path(__file__).parent.parent / "live"
BREAKOUT_STATE    = LIVE_DIR / "paper_state.json"
INTRADAY_STATE    = LIVE_DIR / "intraday_state.json"
LOGS_DIR          = Path(__file__).parent.parent / "logs"
TEMPLATES_DIR     = Path(__file__).parent / "templates"
INITIAL_CASH      = 10_000.0

app   = FastAPI(title="Paper Trading Dashboard", docs_url=None, redoc_url=None)
_jinja = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


# ── State yükleyiciler ────────────────────────────────────────────────────────

def _empty_state() -> dict:
    return {
        "cash": INITIAL_CASH, "peak_equity": INITIAL_CASH,
        "positions": {}, "sym_wins": {}, "sym_losses": {}, "sym_pnl": {},
        "disabled_symbols": [], "daily_pnl": 0.0, "daily_realized_loss": 0.0,
        "trade_log": [], "equity_log": [],
    }


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return _empty_state()


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16] if iso else "—"


def _read_jsonl(path: Path, max_lines: int = 500) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    return [json.loads(l) for l in lines[-max_lines:] if l.strip()]


def _build_observability() -> dict:
    runs   = _read_jsonl(LOGS_DIR / "runs.jsonl")
    scans  = _read_jsonl(LOGS_DIR / "scans.jsonl")
    errors = _read_jsonl(LOGS_DIR / "errors.jsonl")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_scans = [s for s in scans if s.get("ts", "").startswith(today)]

    scan_counts = {}
    for s in today_scans:
        st = s.get("signal_state", "unknown")
        scan_counts[st] = scan_counts.get(st, 0) + 1

    last_runs = {}
    for r in reversed(runs):
        bot = r.get("bot", "")
        if bot not in last_runs and r.get("status") == "completed":
            last_runs[bot] = {"ts": r.get("ts"), "duration_sec": r.get("duration_sec")}

    intervals = {"breakout_v2": 14400, "rsi_reversion": 900}
    now = datetime.now(timezone.utc)
    health = {}
    for bot, interval in intervals.items():
        last_ts = last_runs.get(bot, {}).get("ts")
        if last_ts:
            try:
                elapsed = (now - datetime.fromisoformat(last_ts)).total_seconds()
                health[bot] = "OK" if elapsed < interval * 1.5 else "STALE"
            except Exception:
                health[bot] = "STALE"
        else:
            health[bot] = "NO_DATA"

    recent_scans_raw = scans[-20:]
    recent_scans = []
    for s in recent_scans_raw:
        recent_scans.append({
            "ts": _fmt_time(s.get("ts", "")),
            "bot": s.get("bot", ""),
            "symbol": s.get("symbol", ""),
            "signal_state": s.get("signal_state", ""),
            "reject_reason": s.get("reject_reason", ""),
        })

    recent_errors_raw = errors[-10:]
    recent_errors = []
    for e in recent_errors_raw:
        recent_errors.append({
            "ts": _fmt_time(e.get("ts", "")),
            "bot": e.get("bot", ""),
            "error": e.get("error", ""),
            "context": e.get("context", ""),
        })

    return {
        "scan_counts_today": scan_counts,
        "last_runs": last_runs,
        "health": health,
        "last_scan_ts": _fmt_time(scans[-1].get("ts", "")) if scans else "—",
        "total_scans_today": len(today_scans),
        "recent_scans": recent_scans,
        "recent_errors": recent_errors,
        "total_errors": len(_read_jsonl(LOGS_DIR / "errors.jsonl", 99999)),
    }


def _build_daily_trades() -> dict:
    trades = _read_jsonl(LOGS_DIR / "trades.jsonl", 99999)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    today_closes = [
        t for t in trades
        if t.get("action") == "CLOSE" and t.get("ts", "").startswith(today)
    ]

    bk_closes = [t for t in today_closes if t.get("strategy") == "breakout_v2"
                 or t.get("bot") == "breakout_v2"]
    rr_closes = [t for t in today_closes if t.get("strategy") == "rsi_reversion"
                 or t.get("bot") == "rsi_reversion"]

    def _stats(closes):
        total = len(closes)
        wins = sum(1 for t in closes if (t.get("pnl") or 0) > 0)
        losses = total - wins
        return {"total": total, "wins": wins, "losses": losses}

    bk = _stats(bk_closes)
    rr = _stats(rr_closes)
    combined = _stats(today_closes)
    combined["win_rate"] = round(combined["wins"] / combined["total"] * 100, 1) if combined["total"] > 0 else 0.0

    return {"breakout": bk, "intraday": rr, "combined": combined}


def _pair_trades(trade_log: list) -> list:
    stack: dict[str, list] = {}
    pairs = []
    for t in trade_log:
        sym = t["symbol"]
        if t["action"] == "OPEN":
            stack.setdefault(sym, []).append(t)
        elif t["action"] == "CLOSE":
            entry_price = "—"
            if stack.get(sym):
                entry_price = stack[sym].pop(0)["price"]
            pairs.append({
                "symbol":      sym,
                "entry_price": entry_price,
                "exit_price":  t.get("price", "—"),
                "pnl":         t.get("pnl", 0.0),
                "reason":      t.get("reason", "—"),
                "time":        _fmt_time(t.get("time", "")),
            })
    return list(reversed(pairs))


def _strategy_summary(state: dict, strategy_name: str, timeframe: str) -> dict:
    """Tek bir strateji state'inden özet kart verisi üretir."""
    positions  = state.get("positions", {})
    trade_log  = state.get("trade_log", [])
    equity_log = state.get("equity_log", [])

    eq = state["cash"] + sum(
        p["quantity"] * p["entry_price"] for p in positions.values()
    )
    closed = _pair_trades(trade_log)
    wins   = sum(1 for t in closed if t["pnl"] > 0)
    total  = len(closed)

    eq_labels, eq_values = [], []
    for snap in equity_log[-300:]:
        eq_labels.append(_fmt_time(snap["time"]))
        eq_values.append(round(snap["equity"], 2))

    open_positions = []
    allocated_capital = 0.0
    for sym, pos in positions.items():
        cur_price = pos["entry_price"]
        unrealized = 0.0
        unrealized_pct = 0.0
        duration_min = 0
        size_usdt = round(pos.get("size_usdt", pos["quantity"] * pos["entry_price"]), 2)
        allocated_capital += size_usdt
        try:
            entry = pos["entry_price"]
            qty = pos["quantity"]
            unrealized = (cur_price - entry) * qty
            unrealized_pct = (cur_price - entry) / entry * 100 if entry > 0 else 0.0
            et = pos.get("entry_time", "")
            if et:
                entry_dt = datetime.fromisoformat(et)
                duration_min = int((datetime.now(timezone.utc) - entry_dt).total_seconds() / 60)
        except Exception:
            pass
        sl_price = pos.get("trail_sl", pos["sl"]) if pos.get("trail_active") else pos["sl"]
        max_loss = round(size_usdt * abs(pos["entry_price"] - sl_price) / pos["entry_price"], 2) if pos["entry_price"] > 0 else 0.0
        open_positions.append({
            "symbol":            sym,
            "strategy":          strategy_name,
            "entry_price":       pos["entry_price"],
            "size_usdt":         size_usdt,
            "tp":                pos["tp"],
            "sl":                sl_price,
            "trailing":          "Aktif" if pos.get("trail_active") else "—",
            "entry_time":        _fmt_time(pos.get("entry_time", "")),
            "unrealized_pnl":    round(unrealized, 2),
            "unrealized_pct":    round(unrealized_pct, 2),
            "duration_min":      duration_min,
            "risk_per_trade":    f"{pos.get('risk_per_trade_pct', 0.0) * 100:.1f}%",
            "alloc_pct_strategy": round(size_usdt / eq * 100, 1) if eq > 0 else 0.0,
            "alloc_pct_total":   0.0,
            "max_loss":          max_loss,
        })

    sym_perf = []
    for sym in APPROVED_SYMBOLS:
        w = state["sym_wins"].get(sym, 0)
        l = state["sym_losses"].get(sym, 0)
        t = w + l
        sym_perf.append({
            "symbol":   sym,
            "trades":   t,
            "wins":     w,
            "losses":   l,
            "win_rate": round(w / t * 100, 1) if t > 0 else 0.0,
            "pnl":      round(state["sym_pnl"].get(sym, 0.0), 2),
            "status":   "DISABLED" if sym in state.get("disabled_symbols", []) else "active",
        })

    utilization_pct = round(allocated_capital / eq * 100, 1) if eq > 0 else 0.0

    return {
        "name":               strategy_name,
        "timeframe":          timeframe,
        "equity":             round(eq, 2),
        "cash":               round(state["cash"], 2),
        "starting_capital":   INITIAL_CASH,
        "allocated_capital":  round(allocated_capital, 2),
        "utilization_pct":    utilization_pct,
        "peak_equity":        round(state.get("peak_equity", INITIAL_CASH), 2),
        "return_pct":         round((eq - INITIAL_CASH) / INITIAL_CASH * 100, 2),
        "daily_pnl":          round(state.get("daily_pnl", 0.0), 2),
        "open_count":         len(positions),
        "closed_count":       total,
        "win_rate":           round(wins / total * 100, 1) if total > 0 else 0.0,
        "last_run":           (_fmt_time(equity_log[-1]["time"]) + " UTC") if equity_log else "—",
        "open_positions":     open_positions,
        "closed_trades":      closed[:50],
        "sym_perf":           sym_perf,
        "eq_labels":          eq_labels,
        "eq_values":          eq_values,
    }


def _build_context() -> dict:
    bk = _load(BREAKOUT_STATE)
    id_ = _load(INTRADAY_STATE)

    breakout  = _strategy_summary(bk,  "breakout_v2",   "4H")
    intraday  = _strategy_summary(id_, "rsi_reversion", "15m")

    combined_equity = breakout["equity"] + intraday["equity"]
    combined_return = round((combined_equity - INITIAL_CASH * 2) / (INITIAL_CASH * 2) * 100, 2)
    combined_closed = breakout["closed_count"] + intraday["closed_count"]
    combined_open   = breakout["open_count"] + intraday["open_count"]
    combined_dpnl   = round(breakout["daily_pnl"] + intraday["daily_pnl"], 2)
    combined_cash   = round(breakout["cash"] + intraday["cash"], 2)
    combined_alloc  = round(breakout["allocated_capital"] + intraday["allocated_capital"], 2)
    combined_util   = round(combined_alloc / combined_equity * 100, 1) if combined_equity > 0 else 0.0

    for p in breakout["open_positions"]:
        p["alloc_pct_total"] = round(p["size_usdt"] / combined_equity * 100, 1) if combined_equity > 0 else 0.0
    for p in intraday["open_positions"]:
        p["alloc_pct_total"] = round(p["size_usdt"] / combined_equity * 100, 1) if combined_equity > 0 else 0.0

    return {
        "breakout":            breakout,
        "intraday":            intraday,
        "combined_equity":     round(combined_equity, 2),
        "combined_return":     combined_return,
        "combined_closed":     combined_closed,
        "combined_open":       combined_open,
        "combined_dpnl":       combined_dpnl,
        "combined_cash":       combined_cash,
        "combined_allocated":  combined_alloc,
        "combined_util_pct":   combined_util,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    ctx  = _build_context()
    ctx["obs"] = _build_observability()
    ctx["daily_trades"] = _build_daily_trades()
    html = _jinja.get_template("index.html").render(**ctx)
    return HTMLResponse(content=html)


@app.get("/api/state")
async def api_state():
    return {
        "breakout":  _load(BREAKOUT_STATE),
        "intraday":  _load(INTRADAY_STATE),
    }


@app.get("/api/observability")
async def api_observability():
    return _build_observability()


def main() -> None:
    p = argparse.ArgumentParser(description="Paper Trading Dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    print(f"\n  Dashboard çalışıyor → http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
