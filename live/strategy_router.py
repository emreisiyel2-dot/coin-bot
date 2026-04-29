"""
Strategy Router — selects active strategies based on market regime.

Mapping:
  bearish → short_momentum_v1
  bullish → momentum_pullback_v1, trend_pullback_v1
  neutral → rsi_reversion
"""

import logging

logger = logging.getLogger(__name__)

REGIME_STRATEGY_MAP: dict[str, list[str]] = {
    "bearish": ["short_momentum_v1"],
    "bullish": ["momentum_pullback_v1", "trend_pullback_v1"],
    "neutral": ["rsi_reversion"],
}


def select_strategies(market_regime: str) -> list[str]:
    """Return list of strategy names to run for the given regime."""
    strategies = REGIME_STRATEGY_MAP.get(market_regime, REGIME_STRATEGY_MAP["neutral"])
    logger.info("STRATEGY ROUTER | regime=%s | active=%s", market_regime, strategies)
    return list(strategies)
