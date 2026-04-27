"""
Threshold Sensitivity Analysis — dry-run, no trades.

Reads logs/scans.jsonl and analyzes how restrictive current thresholds are.

Usage:
    python -m tools.analyze_thresholds
"""

import json
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).parent.parent / "logs"


def _count_above(values: list[float], thresholds: list[float]) -> dict[str, int]:
    return {f">= {t}": sum(1 for v in values if v >= t) for t in thresholds}


def _count_below(values: list[float], thresholds: list[float]) -> dict[str, int]:
    return {f"< {t}": sum(1 for v in values if v < t) for t in thresholds}


def _stats(values: list[float]) -> dict:
    if not values:
        return {"min": None, "max": None, "avg": None, "count": 0}
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
        "count": len(values),
    }


def analyze() -> dict:
    scans_path = LOGS_DIR / "scans.jsonl"
    if not scans_path.exists():
        print("logs/scans.jsonl not found.")
        return {}

    lines = scans_path.read_text().strip().splitlines()
    scans = [json.loads(l) for l in lines if l.strip()]
    scored = [s for s in scans if "score_decision" in s]

    if not scored:
        print("No scan entries with score_decision found.")
        return {}

    # ── Collect values ───────────────────────────────────────────────
    scores: list[float] = []
    rsis: list[float] = []
    vol_spikes: list[float] = []
    atr_pcts: list[float] = []
    symbol_data: dict[str, dict] = defaultdict(lambda: {
        "scores": [], "rsis": [], "vol_spikes": [], "trends": defaultdict(int),
        "reject_reasons": defaultdict(int), "score_reject_reasons": defaultdict(int),
    })

    for s in scored:
        sym = s.get("symbol", "unknown")
        sd = s.get("score_decision", {})
        feat = s.get("features", {})

        sc = sd.get("score", 0)
        scores.append(sc)
        symbol_data[sym]["scores"].append(sc)

        rsi = feat.get("rsi")
        if rsi is not None:
            rsis.append(rsi)
            symbol_data[sym]["rsis"].append(rsi)

        vs = feat.get("volume_spike_ratio")
        if vs is not None:
            vol_spikes.append(vs)
            symbol_data[sym]["vol_spikes"].append(vs)

        ap = feat.get("atr_pct")
        if ap is not None:
            atr_pcts.append(ap)

        trend = feat.get("trend", "neutral")
        symbol_data[sym]["trends"][trend] += 1

        reject = s.get("reject_reason", "")
        if reject:
            symbol_data[sym]["reject_reasons"][reject] += 1

        for sr in sd.get("reject_reasons", []):
            symbol_data[sym]["score_reject_reasons"][sr] += 1

    # ── Build report ─────────────────────────────────────────────────
    report: dict = {}

    # 1. Score distribution
    report["score_distribution"] = {
        **_stats(scores),
        "count_above_thresholds": _count_above(scores, [40, 50, 60, 70]),
    }

    # 2. RSI distribution
    report["rsi_distribution"] = {
        **_stats(rsis),
        "count_below_thresholds": _count_below(rsis, [45, 40, 35, 33, 30]),
    }

    # 3. Volume spike distribution
    report["volume_spike_distribution"] = {
        **_stats(vol_spikes),
        "count_above_thresholds": _count_above(vol_spikes, [1.0, 1.1, 1.2, 1.5]),
    }

    # 4. ATR pct distribution
    report["atr_pct_distribution"] = {
        **_stats(atr_pcts),
    }

    # 5. Per-symbol summary
    symbols_out: dict[str, dict] = {}
    for sym, data in sorted(symbol_data.items()):
        top_rejects = sorted(
            {**data["reject_reasons"], **data["score_reject_reasons"]}.items(),
            key=lambda x: -x[1],
        )[:5]
        symbols_out[sym] = {
            "avg_score": round(sum(data["scores"]) / len(data["scores"]), 1),
            "max_score": max(data["scores"]) if data["scores"] else 0,
            "min_rsi": round(min(data["rsis"]), 1) if data["rsis"] else None,
            "avg_rsi": round(sum(data["rsis"]) / len(data["rsis"]), 1) if data["rsis"] else None,
            "max_volume_spike": round(max(data["vol_spikes"]), 3) if data["vol_spikes"] else None,
            "avg_volume_spike": round(sum(data["vol_spikes"]) / len(data["vol_spikes"]), 3)
                                if data["vol_spikes"] else None,
            "trend_distribution": dict(data["trends"]),
            "top_reject_reasons": [{"reason": r, "count": c} for r, c in top_rejects],
            "scan_count": len(data["scores"]),
        }
    report["per_symbol"] = symbols_out
    report["total_scored_scans"] = len(scored)

    return report


def print_summary(report: dict) -> None:
    if not report:
        return

    n = report["total_scored_scans"]
    sd = report["score_distribution"]
    rd = report["rsi_distribution"]
    vd = report["volume_spike_distribution"]

    print("\n" + "=" * 66)
    print("  THRESHOLD SENSITIVITY ANALYSIS")
    print("=" * 66)
    print(f"  Scored scan entries: {n}")

    # Score
    print(f"\n  {'SCORE DISTRIBUTION':^62}")
    print(f"  {'─' * 62}")
    print(f"  Min: {sd['min']}  Max: {sd['max']}  Avg: {sd['avg']}  (out of {sd['count']})")
    for k, v in sd.get("count_above_thresholds", {}).items():
        pct = v / n * 100 if n else 0
        bar = "█" * int(pct / 2)
        print(f"  {k:>8}: {v:>4} ({pct:5.1f}%) {bar}")

    # RSI
    print(f"\n  {'RSI DISTRIBUTION':^62}")
    print(f"  {'─' * 62}")
    print(f"  Min: {rd['min']}  Max: {rd['max']}  Avg: {rd['avg']}")
    for k, v in rd.get("count_below_thresholds", {}).items():
        pct = v / n * 100 if n else 0
        bar = "█" * int(pct / 2)
        print(f"  {k:>8}: {v:>4} ({pct:5.1f}%) {bar}")

    # Volume
    print(f"\n  {'VOLUME SPIKE RATIO':^62}")
    print(f"  {'─' * 62}")
    print(f"  Min: {vd['min']}  Max: {vd['max']}  Avg: {vd['avg']}")
    for k, v in vd.get("count_above_thresholds", {}).items():
        pct = v / n * 100 if n else 0
        bar = "█" * int(pct / 2)
        print(f"  {k:>8}: {v:>4} ({pct:5.1f}%) {bar}")

    # Per-symbol
    print(f"\n  {'PER-SYMBOL SUMMARY':^62}")
    print(f"  {'─' * 62}")
    print(f"  {'Symbol':<12} {'AvgSc':>6} {'MaxSc':>6} {'AvgRSI':>7} {'MinRSI':>7} {'MaxVol':>7} {'Trend':>10}")
    print(f"  {'─' * 62}")
    for sym, d in report.get("per_symbol", {}).items():
        print(f"  {sym:<12} {d['avg_score']:>6} {d['max_score']:>6} "
              f"{d.get('avg_rsi', '—'):>7} {d.get('min_rsi', '—'):>7} "
              f"{d.get('max_volume_spike', '—'):>7} "
              f"{max(d.get('trend_distribution', {}), key=d.get('trend_distribution', {}).get, default='?'):>10}")

    # Diagnosis
    print(f"\n  {'DIAGNOSIS':^62}")
    print(f"  {'─' * 62}")
    buy_count = sum(d.get("count_above_thresholds", {}).get(">= 60", 0)
                    for d in [sd] if "count_above_thresholds" in sd)
    rsi_below_33 = rd.get("count_below_thresholds", {}).get("< 33", 0)
    vol_above_12 = vd.get("count_above_thresholds", {}).get(">= 1.2", 0)

    if buy_count == 0:
        print(f"  ❌ Score never reaches 60. Highest: {sd['max']}")
    else:
        print(f"  ✓ Score >= 60 in {buy_count}/{n} scans")

    if rsi_below_33 == 0:
        print(f"  ❌ RSI never drops below 33. Lowest: {rd['min']}")
    else:
        print(f"  ✓ RSI < 33 in {rsi_below_33}/{n} scans")

    if vol_above_12 == 0:
        print(f"  ❌ Volume spike never reaches 1.2x. Highest: {vd['max']}")
    else:
        print(f"  ✓ Volume >= 1.2x in {vol_above_12}/{n} scans")

    gap_to_buy = 60 - sd["max"]
    print(f"\n  Score gap to trade: {gap_to_buy} points (max={sd['max']}, need=60)")
    print(f"  RSI gap to oversold: {round(rd['min'] - 33, 1)} points (min={rd['min']}, need<33)")

    print("=" * 66 + "\n")


def main() -> None:
    report = analyze()
    if report:
        print_summary(report)
        out_path = LOGS_DIR / "threshold_analysis.json"
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"  Saved → {out_path}")


if __name__ == "__main__":
    main()
