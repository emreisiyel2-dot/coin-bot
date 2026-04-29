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

REGIME_ALLOCATION_MAP: dict[str, dict[str, float]] = {
    "bearish": {"short_momentum_v1": 1.0},
    "bullish": {"momentum_pullback_v1": 0.5, "trend_pullback_v1": 0.5},
    "neutral": {"rsi_reversion": 1.0},
}


def select_strategies(market_regime: str) -> list[str]:
    """Return list of strategy names to run for the given regime."""
    strategies = REGIME_STRATEGY_MAP.get(market_regime, REGIME_STRATEGY_MAP["neutral"])
    logger.info("STRATEGY ROUTER | regime=%s | active=%s", market_regime, strategies)
    return list(strategies)


def get_strategy_allocations(
    active_strategies: list[str],
    market_regime: str,
    strategy_mode: str,
) -> dict[str, float]:
    """Return per-strategy capital allocation (fraction of equity).

    Manual mode: single strategy gets 1.0.
    Auto mode: split based on REGIME_ALLOCATION_MAP.
    """
    if strategy_mode != "auto":
        return {strategy_mode: 1.0}

    allocs = REGIME_ALLOCATION_MAP.get(market_regime, REGIME_ALLOCATION_MAP["neutral"])
    return {s: allocs.get(s, 0.0) for s in active_strategies}
