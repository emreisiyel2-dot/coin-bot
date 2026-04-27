"""
Alerts — modular notification layer for trade events.

Writes alerts to logs/alerts.jsonl and prints compact terminal messages.
Telegram is a placeholder (disabled by default).

Usage:
    from core.alerts import send_alert

    send_alert("TRADE_OPENED", {
        "symbol": "SOL/USDT",
        "strategy": "momentum_pullback_v1",
        "price": 86.27,
        "size_usdt": 2000,
    })
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ALERTS_ENABLED, TELEGRAM_ALERTS_ENABLED

logger = logging.getLogger(__name__)

_ALERTS_DIR = Path(__file__).parent.parent / "logs"
_ALERTS_FILE = _ALERTS_DIR / "alerts.jsonl"

VALID_EVENT_TYPES = {
    "TRADE_OPENED",
    "TRADE_CLOSED",
    "PARTIAL_CLOSE",
    "STOP_LOSS",
    "TAKE_PROFIT",
    "TRAILING_STOP",
    "MOMENTUM_DECAY_EXIT",
    "EARLY_MOMENTUM_FAILURE",
    "SOFT_PROFIT_PROTECT",
    "SCALE_IN",
    "TRADE_BLOCKED_COOLDOWN",
    "SHORT_OPENED",
    "SHORT_CLOSED",
    "SHORT_STOP_LOSS",
    "SHORT_TAKE_PROFIT",
    "SHORT_MOMENTUM_DECAY_EXIT",
    "SHORT_SOFT_PROFIT_PROTECT",
    "SHORT_TRAILING_STOP",
    "SHORT_BLOCKED_COOLDOWN",
    "ERROR",
    "TEST",
}


def _build_message(event_type: str, payload: dict) -> str:
    symbol = payload.get("symbol", "?")
    price = payload.get("price")
    strategy = payload.get("strategy", "")
    size_usdt = payload.get("size_usdt")
    pnl = payload.get("pnl")
    pnl_pct = payload.get("pnl_pct")
    reason = payload.get("reason")

    parts = [f"{event_type}", f"{symbol}"]

    if price is not None:
        parts.append(f"@ {price:.4f}")
    if strategy:
        parts.append(f"| {strategy}")
    if size_usdt is not None:
        parts.append(f"| size {size_usdt:.2f} USDT")
    if pnl is not None:
        parts.append(f"| pnl {pnl:+.2f}")
    if pnl_pct is not None:
        parts.append(f"({pnl_pct:+.2f}%)")
    if reason:
        parts.append(f"| {reason}")

    return " ".join(parts)


def send_alert(event_type: str, payload: dict) -> None:
    """Send an alert. Writes to alerts.jsonl and prints to terminal.

    Args:
        event_type: one of VALID_EVENT_TYPES
        payload: dict with keys like symbol, strategy, action, price,
                 size_usdt, pnl, pnl_pct, reason, message
    """
    if not ALERTS_ENABLED:
        return

    if not isinstance(payload, dict):
        logger.warning("ALERT | payload must be a dict, got: %s", type(payload).__name__)
        return

    if event_type not in VALID_EVENT_TYPES:
        logger.warning("ALERT | unknown event_type: %s", event_type)
        return

    ts = datetime.now(timezone.utc).isoformat()
    message = payload.get("message") or _build_message(event_type, payload)

    record = {
        "ts": ts,
        "event_type": event_type,
        **payload,
        "message": message,
    }

    try:
        _ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_ALERTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass

    # Compact terminal output
    symbol = payload.get("symbol", "")
    strategy = payload.get("strategy", "")
    price = payload.get("price", "")
    print(f"[ALERT] {event_type} | {symbol} | {strategy} | {price}")

    # Telegram placeholder
    send_telegram_alert(message)


def send_telegram_alert(message: str) -> None:
    """Placeholder for Telegram notifications. Disabled by default."""
    if not TELEGRAM_ALERTS_ENABLED:
        return
    # Future: send message via Telegram Bot API
    # Requires: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in env
    logger.debug("TELEGRAM | would send: %s", message)
