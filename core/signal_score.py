"""
Signal Scoring Layer — observation-only.

Accepts a feature snapshot and returns a scored decision.
This does NOT execute trades. It is for logging and analysis.

The actual trade decision still comes from strategy.generate_signal().
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Configurable thresholds ──────────────────────────────────────────────────
RSI_OVERSOLD_THRESHOLD   = 35.0
RSI_DEEP_OVERSOLD        = 30.0
VOLUME_SPIKE_MIN         = 1.2
ATR_PCT_MIN              = 0.10
DISTANCE_EMA50_MAX_PCT   = 5.0   # max % extension from EMA50
CANDLE_BODY_MAX_PCT      = 90.0  # reject if last candle is near-full range

SCORE_RSI_OVERSOLD       = 25
SCORE_RSI_DEEP_EXTRA     = 15
SCORE_TREND_BULLISH      = 20
SCORE_VOLUME_SPIKE       = 15
SCORE_ATR_ADEQUATE       = 10
SCORE_DISTANCE_OK        = 10
SCORE_CANDLE_OK          = 5

BUY_SCORE_THRESHOLD      = 60
HIGH_CONFIDENCE_MIN      = 75
MEDIUM_CONFIDENCE_MIN    = 50


def score_signal(features: dict[str, Any]) -> dict[str, Any]:
    """
    Score a feature snapshot.

    Returns:
        {
            "action": "BUY" | "SKIP",
            "score": 0-100,
            "confidence": "low" | "medium" | "high",
            "reasons": [...],
            "reject_reasons": [...]
        }
    """
    result: dict[str, Any] = {
        "action":          "SKIP",
        "score":           0,
        "confidence":      "low",
        "reasons":         [],
        "reject_reasons":  [],
    }

    # Guard: insufficient feature data
    if features.get("close") is None:
        result["reject_reasons"].append("insufficient feature data")
        return result

    score = 0
    reasons: list[str] = []
    reject_reasons: list[str] = []

    rsi = features.get("rsi")
    trend = features.get("trend", "neutral")
    vol_spike = features.get("volume_spike_ratio")
    atr_pct = features.get("atr_pct")
    dist_ema50 = features.get("distance_ema50_pct")
    candle_body = features.get("candle_body_pct")

    # ── RSI scoring ──────────────────────────────────────────────────────
    if rsi is not None:
        if rsi < RSI_OVERSOLD_THRESHOLD:
            score += SCORE_RSI_OVERSOLD
            reasons.append(f"RSI below {RSI_OVERSOLD_THRESHOLD:.0f} ({rsi:.1f})")
            if rsi < RSI_DEEP_OVERSOLD:
                score += SCORE_RSI_DEEP_EXTRA
                reasons.append(f"RSI deep oversold ({rsi:.1f})")

    # ── Trend scoring ────────────────────────────────────────────────────
    if trend == "bullish":
        score += SCORE_TREND_BULLISH
        reasons.append("close above EMA200")
    elif trend == "bearish":
        reject_reasons.append("trend bearish (close below EMA200)")

    # ── Volume scoring ───────────────────────────────────────────────────
    if vol_spike is not None and vol_spike >= VOLUME_SPIKE_MIN:
        score += SCORE_VOLUME_SPIKE
        reasons.append(f"volume spike {vol_spike:.2f}x")
    elif vol_spike is not None:
        reject_reasons.append("volume too weak")

    # ── ATR scoring ──────────────────────────────────────────────────────
    if atr_pct is not None and atr_pct >= ATR_PCT_MIN:
        score += SCORE_ATR_ADEQUATE
        reasons.append(f"ATR adequate ({atr_pct:.3f}%)")
    elif atr_pct is not None:
        reject_reasons.append("ATR too low")

    # ── Distance from EMA50 ──────────────────────────────────────────────
    if dist_ema50 is not None and abs(dist_ema50) <= DISTANCE_EMA50_MAX_PCT:
        score += SCORE_DISTANCE_OK
        reasons.append("price near EMA50")
    elif dist_ema50 is not None:
        reject_reasons.append("price too extended from EMA50")

    # ── Candle body filter ───────────────────────────────────────────────
    if candle_body is not None and candle_body <= CANDLE_BODY_MAX_PCT:
        score += SCORE_CANDLE_OK
        reasons.append("candle body ok")

    # ── Final decision ───────────────────────────────────────────────────
    if score < BUY_SCORE_THRESHOLD:
        reject_reasons.append("score below threshold")

    action = "BUY" if score >= BUY_SCORE_THRESHOLD and trend != "bearish" else "SKIP"

    if action == "SKIP" and trend == "neutral" and "trend bearish" not in str(reject_reasons):
        reject_reasons.append("trend filter invalid (neutral)")

    # Confidence
    if score >= HIGH_CONFIDENCE_MIN:
        confidence = "high"
    elif score >= MEDIUM_CONFIDENCE_MIN:
        confidence = "medium"
    else:
        confidence = "low"

    result["action"] = action
    result["score"] = score
    result["confidence"] = confidence
    result["reasons"] = reasons
    result["reject_reasons"] = reject_reasons

    return result
