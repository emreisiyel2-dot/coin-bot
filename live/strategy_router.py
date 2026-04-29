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


MIN_WEIGHT = 0.2
MAX_WEIGHT = 0.8
BASE_BLEND = 0.7
SUGGEST_BLEND = 0.3


def blend_allocations(
    base_allocations: dict[str, float],
    performance: dict[str, dict],
) -> dict[str, float]:
    """Blend base (regime) allocations with performance-based suggestions.

    70% base + 30% suggested. Skips strategies with insufficient_data.
    Normalizes to sum=1.0, clamps to [0.2, 0.8].
    Single-strategy: returns base unchanged.
    """
    if len(base_allocations) <= 1:
        return dict(base_allocations)

    blended: dict[str, float] = {}
    for strat, base_w in base_allocations.items():
        perf = performance.get(strat, {})
        if perf.get("status") != "ok":
            blended[strat] = base_w
        else:
            suggested = perf.get("suggested_weight", base_w)
            blended[strat] = base_w * BASE_BLEND + suggested * SUGGEST_BLEND
            blended[strat] = max(MIN_WEIGHT, min(MAX_WEIGHT, blended[strat]))

    # Normalize to sum=1.0
    total = sum(blended.values())
    if total > 0:
        blended = {s: w / total for s, w in blended.items()}

    logger.info(
        "STRATEGY ADAPTIVE ALLOCATION | base=%s | suggested=%s | final=%s",
        {s: round(w, 2) for s, w in base_allocations.items()},
        {s: round(performance.get(s, {}).get("suggested_weight", 0), 2)
         for s in base_allocations if performance.get(s, {}).get("status") == "ok"},
        {s: round(w, 2) for s, w in blended.items()},
    )
    return {s: round(w, 4) for s, w in blended.items()}
