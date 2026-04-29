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

# ── Short-aware scoring thresholds ─────────────────────────────────────────
SHORT_SCORE_MIN_TO_TRADE = 60
SHORT_VOLUME_SPIKE_MIN   = 1.0
SHORT_ATR_PCT_MIN        = 0.18
SHORT_RSI_MAX            = 45.0


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


def score_signal_short(
    features: dict[str, Any],
    market_regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Short-aware scoring for short_momentum_v1.

    Returns:
        {
            "score_model": "short_aware",
            "action": "SHORT" | "SKIP",
            "score": 0-100,
            "confidence": "low" | "medium" | "high",
            "reasons": [...],
            "reject_reasons": [...]
        }
    """
    result: dict[str, Any] = {
        "score_model": "short_aware",
        "action":       "SKIP",
        "score":        0,
        "confidence":   "low",
        "reasons":      [],
        "reject_reasons": [],
    }

    if features.get("close") is None:
        result["reject_reasons"].append("insufficient feature data")
        return result

    score = 0
    reasons: list[str] = []
    reject_reasons: list[str] = []

    trend = features.get("trend", "neutral")
    rsi = features.get("rsi")
    vol_spike = features.get("volume_spike_ratio")
    atr_pct = features.get("atr_pct")
    candle_body = features.get("candle_body_pct")
    close = features.get("close")
    ema200 = features.get("ema_200")
    ema50 = features.get("ema_50")

    # ── Market regime ──────────────────────────────────────────────────────
    regime = (market_regime or {}).get("regime", "neutral")
    if regime == "bearish":
        score += 20
        reasons.append("market regime bearish")
    else:
        reject_reasons.append(f"market regime not bearish ({regime})")

    # ── Trend ──────────────────────────────────────────────────────────────
    if trend == "bearish":
        score += 20
        reasons.append("trend bearish (close below EMA200)")
    else:
        reject_reasons.append(f"trend not bearish ({trend})")

    # ── Price below EMA200 ─────────────────────────────────────────────────
    if close is not None and ema200 is not None and close < ema200:
        score += 15
        reasons.append("close below EMA200")
    elif close is not None and ema200 is not None:
        reject_reasons.append("close above EMA200")

    # ── Price below EMA50 ──────────────────────────────────────────────────
    if close is not None and ema50 is not None and close < ema50:
        score += 15
        reasons.append("close below EMA50")
    elif close is not None and ema50 is not None:
        reject_reasons.append("close above EMA50")

    # ── Volume spike ───────────────────────────────────────────────────────
    if vol_spike is not None and vol_spike >= SHORT_VOLUME_SPIKE_MIN:
        score += 15
        reasons.append(f"volume spike {vol_spike:.2f}x >= {SHORT_VOLUME_SPIKE_MIN}")
    elif vol_spike is not None:
        reject_reasons.append(f"volume too low ({vol_spike:.2f}x < {SHORT_VOLUME_SPIKE_MIN})")

    # ── ATR ────────────────────────────────────────────────────────────────
    if atr_pct is not None and atr_pct >= SHORT_ATR_PCT_MIN:
        score += 10
        reasons.append(f"ATR {atr_pct:.3f}% >= {SHORT_ATR_PCT_MIN}%")
    elif atr_pct is not None:
        reject_reasons.append(f"ATR too low ({atr_pct:.3f}% < {SHORT_ATR_PCT_MIN}%)")

    # ── RSI in short-favorable zone ────────────────────────────────────────
    if rsi is not None and 30.0 <= rsi <= SHORT_RSI_MAX:
        score += 10
        reasons.append(f"RSI in short zone ({rsi:.1f})")
    elif rsi is not None:
        reject_reasons.append(f"RSI outside short zone ({rsi:.1f})")

    # ── Candle body not full-range ─────────────────────────────────────────
    if candle_body is not None and candle_body <= 75.0:
        score += 5
        reasons.append(f"candle body ok ({candle_body:.1f}%)")

    # ── Confidence ─────────────────────────────────────────────────────────
    if score >= HIGH_CONFIDENCE_MIN:
        confidence = "high"
    elif score >= MEDIUM_CONFIDENCE_MIN:
        confidence = "medium"
    else:
        confidence = "low"

    action = "SHORT" if score >= SHORT_SCORE_MIN_TO_TRADE else "SKIP"
    if action == "SKIP" and score < SHORT_SCORE_MIN_TO_TRADE:
        reject_reasons.append(f"score below threshold ({score} < {SHORT_SCORE_MIN_TO_TRADE})")

    result["action"] = action
    result["score"] = score
    result["confidence"] = confidence
    result["reasons"] = reasons
    result["reject_reasons"] = reject_reasons

    return result
