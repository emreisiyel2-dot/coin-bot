"""
Market Regime — determines global market mode using BTC and ETH features.

Output:
  {"regime": "bullish|bearish|neutral", "confidence": 0-100, "reasons": [...]}

Rules:
  bullish  — BTC and ETH close > EMA200 AND EMA50 > EMA200
  bearish  — BTC and ETH close < EMA200
  neutral  — everything else
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

REGIME_SYMBOLS = ["BTC/USDT", "ETH/USDT"]

# Minimum liquidity to consider regime valid
MIN_VOLUME_SMA = 500_000.0


def _classify_symbol(features: dict[str, Any] | None) -> dict[str, Any]:
    """Classify a single symbol's trend state."""
    if not features:
        return {"state": "unknown", "reasons": ["no_features"]}

    close = features.get("close")
    ema_50 = features.get("ema_50")
    ema_200 = features.get("ema_200")
    trend = features.get("trend")
    vol_sma = features.get("volume_sma")

    reasons = []

    if close is None or ema_200 is None:
        return {"state": "unknown", "reasons": ["missing_ema200"]}

    above_ema200 = close > ema_200
    ema50_above_ema200 = ema_50 is not None and ema_50 > ema_200

    reasons.append(f"close{'>' if above_ema200 else '<='}EMA200")
    if ema_50 is not None:
        reasons.append(f"EMA50{'>' if ema50_above_ema200 else '<='}EMA200")
    if vol_sma is not None:
        reasons.append(f"vol_sma={vol_sma:.0f}")

    if above_ema200 and ema50_above_ema200:
        return {"state": "bullish", "reasons": reasons}
    if not above_ema200:
        return {"state": "bearish", "reasons": reasons}
    return {"state": "neutral", "reasons": reasons}


def determine_market_regime(
    features_map: dict[str, dict],
) -> dict[str, Any]:
    """Determine market regime from BTC and ETH features.

    Args:
        features_map: dict of symbol -> feature snapshot

    Returns:
        {"regime": str, "confidence": int, "reasons": list,
         "btc": dict, "eth": dict}
    """
    btc = _classify_symbol(features_map.get("BTC/USDT"))
    eth = _classify_symbol(features_map.get("ETH/USDT"))

    btc_state = btc["state"]
    eth_state = eth["state"]

    # Count agreement
    states = [s for s in [btc_state, eth_state] if s != "unknown"]

    if not states:
        regime = "neutral"
        confidence = 0
    elif all(s == "bullish" for s in states):
        regime = "bullish"
        confidence = 70 + 15 * len(states)
    elif all(s == "bearish" for s in states):
        regime = "bearish"
        confidence = 70 + 15 * len(states)
    elif "bearish" in states:
        # Mixed but any bearish → lean bearish
        regime = "bearish"
        confidence = 55
    elif "bullish" in states:
        regime = "neutral"
        confidence = 50
    else:
        regime = "neutral"
        confidence = 30

    logger.info(
        "MARKET REGIME | %s (conf=%d) | BTC=%s | ETH=%s",
        regime, confidence, btc_state, eth_state,
    )

    return {
        "regime": regime,
        "confidence": confidence,
        "reasons": btc.get("reasons", []) + eth.get("reasons", []),
        "btc": btc,
        "eth": eth,
    }


def apply_regime_filter(
    regime_data: dict[str, Any],
    symbol: str,
    features: dict[str, Any] | None,
    score: int,
) -> tuple[bool, str]:
    """Check if symbol passes regime-specific filters.

    Returns (allowed, reason).
    """
    regime = regime_data.get("regime", "neutral")

    if regime == "bullish":
        return True, ""

    if regime == "neutral":
        return True, ""

    if regime == "bearish":
        # Bearish: require A+ setups only
        if not features:
            return False, "regime_bearish_no_features"

        close = features.get("close")
        ema_50 = features.get("ema_50")
        ema_200 = features.get("ema_200")
        vol_spike = features.get("volume_spike_ratio")

        if ema_200 is not None and (close is None or close <= ema_200):
            return False, "regime_bearish_below_ema200"

        if ema_50 is not None and (close is None or close <= ema_50):
            return False, "regime_bearish_below_ema50"

        if score < 70:
            return False, f"regime_bearish_score_too_low ({score} < 70)"

        if vol_spike is not None and vol_spike < 1.2:
            return False, f"regime_bearish_volume_too_weak ({vol_spike:.2f} < 1.2)"

        return True, ""

    return True, ""
