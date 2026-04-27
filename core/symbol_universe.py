"""
Symbol universe — configurable core/expanded symbol lists with validation.

Usage:
    from core.symbol_universe import get_symbols, validate_symbol, filter_valid_symbols

    symbols = get_symbols("expanded")
    valid   = filter_valid_symbols(symbols)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

CORE_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "NEAR/USDT",
]

EXPANDED_SYMBOLS = [
    "AVAX/USDT",
    "LINK/USDT",
    "ARB/USDT",
    "OP/USDT",
    "MATIC/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
]

DYNAMIC_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT", "AVAX/USDT",
    "LINK/USDT", "ARB/USDT", "OP/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LTC/USDT", "ATOM/USDT",
    "APT/USDT", "SUI/USDT", "INJ/USDT", "SEI/USDT", "TIA/USDT",
    "FET/USDT", "RENDER/USDT", "WIF/USDT", "PEPE/USDT", "BONK/USDT",
    "ORDI/USDT", "STX/USDT", "FIL/USDT", "AAVE/USDT", "UNI/USDT",
    "RUNE/USDT", "GALA/USDT", "JUP/USDT", "PYTH/USDT", "ENA/USDT",
    "WLD/USDT", "TAO/USDT", "PENDLE/USDT", "ONDO/USDT", "AR/USDT",
]


def get_symbols(mode: str = "core") -> list[str]:
    """Return symbol list based on mode.

    mode="core"     → CORE_SYMBOLS only
    mode="expanded" → CORE_SYMBOLS + EXPANDED_SYMBOLS
    mode="dynamic"  → DYNAMIC_SYMBOLS (up to 40)
    """
    if mode == "dynamic":
        return list(DYNAMIC_SYMBOLS)
    if mode == "expanded":
        return list(CORE_SYMBOLS) + list(EXPANDED_SYMBOLS)
    if mode == "core":
        return list(CORE_SYMBOLS)
    raise ValueError(
        f"Unknown symbol mode: {mode!r}. Allowed: 'core', 'expanded', 'dynamic'"
    )


def validate_symbol(symbol: str) -> tuple[bool, str]:
    """Validate a single symbol. Returns (is_valid, reason).

    Checks:
      - non-empty string
      - ends with /USDT
      - no duplicate slashes or spaces
    """
    if not symbol or not isinstance(symbol, str):
        return False, "empty or non-string"

    symbol = symbol.strip()
    if not symbol.endswith("/USDT"):
        return False, f"does not end with /USDT: {symbol!r}"

    if " " in symbol:
        return False, f"contains spaces: {symbol!r}"

    if symbol.count("/") != 1:
        return False, f"invalid slash count: {symbol!r}"

    return True, "ok"


def _verify_exchange_tradable(symbol: str, exchange=None) -> bool:
    """Optionally verify symbol exists on exchange. Never raises."""
    if exchange is None:
        return True
    try:
        markets = getattr(exchange, "markets", None)
        if markets is None:
            return True
        return symbol in markets
    except Exception:
        return True


def filter_valid_symbols(
    symbols: list[str],
    exchange=None,
) -> tuple[list[str], list[str], list[str]]:
    """Filter symbols through validation.

    Returns:
        (valid, rejected, warnings)
        - valid:     symbols that passed all checks
        - rejected:  symbols that failed basic format validation
        - warnings:  symbols that passed format but could not be confirmed on exchange
    """
    seen: set[str] = set()
    valid: list[str] = []
    rejected: list[str] = []
    warnings: list[str] = []

    for symbol in symbols:
        is_valid, reason = validate_symbol(symbol)
        if not is_valid:
            rejected.append(f"{symbol} ({reason})")
            logger.warning("SYMBOL REJECTED | %s | %s", symbol, reason)
            continue

        normalized = symbol.strip().upper()
        if normalized in seen:
            rejected.append(f"{symbol} (duplicate)")
            logger.warning("SYMBOL REJECTED | %s | duplicate", symbol)
            continue
        seen.add(normalized)

        if exchange is not None:
            if not _verify_exchange_tradable(symbol, exchange):
                warnings.append(symbol)
                logger.warning(
                    "SYMBOL WARNING | %s | not confirmed on exchange (kept anyway)", symbol
                )
                valid.append(symbol)
                continue

        valid.append(symbol)

    logger.info(
        "SYMBOL VALIDATION | requested=%d | valid=%d | rejected=%d | warnings=%d",
        len(symbols), len(valid), len(rejected), len(warnings),
    )
    return valid, rejected, warnings
