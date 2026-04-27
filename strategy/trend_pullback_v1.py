"""
Trend Pullback v1 — dual-direction pullback strategy.

Trades pullbacks inside an existing trend. Supports both long and short
depending on trend direction and market regime.

LONG setup (regime == bullish or neutral):
  1. trend == bullish
  2. close > EMA200
  3. distance_ema50_pct between -0.35 and +0.75
  4. RSI between 40 and 58
  5. ATR percentage >= 0.15
  6. candle_body_pct <= 80
  7. volume_spike_ratio >= 0.70

SHORT setup (regime == bearish or neutral):
  1. trend == bearish
  2. close < EMA200
  3. distance_ema50_pct between -0.75 and +0.35
  4. RSI between 42 and 62
  5. ATR percentage >= 0.15
  6. candle_body_pct <= 80
  7. volume_spike_ratio >= 0.70

Internal scoring:
  base 0
  +20 trend aligned
  +20 near EMA50
  +15 RSI in pullback range
  +15 ATR adequate
  +15 volume adequate
  +15 candle acceptable

Threshold: 70

Paper trading only.
"""

import logging
from typing import Any

from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)

TREND_PULLBACK_SCORE_MIN = 70

# ── Long setup constants ──────────────────────────────────────────────────────
LONG_DIST_EMA50_MIN = -0.35
LONG_DIST_EMA50_MAX = 0.75
LONG_RSI_MIN = 40.0
LONG_RSI_MAX = 58.0

# ── Short setup constants ─────────────────────────────────────────────────────
SHORT_DIST_EMA50_MIN = -0.75
SHORT_DIST_EMA50_MAX = 0.35
SHORT_RSI_MIN = 42.0
SHORT_RSI_MAX = 62.0

# ── Shared constants ──────────────────────────────────────────────────────────
ATR_PCT_MIN = 0.15
CANDLE_BODY_MAX_PCT = 80.0
VOLUME_SPIKE_MIN = 0.70

# ── Score weights ──────────────────────────────────────────────────────────────
SCORE_TREND = 20
SCORE_NEAR_EMA50 = 20
SCORE_RSI = 15
SCORE_ATR = 15
SCORE_VOLUME = 15
SCORE_CANDLE = 15


class TrendPullbackV1Strategy(BaseStrategy):
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.last_rejection_reason: str = ""
        self.last_side: str = ""
        self.last_score: int = 0
        self.last_decision: dict[str, Any] = {}

    def generate_signal(
        self,
        df: Any = None,
        symbol: str | None = None,
        features: dict | None = None,
        score_decision: dict | None = None,
        market_regime: dict | None = None,
    ) -> Signal:
        self.last_rejection_reason = ""
        self.last_side = ""
        self.last_score = 0
        self.last_decision = {}

        if not features:
            self._set_decision("SKIP", 0, [], ["no_features"])
            self.last_rejection_reason = "no_features"
            return Signal.HOLD

        regime = (market_regime or {}).get("regime", "neutral")
        long_result = self._evaluate_long(features, regime, symbol)
        short_result = self._evaluate_short(features, regime, symbol)

        # Pick best passing setup, or none
        candidates = []
        if long_result["signal"] is not None:
            candidates.append(long_result)
        if short_result["signal"] is not None:
            candidates.append(short_result)

        if not candidates:
            # Both rejected — report the more relevant one
            if regime == "bearish":
                best = short_result
            elif regime == "bullish":
                best = long_result
            else:
                best = long_result if long_result["score"] >= short_result["score"] else short_result

            self.last_rejection_reason = best["reject_reasons"][0] if best["reject_reasons"] else "no_setup"
            self.last_side = ""
            self.last_score = best["score"]
            self.last_decision = {
                "action": "SKIP",
                "side": "none",
                "score": best["score"],
                "threshold": TREND_PULLBACK_SCORE_MIN,
                "reasons": best["reasons"],
                "reject_reasons": best["reject_reasons"],
            }
            return Signal.HOLD

        # Pick highest scoring candidate
        best = max(candidates, key=lambda c: c["score"])
        if best["score"] < TREND_PULLBACK_SCORE_MIN:
            self.last_rejection_reason = f"score_too_low ({best['score']} < {TREND_PULLBACK_SCORE_MIN})"
            self.last_side = best["side"]
            self.last_score = best["score"]
            self.last_decision = {
                "action": "SKIP",
                "side": best["side"],
                "score": best["score"],
                "threshold": TREND_PULLBACK_SCORE_MIN,
                "reasons": best["reasons"],
                "reject_reasons": [self.last_rejection_reason],
            }
            return Signal.HOLD

        # Signal accepted
        signal = best["signal"]
        self.last_side = best["side"]
        self.last_score = best["score"]
        self.last_rejection_reason = ""

        logger.info(
            "%s | %s | score=%d | rsi=%.1f dist_ema50=%.2f%% atr=%.3f%% vol=%.2fx body=%.0f%%",
            "BUY" if signal == Signal.BUY else "SHORT",
            symbol or "?", best["score"],
            features.get("rsi", 0), features.get("distance_ema50_pct", 0),
            features.get("atr_pct", 0), features.get("volume_spike_ratio", 0),
            features.get("candle_body_pct", 0),
        )

        self.last_decision = {
            "action": "BUY" if signal == Signal.BUY else "SHORT",
            "side": best["side"],
            "score": best["score"],
            "threshold": TREND_PULLBACK_SCORE_MIN,
            "reasons": best["reasons"],
            "reject_reasons": [],
        }
        return signal

    def _evaluate_long(self, features: dict, regime: str, symbol: str | None) -> dict:
        reasons: list[str] = []
        rejects: list[str] = []
        score = 0

        # Regime gate
        if regime == "bearish":
            rejects.append("regime_too_bearish_for_long")
            return {"signal": None, "side": "long", "score": 0,
                    "reasons": reasons, "reject_reasons": rejects}

        # Trend
        trend = features.get("trend", "neutral")
        if trend == "bullish":
            score += SCORE_TREND
            reasons.append("trend_aligned")
        else:
            rejects.append("trend_not_bullish")
            return {"signal": None, "side": "long", "score": score,
                    "reasons": reasons, "reject_reasons": rejects}

        # EMA200
        close_price = features.get("close")
        ema_200 = features.get("ema_200")
        if ema_200 is not None and close_price is not None and close_price <= ema_200:
            rejects.append("price_below_ema200")
            return {"signal": None, "side": "long", "score": score,
                    "reasons": reasons, "reject_reasons": rejects}

        # Distance to EMA50
        dist_ema50 = features.get("distance_ema50_pct")
        if dist_ema50 is not None and LONG_DIST_EMA50_MIN <= dist_ema50 <= LONG_DIST_EMA50_MAX:
            score += SCORE_NEAR_EMA50
            reasons.append("near_ema50")
        else:
            rejects.append("not_near_ema50")

        # RSI
        rsi = features.get("rsi")
        if rsi is not None and LONG_RSI_MIN <= rsi <= LONG_RSI_MAX:
            score += SCORE_RSI
            reasons.append("rsi_in_range")
        else:
            rejects.append("rsi_out_of_pullback_range")

        # ATR
        atr_pct = features.get("atr_pct")
        if atr_pct is not None and atr_pct >= ATR_PCT_MIN:
            score += SCORE_ATR
            reasons.append("atr_adequate")
        else:
            rejects.append("atr_too_low")

        # Candle body
        candle_body = features.get("candle_body_pct")
        if candle_body is not None and candle_body <= CANDLE_BODY_MAX_PCT:
            score += SCORE_CANDLE
            reasons.append("candle_acceptable")
        else:
            rejects.append("candle_too_large")

        # Volume
        vol_spike = features.get("volume_spike_ratio")
        if vol_spike is not None and vol_spike >= VOLUME_SPIKE_MIN:
            score += SCORE_VOLUME
            reasons.append("volume_adequate")
        else:
            rejects.append("volume_too_weak")

        signal = Signal.BUY if score >= TREND_PULLBACK_SCORE_MIN else None
        return {"signal": signal, "side": "long", "score": score,
                "reasons": reasons, "reject_reasons": rejects}

    def _evaluate_short(self, features: dict, regime: str, symbol: str | None) -> dict:
        reasons: list[str] = []
        rejects: list[str] = []
        score = 0

        # Regime gate
        if regime == "bullish":
            rejects.append("regime_too_bullish_for_short")
            return {"signal": None, "side": "short", "score": 0,
                    "reasons": reasons, "reject_reasons": rejects}

        # Trend
        trend = features.get("trend", "neutral")
        if trend == "bearish":
            score += SCORE_TREND
            reasons.append("trend_aligned")
        else:
            rejects.append("trend_not_bearish")
            return {"signal": None, "side": "short", "score": score,
                    "reasons": reasons, "reject_reasons": rejects}

        # EMA200
        close_price = features.get("close")
        ema_200 = features.get("ema_200")
        if ema_200 is not None and close_price is not None and close_price >= ema_200:
            rejects.append("price_above_ema200")
            return {"signal": None, "side": "short", "score": score,
                    "reasons": reasons, "reject_reasons": rejects}

        # Distance to EMA50
        dist_ema50 = features.get("distance_ema50_pct")
        if dist_ema50 is not None and SHORT_DIST_EMA50_MIN <= dist_ema50 <= SHORT_DIST_EMA50_MAX:
            score += SCORE_NEAR_EMA50
            reasons.append("near_ema50")
        else:
            rejects.append("not_near_ema50")

        # RSI
        rsi = features.get("rsi")
        if rsi is not None and SHORT_RSI_MIN <= rsi <= SHORT_RSI_MAX:
            score += SCORE_RSI
            reasons.append("rsi_in_range")
        else:
            rejects.append("rsi_out_of_pullback_range")

        # ATR
        atr_pct = features.get("atr_pct")
        if atr_pct is not None and atr_pct >= ATR_PCT_MIN:
            score += SCORE_ATR
            reasons.append("atr_adequate")
        else:
            rejects.append("atr_too_low")

        # Candle body
        candle_body = features.get("candle_body_pct")
        if candle_body is not None and candle_body <= CANDLE_BODY_MAX_PCT:
            score += SCORE_CANDLE
            reasons.append("candle_acceptable")
        else:
            rejects.append("candle_too_large")

        # Volume
        vol_spike = features.get("volume_spike_ratio")
        if vol_spike is not None and vol_spike >= VOLUME_SPIKE_MIN:
            score += SCORE_VOLUME
            reasons.append("volume_adequate")
        else:
            rejects.append("volume_too_weak")

        signal = Signal.SHORT if score >= TREND_PULLBACK_SCORE_MIN else None
        return {"signal": signal, "side": "short", "score": score,
                "reasons": reasons, "reject_reasons": rejects}

    def _set_decision(self, action: str, score: int,
                      reasons: list[str], reject_reasons: list[str]) -> None:
        self.last_decision = {
            "action": action,
            "side": "none",
            "score": score,
            "threshold": TREND_PULLBACK_SCORE_MIN,
            "reasons": reasons,
            "reject_reasons": reject_reasons,
        }
