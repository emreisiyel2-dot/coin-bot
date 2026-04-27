"""
Watch Alerts — tails logs/alerts.jsonl and prints new alerts in readable form.

Usage:
    python -m tools.watch_alerts
"""

import json
import sys
import time
from pathlib import Path

ALERTS_FILE = Path(__file__).parent.parent / "logs" / "alerts.jsonl"

ICONS = {
    "TRADE_OPENED":        "🟢",
    "TRADE_CLOSED":        "🔴",
    "PARTIAL_CLOSE":       "🟡",
    "STOP_LOSS":           "🛑",
    "TAKE_PROFIT":         "🎯",
    "TRAILING_STOP":       "📐",
    "MOMENTUM_DECAY_EXIT": "💨",
    "EARLY_MOMENTUM_FAILURE": "⚡",
    "SOFT_PROFIT_PROTECT": "🛡️",
    "SCALE_IN":            "⬆️",
    "TRADE_BLOCKED_COOLDOWN": "🚫",
    "ERROR":               "❌",
    "TEST":                "📡",
}


def format_alert(record: dict) -> str:
    icon = ICONS.get(record.get("event_type", ""), "📡")
    ts = record.get("ts", "")[11:19]  # HH:MM:SS
    event = record.get("event_type", "?")
    symbol = record.get("symbol", "?")
    msg = record.get("message", "")

    parts = [f"{icon} [{ts}] {event:15s} {symbol:<14s}"]
    pnl = record.get("pnl")
    pnl_pct = record.get("pnl_pct")
    price = record.get("price")
    reason = record.get("reason")
    size = record.get("size_usdt")

    if price is not None:
        parts.append(f"@ {price:.4f}")
    if size is not None:
        parts.append(f"size={size:.2f}")
    if pnl is not None:
        parts.append(f"pnl={pnl:+.2f}")
    if pnl_pct is not None:
        parts.append(f"({pnl_pct:+.2f}%)")
    if reason:
        parts.append(f"[{reason}]")

    return " ".join(parts)


def watch(poll_interval: float = 2.0) -> None:
    print(f"Watching {ALERTS_FILE} (ctrl+C to stop)...\n")

    # Print existing alerts first
    if ALERTS_FILE.exists():
        lines = ALERTS_FILE.read_text().strip().splitlines()
        if lines:
            print(f"--- Last {min(len(lines), 20)} existing alerts ---")
            for line in lines[-20:]:
                try:
                    record = json.loads(line)
                    print(format_alert(record))
                except (json.JSONDecodeError, KeyError):
                    print(f"  (malformed) {line[:100]}")
            print("--- Watching for new alerts ---\n")

    # Tail for new alerts
    seen = 0
    if ALERTS_FILE.exists():
        seen = len(ALERTS_FILE.read_text().strip().splitlines())

    while True:
        try:
            time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break

        if not ALERTS_FILE.exists():
            continue

        lines = ALERTS_FILE.read_text().strip().splitlines()
        new_count = len(lines)

        if new_count > seen:
            for line in lines[seen:]:
                try:
                    record = json.loads(line)
                    print(format_alert(record))
                except (json.JSONDecodeError, KeyError):
                    print(f"  (malformed) {line[:100]}")
            seen = new_count


if __name__ == "__main__":
    watch()
