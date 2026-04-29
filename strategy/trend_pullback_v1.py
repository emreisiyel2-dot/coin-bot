"""
Trend Pullback v1 — long-only pullback strategy.

Captures pullbacks within an existing bullish trend.
More frequent than momentum_pullback_v1 but still controlled.

Entry rules LONG:
  1. trend == "bullish"
  2. close > EMA200
  3. close > EMA50
  4. distance_ema50_pct between -1.5 and +0.3
  5. RSI between 40 and 60
  6. volume_spike_ratio >= 0.9
  7. atr_pct >= 0.15
  8. candle_body_pct <= 80

Scoring:
  base score = 50 (all hard filters pass)
  +10 if RSI between 45 and 55
  +10 if volume_spike_ratio > 1.2
  +10 if price just crossed above EMA50 (if data available)
  Threshold: 55

Paper trading only.
"""

import logging
from typing import Any

from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)

TREND_PULLBACK_SCORE_MIN = 55

# Hard filter constants
DIST_EMA50_MIN = -1.5
DIST_EMA50_MAX = 0.3
DIST_EMA50_HARD_MAX = 1.0
RSI_MIN = 40.0
RSI_MAX = 60.0
RSI_HARD_MIN = 35.0
RSI_HARD_MAX = 65.0
ATR_PCT_MIN = 0.15
CANDLE_BODY_MAX_PCT = 80.0
VOLUME_SPIKE_MIN = 0.9

# Bonus thresholds
RSI_SWEET_MIN = 45.0
RSI_SWEET_MAX = 55.0
VOLUME_BONUS_THRESHOLD = 1.2
BASE_SCORE = 50


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
            self.last_rejection_reason = "no_features"
            self._set_decision("SKIP", 0, [], ["no_features"])
            return Signal.HOLD

        return self._evaluate_long(features, symbol)

    def _evaluate_long(self, features: dict, symbol: str | None) -> Signal:
        reasons: list[str] = []
        rejects: list[str] = []

        # Pullback summary for logging
        pullback = {
            "distance_ema50_pct": features.get("distance_ema50_pct"),
            "rsi": features.get("rsi"),
            "volume_spike_ratio": features.get("volume_spike_ratio"),
            "atr_pct": features.get("atr_pct"),
        }

        # 1. Trend
        trend = features.get("trend", "neutral")
        if trend != "bullish":
            self.last_rejection_reason = "trend_not_bullish"
            rejects.append("trend_not_bullish")
            self._set_decision("SKIP", 0, reasons, rejects, pullback)
            return Signal.HOLD

        close_price = features.get("close")
        ema_200 = features.get("ema_200")
        ema_50 = features.get("ema_50")

        # 2. EMA200
        if ema_200 is not None and close_price is not None and close_price <= ema_200:
            self.last_rejection_reason = "price_below_ema200"
            rejects.append("price_below_ema200")
            self._set_decision("SKIP", 0, reasons, rejects, pullback)
            return Signal.HOLD

        # 3. EMA50
        if ema_50 is not None and close_price is not None and close_price <= ema_50:
            self.last_rejection_reason = "price_below_ema50"
            rejects.append("price_below_ema50")
            self._set_decision("SKIP", 0, reasons, rejects, pullback)
            return Signal.HOLD

        # Hard filters passed — start with base score
        score = BASE_SCORE

        # 4. Pullback zone (distance to EMA50)
        dist_ema50 = features.get("distance_ema50_pct")
        if dist_ema50 is None:
            self.last_rejection_reason = "pullback_invalid"
            rejects.append("pullback_invalid")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        if dist_ema50 > DIST_EMA50_HARD_MAX:
            self.last_rejection_reason = "too_extended_from_ema50"
            rejects.append("too_extended_from_ema50")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        if not (DIST_EMA50_MIN <= dist_ema50 <= DIST_EMA50_MAX):
            self.last_rejection_reason = "pullback_invalid"
            rejects.append("pullback_invalid")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        reasons.append("pullback_valid")

        # 5. RSI
        rsi = features.get("rsi")
        if rsi is None or rsi < RSI_HARD_MIN or rsi > RSI_HARD_MAX:
            self.last_rejection_reason = "rsi_out_of_range"
            rejects.append("rsi_out_of_range")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        if not (RSI_MIN <= rsi <= RSI_MAX):
            self.last_rejection_reason = "rsi_out_of_range"
            rejects.append("rsi_out_of_range")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        reasons.append("rsi_in_range")
        if RSI_SWEET_MIN <= rsi <= RSI_SWEET_MAX:
            score += 10
            reasons.append("rsi_sweet_spot")

        # 6. Volume
        vol_spike = features.get("volume_spike_ratio")
        if vol_spike is None or vol_spike < VOLUME_SPIKE_MIN:
            self.last_rejection_reason = "volume_too_weak"
            rejects.append("volume_too_weak")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        reasons.append("volume_adequate")
        if vol_spike > VOLUME_BONUS_THRESHOLD:
            score += 10
            reasons.append("volume_strong")

        # 7. ATR
        atr_pct = features.get("atr_pct")
        if atr_pct is None or atr_pct < ATR_PCT_MIN:
            self.last_rejection_reason = "atr_too_low"
            rejects.append("atr_too_low")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        reasons.append("atr_adequate")

        # 8. Candle body
        candle_body = features.get("candle_body_pct")
        if candle_body is not None and candle_body > CANDLE_BODY_MAX_PCT:
            self.last_rejection_reason = "candle_too_large"
            rejects.append("candle_too_large")
            self._set_decision("SKIP", score, reasons, rejects, pullback)
            return Signal.HOLD
        reasons.append("candle_acceptable")

        # 9. EMA50 cross bonus (safe — skip if data not available)
        prev_close = features.get("previous_close")
        if prev_close is not None and ema_50 is not None:
            if prev_close <= ema_50 and close_price is not None and close_price > ema_50:
                score += 10
                reasons.append("crossed_above_ema50")

        # Score gate
        if score < TREND_PULLBACK_SCORE_MIN:
            self.last_rejection_reason = f"score_too_low ({score} < {TREND_PULLBACK_SCORE_MIN})"
            self.last_side = "long"
            self.last_score = score
            self._set_decision("SKIP", score, reasons, [self.last_rejection_reason], pullback)
            return Signal.HOLD

        # Signal accepted
        self.last_side = "long"
        self.last_score = score
        self.last_rejection_reason = ""
        self.last_decision = {
            "action": "BUY",
            "side": "long",
            "score": score,
            "threshold": TREND_PULLBACK_SCORE_MIN,
            "reasons": reasons,
            "reject_reasons": [],
            "pullback": pullback,
        }

        logger.info(
            "BUY | %s | score=%d | rsi=%.1f dist=%.2f%% atr=%.3f%% vol=%.2fx body=%.0f%%",
            symbol or "?", score, rsi, dist_ema50, atr_pct,
            vol_spike or 0, candle_body or 0,
        )
        return Signal.BUY

    def _set_decision(self, action: str, score: int,
                      reasons: list[str], reject_reasons: list[str],
                      pullback: dict | None = None) -> None:
        self.last_decision = {
            "action": action,
            "side": "long" if action == "BUY" else "none",
            "score": score,
            "threshold": TREND_PULLBACK_SCORE_MIN,
            "reasons": reasons,
            "reject_reasons": reject_reasons,
            "pullback": pullback or {},
        }
