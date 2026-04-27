"""
Symbol Discovery — placeholder for future internet/news-based symbol discovery.

THIS MODULE IS DISABLED BY DEFAULT.
Set SYMBOL_DISCOVERY_ENABLED=true in env to activate (not recommended yet).

Intended future sources:
  - CoinGecko trending coins
  - CoinGecko market data / top gainers
  - CryptoPanic news sentiment

IMPORTANT:
  - Discovered symbols are for WATCHLIST DISCOVERY ONLY.
  - They must NOT directly trigger trades.
  - All discovered symbols must pass validation (core/symbol_universe.py)
    AND existing strategy + risk checks before any action.
  - No real money is connected. Paper trading only.

Usage (future):
    from core.symbol_discovery import discover_symbols
    candidates = discover_symbols()  # returns list of symbol strings
"""

import logging

from config.settings import SYMBOL_DISCOVERY_ENABLED

logger = logging.getLogger(__name__)

# Future source stubs — each returns list[str] of symbols like "PEPE/USDT"

def _coingecko_trending() -> list[str]:
    """Fetch trending coins from CoinGecko. Not implemented."""
    return []


def _coingecko_top_gainers() -> list[str]:
    """Fetch top gainers from CoinGecko market data. Not implemented."""
    return []


def _cryptopanic_sentiment() -> list[str]:
    """Fetch coins with positive news sentiment from CryptoPanic. Not implemented."""
    return []


def discover_symbols() -> list[str]:
    """Return discovered candidate symbols from enabled sources.

    Currently returns empty list — all sources are stubs.
    When implemented, each source should:
      1. Fetch data with error handling (network failures must not crash the bot)
      2. Convert to /USDT format
      3. Return raw list for validation by symbol_universe.filter_valid_symbols()
    """
    if not SYMBOL_DISCOVERY_ENABLED:
        logger.debug("SYMBOL_DISCOVERY disabled — skipping")
        return []

    candidates: list[str] = []
    candidates.extend(_coingecko_trending())
    candidates.extend(_coingecko_top_gainers())
    candidates.extend(_cryptopanic_sentiment())

    if candidates:
        logger.info("SYMBOL_DISCOVERY | candidates=%d | %s", len(candidates), candidates[:10])
    return candidates
