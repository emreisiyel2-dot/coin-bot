"""
RSI Reversion Strategy — Yapısal Optimizasyon

4 fazda parametreleri test eder ve en iyi konfigürasyonu bulur.

Kullanım:
    python -m backtest.rsi_reversion_optimize            # tüm fazlar (90 gün)
    python -m backtest.rsi_reversion_optimize --days 60
    python -m backtest.rsi_reversion_optimize --phase 2  # sadece faz 2
    python -m backtest.rsi_reversion_optimize --no-fetch # cache'li veriyi kullan
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from data.market_data import fetch_ohlcv_range
from indicators.indicators import compute_atr, compute_ema, compute_rsi

# ── Sabitler ──────────────────────────────────────────────────────────────────
SYMBOLS       = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT"]
TIMEFRAME     = "15m"
POSITION_SIZE = 1_000.0     # standart pozisyon büyüklüğü ($) — karşılaştırma için sabit
INITIAL_EQ    = 10_000.0
CACHE_DIR     = Path(__file__).parent / "_rr_cache"

# Baseline config
BASE_RSI      = 33.0
BASE_TP       = 0.015
BASE_SL       = 0.008
BASE_COOLDOWN = 120


# ── Veri çekme / cache ────────────────────────────────────────────────────────

def _cache_path(sym: str) -> Path:
    return CACHE_DIR / f"{sym.replace('/', '_')}.parquet"


def fetch_all(days: int) -> dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(exist_ok=True)
    now   = datetime.now(timezone.utc)
    since = int((now - timedelta(days=days)).timestamp() * 1000)
    until = int(now.timestamp() * 1000)
    data  = {}
    for sym in SYMBOLS:
        print(f"  Veri çekiliyor: {sym} ({days} gün)...", flush=True)
        df = fetch_ohlcv_range(sym, TIMEFRAME, since=since, until=until, chunk_size=1000)
        df.to_parquet(_cache_path(sym), index=False)
        data[sym] = df
        print(f"  → {len(df)} bar", flush=True)
    return data


def load_cache() -> dict[str, pd.DataFrame]:
    data = {}
    for sym in SYMBOLS:
        p = _cache_path(sym)
        if not p.exists():
            raise FileNotFoundError(f"Cache bulunamadı: {p}. --no-fetch olmadan çalıştırın.")
        data[sym] = pd.read_parquet(p)
    return data


# ── Backtest çekirdeği ────────────────────────────────────────────────────────

def _backtest_symbol(df: pd.DataFrame, symbol: str, params: dict) -> tuple[list, list]:
    """
    Tek sembol üzerinde bar-by-bar backtest.

    Mekanik:
      Bar i → sinyal → close[i]'den giriş
      Bar i+1 → low <= SL → SL kapanışı (muhafazakâr, önce kontrol)
               high >= TP → TP kapanışı
    """
    rsi_oversold  = params["rsi_oversold"]
    rsi_period    = params.get("rsi_period", 7)
    ema_fast_p    = params.get("ema_fast_period", 20)
    ema_slow_p    = params.get("ema_slow_period", 50)
    tp_pct        = params["tp_pct"]
    sl_pct        = params["sl_pct"]
    cooldown_bars = max(1, params["cooldown_minutes"] // 15)
    atr_low_pct   = params.get("atr_low_pct", 0.0)
    atr_high_pct  = params.get("atr_high_pct", 0.0)
    use_atr       = atr_low_pct > 0 or atr_high_pct > 0

    min_bars = max(ema_slow_p + 5, rsi_period + 5, 20)
    if len(df) <= min_bars + 1:
        return [], []

    ema_fast_v = compute_ema(df, ema_fast_p).values
    ema_slow_v = compute_ema(df, ema_slow_p).values
    rsi_v      = compute_rsi(df, rsi_period).values
    atr_v      = compute_atr(df, 14).values if use_atr else None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    trades:       list = []
    equity_curve: list = []
    equity = INITIAL_EQ

    in_position    = False
    entry_price    = tp = sl = 0.0
    last_close_bar = -(cooldown_bars + 1)

    for i in range(min_bars, len(df) - 1):
        nxt = i + 1

        if in_position:
            hit_sl = lows[nxt] <= sl
            hit_tp = highs[nxt] >= tp

            if hit_sl:
                pnl_usd = POSITION_SIZE * (-sl_pct)
                equity += pnl_usd
                trades.append({"symbol": symbol, "outcome": "SL",
                                "pnl_usd": round(pnl_usd, 4), "pnl_pct": -sl_pct})
                in_position    = False
                last_close_bar = nxt
            elif hit_tp:
                pnl_usd = POSITION_SIZE * tp_pct
                equity += pnl_usd
                trades.append({"symbol": symbol, "outcome": "TP",
                                "pnl_usd": round(pnl_usd, 4), "pnl_pct": tp_pct})
                in_position    = False
                last_close_bar = nxt

            equity_curve.append(equity)
            continue

        if (i - last_close_bar) < cooldown_bars:
            equity_curve.append(equity)
            continue

        if np.isnan(rsi_v[i]) or np.isnan(ema_fast_v[i]) or np.isnan(ema_slow_v[i]):
            equity_curve.append(equity)
            continue

        rsi_ok   = rsi_v[i] < rsi_oversold
        trend_ok = closes[i] > ema_slow_v[i]
        ema_ok   = ema_fast_v[i] > ema_slow_v[i]

        vol_ok = True
        if use_atr and atr_v is not None and not np.isnan(atr_v[i]):
            atr_pct_v = atr_v[i] / closes[i] if closes[i] > 0 else 0.0
            if atr_low_pct  > 0 and atr_pct_v < atr_low_pct:
                vol_ok = False
            if atr_high_pct > 0 and atr_pct_v > atr_high_pct:
                vol_ok = False

        if rsi_ok and trend_ok and ema_ok and vol_ok:
            in_position = True
            entry_price = closes[i]
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)

        equity_curve.append(equity)

    return trades, equity_curve


# ── Metrik hesaplama ──────────────────────────────────────────────────────────

def _metrics(trades: list, equity_curve: list) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "net_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "expectancy": 0.0, "max_dd_pct": 0.0}

    wins = [t for t in trades if t["outcome"] == "TP"]
    loss = [t for t in trades if t["outcome"] == "SL"]
    gp   = sum(t["pnl_usd"] for t in wins)
    gl   = abs(sum(t["pnl_usd"] for t in loss))
    net  = gp - gl
    n    = len(trades)

    max_dd = 0.0
    if equity_curve:
        arr  = np.array(equity_curve, dtype=float)
        peak = np.maximum.accumulate(arr)
        dd   = (arr - peak) / np.where(peak > 0, peak, 1) * 100
        max_dd = float(dd.min())

    return {
        "trades":        n,
        "win_rate":      round(len(wins) / n * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else float("inf"),
        "net_pnl":       round(net, 2),
        "avg_win":       round(gp / len(wins), 2) if wins else 0.0,
        "avg_loss":      round(gl / len(loss), 2) if loss else 0.0,
        "expectancy":    round(net / n, 2),
        "max_dd_pct":    round(max_dd, 2),
    }


def run_config(data: dict, params: dict) -> tuple[dict, dict]:
    all_trades:  list = []
    combined_eq: list = []
    sym_metrics: dict = {}

    for sym, df in data.items():
        trades, eq = _backtest_symbol(df, sym, params)
        sym_metrics[sym] = _metrics(trades, eq)
        all_trades.extend(trades)
        if eq:
            if not combined_eq:
                combined_eq = list(eq)
            else:
                min_len = min(len(combined_eq), len(eq))
                combined_eq = [combined_eq[j] + eq[j] - INITIAL_EQ
                               for j in range(min_len)]

    return _metrics(all_trades, combined_eq), sym_metrics


# ── Yazdırma yardımcıları ─────────────────────────────────────────────────────

_HDR = (
    f"  {'Konfig':<32} {'Trade':>5} {'WR%':>6} {'PF':>5} "
    f"{'NetPnL':>8} {'AvgW':>6} {'AvgL':>6} {'Expct':>6} {'MaxDD%':>7}"
)
_SEP = "  " + "─" * 84


def _row(label: str, m: dict) -> str:
    pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "  ∞"
    return (
        f"  {label:<32} {m['trades']:>5} {m['win_rate']:>5.1f}% {pf:>5} "
        f"{m['net_pnl']:>+8.2f} {m['avg_win']:>6.2f} {m['avg_loss']:>6.2f} "
        f"{m['expectancy']:>+6.2f} {m['max_dd_pct']:>6.2f}%"
    )


def _sym_breakdown(sym_m: dict, indent: str = "      ") -> None:
    print(f"{indent}{'Sembol':<14} {'Trade':>5} {'WR%':>6} {'NetPnL':>9}")
    print(f"{indent}" + "─" * 38)
    for sym, m in sym_m.items():
        print(f"{indent}{sym:<14} {m['trades']:>5} {m['win_rate']:>5.1f}% {m['net_pnl']:>+9.2f}")


def _phase_header(title: str) -> None:
    print()
    print("═" * 88)
    print(f"  {title}")
    print("═" * 88)
    print(_HDR)
    print(_SEP)


def _best_idx(results: list, key: str = "net_pnl") -> int:
    values = [r[0].get(key, float("-inf")) for r in results]
    return int(np.argmax(values))


# ── Fazlar ────────────────────────────────────────────────────────────────────

def phase1_rsi(data: dict) -> dict:
    _phase_header("FAZ 1 — RSI Eşiği  (TP=1.5% / SL=0.8% / Cooldown=120dk)")
    base = {"tp_pct": BASE_TP, "sl_pct": BASE_SL, "cooldown_minutes": BASE_COOLDOWN}
    configs = [
        ("RSI < 33 (mevcut)", {"rsi_oversold": 33.0}),
        ("RSI < 30",          {"rsi_oversold": 30.0}),
        ("RSI < 28",          {"rsi_oversold": 28.0}),
    ]
    results = []
    for label, ov in configs:
        cm, sm = run_config(data, {**base, **ov})
        results.append((cm, sm, label, {**base, **ov}))
        print(_row(label, cm))

    bi   = _best_idx(results)
    best = results[bi]
    print(_SEP)
    print(f"  ★  En iyi: {best[2]}  (NetPnL: ${best[0]['net_pnl']:+.2f})")
    print("\n  Sembol dağılımı (en iyi config):")
    _sym_breakdown(best[1])
    return best[3]


def phase2_tp_sl(data: dict, prev: dict) -> dict:
    rsi = prev["rsi_oversold"]
    _phase_header(f"FAZ 2 — TP/SL Kombinasyonları  (RSI<{rsi:.0f} / Cooldown=120dk)")
    base = {**prev, "cooldown_minutes": BASE_COOLDOWN}
    configs = [
        ("TP 1.5% / SL 0.8% (mevcut)", {"tp_pct": 0.015, "sl_pct": 0.008}),
        ("TP 1.8% / SL 0.8%",          {"tp_pct": 0.018, "sl_pct": 0.008}),
        ("TP 1.2% / SL 0.7%",          {"tp_pct": 0.012, "sl_pct": 0.007}),
        ("TP 1.5% / SL 1.0%",          {"tp_pct": 0.015, "sl_pct": 0.010}),
        ("TP 2.0% / SL 0.9%",          {"tp_pct": 0.020, "sl_pct": 0.009}),
    ]
    results = []
    for label, ov in configs:
        cm, sm = run_config(data, {**base, **ov})
        results.append((cm, sm, label, {**base, **ov}))
        print(_row(label, cm))

    bi   = _best_idx(results)
    best = results[bi]
    print(_SEP)
    print(f"  ★  En iyi: {best[2]}  (NetPnL: ${best[0]['net_pnl']:+.2f})")
    print("\n  Sembol dağılımı (en iyi config):")
    _sym_breakdown(best[1])
    return best[3]


def phase3_cooldown(data: dict, prev: dict) -> dict:
    rsi = prev["rsi_oversold"]
    tp  = prev["tp_pct"] * 100
    sl  = prev["sl_pct"] * 100
    _phase_header(f"FAZ 3 — Cooldown Süresi  (RSI<{rsi:.0f} / TP={tp:.1f}% / SL={sl:.1f}%)")
    configs = [
        ("Cooldown 60 dk",           {"cooldown_minutes": 60}),
        ("Cooldown 90 dk",           {"cooldown_minutes": 90}),
        ("Cooldown 120 dk (mevcut)", {"cooldown_minutes": 120}),
        ("Cooldown 180 dk",          {"cooldown_minutes": 180}),
        ("Cooldown 240 dk",          {"cooldown_minutes": 240}),
    ]
    results = []
    for label, ov in configs:
        cm, sm = run_config(data, {**prev, **ov})
        results.append((cm, sm, label, {**prev, **ov}))
        print(_row(label, cm))

    bi   = _best_idx(results)
    best = results[bi]
    print(_SEP)
    print(f"  ★  En iyi: {best[2]}  (NetPnL: ${best[0]['net_pnl']:+.2f})")
    print("\n  Sembol dağılımı (en iyi config):")
    _sym_breakdown(best[1])
    return best[3]


def phase4_volatility(data: dict, prev: dict) -> dict:
    rsi = prev["rsi_oversold"]
    _phase_header(f"FAZ 4 — Volatilite Filtresi  (RSI<{rsi:.0f} + optimum TP/SL/CD)")
    configs = [
        ("Filtre yok (baseline)",         {"atr_low_pct": 0.0,   "atr_high_pct": 0.0}),
        ("Min vol filtresi (ATR>0.3%)",   {"atr_low_pct": 0.003, "atr_high_pct": 0.0}),
        ("Max vol filtresi (ATR<1.5%)",   {"atr_low_pct": 0.0,   "atr_high_pct": 0.015}),
        ("Her iki filtre (0.3%–1.5%)",    {"atr_low_pct": 0.003, "atr_high_pct": 0.015}),
        ("Dar bant (0.4%–1.2%)",          {"atr_low_pct": 0.004, "atr_high_pct": 0.012}),
    ]
    results = []
    for label, ov in configs:
        cm, sm = run_config(data, {**prev, **ov})
        results.append((cm, sm, label, {**prev, **ov}))
        print(_row(label, cm))

    bi   = _best_idx(results)
    best = results[bi]
    print(_SEP)
    print(f"  ★  En iyi (NetPnL): {best[2]}  (NetPnL: ${best[0]['net_pnl']:+.2f})")
    print("\n  Sembol dağılımı (en iyi config):")
    _sym_breakdown(best[1])
    return best[3]


def final_summary(data: dict, opt: dict) -> None:
    baseline = {"rsi_oversold": BASE_RSI, "tp_pct": BASE_TP, "sl_pct": BASE_SL,
                "cooldown_minutes": BASE_COOLDOWN, "atr_low_pct": 0.0, "atr_high_pct": 0.0}
    b_cm, _  = run_config(data, baseline)
    o_cm, o_sm = run_config(data, opt)

    print()
    print("═" * 88)
    print("  SONUÇ — Baseline vs Optimum Konfigürasyon")
    print("═" * 88)
    print(_HDR)
    print(_SEP)
    print(_row("Baseline (mevcut)", b_cm))
    print(_row("Optimum  (bu çalışma)", o_cm))
    print(_SEP)

    d_pnl = o_cm["net_pnl"] - b_cm["net_pnl"]
    d_wr  = o_cm["win_rate"] - b_cm["win_rate"]
    d_pf  = (o_cm["profit_factor"] - b_cm["profit_factor"]
             if o_cm["profit_factor"] != float("inf") else float("inf"))

    print(f"\n  Δ Net PnL       : ${d_pnl:+.2f}")
    print(f"  Δ Win Rate      : {d_wr:+.1f}%")
    print(f"  Δ Profit Factor : {d_pf:+.2f}")

    atr_low  = opt.get("atr_low_pct",  0.0)
    atr_high = opt.get("atr_high_pct", 0.0)
    atr_str  = (f"ATR > {atr_low*100:.1f}% ve < {atr_high*100:.1f}%"
                if atr_low or atr_high else "devre dışı")

    print()
    print("  ┌─ Önerilen config ─────────────────────────────────────────")
    print(f"  │  RSI eşiği          : {opt.get('rsi_oversold', BASE_RSI):.0f}")
    print(f"  │  Take Profit        : {opt.get('tp_pct', BASE_TP)*100:.1f}%")
    print(f"  │  Stop Loss          : {opt.get('sl_pct', BASE_SL)*100:.1f}%")
    print(f"  │  Cooldown           : {opt.get('cooldown_minutes', BASE_COOLDOWN)} dk")
    print(f"  │  Volatilite filtresi: {atr_str}")
    print("  └───────────────────────────────────────────────────────────")

    print("\n  Sembol dağılımı (optimum config):")
    _sym_breakdown(o_sm)

    print()
    print("  .env / settings.py değerleri:")
    print(f"    RR_RSI_OVERSOLD={opt.get('rsi_oversold', BASE_RSI):.0f}")
    print(f"    RR_TP_PCT={opt.get('tp_pct', BASE_TP)}")
    print(f"    RR_SL_PCT={opt.get('sl_pct', BASE_SL)}")
    print(f"    RR_COOLDOWN_MIN={opt.get('cooldown_minutes', BASE_COOLDOWN)}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="RSI Reversion Optimizer")
    p.add_argument("--days",     type=int, default=90, help="Kaç günlük veri (varsayılan: 90)")
    p.add_argument("--phase",    type=int, default=0,  help="Sadece bu fazı çalıştır (1-4)")
    p.add_argument("--no-fetch", action="store_true",  help="Cache'li parquet dosyalarını kullan")
    args = p.parse_args()

    print()
    print("═" * 88)
    print("  RSI REVERSION OPTIMIZER")
    print(f"  Semboller : {', '.join(SYMBOLS)}")
    print(f"  Timeframe : {TIMEFRAME}  |  Veri: {args.days} gün")
    print(f"  Pozisyon  : ${POSITION_SIZE:,.0f} sabit (karşılaştırma standardı)")
    print("═" * 88)

    if args.no_fetch:
        print("\n  Cache'den yükleniyor (--no-fetch)...")
        data = load_cache()
    else:
        print("\n  Binance'dan 15m veri çekiliyor...")
        data = fetch_all(args.days)

    print()
    for sym, df in data.items():
        t0 = df["timestamp"].iloc[0].strftime("%Y-%m-%d")
        t1 = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
        print(f"  {sym}: {len(df)} bar  ({t0} → {t1})")

    run_all = args.phase == 0
    best1 = best2 = best3 = best4 = {
        "rsi_oversold": BASE_RSI, "tp_pct": BASE_TP,
        "sl_pct": BASE_SL, "cooldown_minutes": BASE_COOLDOWN,
    }

    if run_all or args.phase == 1:
        best1 = phase1_rsi(data)
    if run_all or args.phase == 2:
        best2 = phase2_tp_sl(data, best1 if run_all else best2)
    if run_all or args.phase == 3:
        best3 = phase3_cooldown(data, best2 if run_all else best3)
    if run_all or args.phase == 4:
        best4 = phase4_volatility(data, best3 if run_all else best4)

    if run_all:
        final_summary(data, best4)


if __name__ == "__main__":
    main()
