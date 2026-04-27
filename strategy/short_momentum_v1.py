"""
Short Momentum v1 — bearish continuation / pullback short entry.

SHORT conditions:
  1. trend == bearish (close < EMA200)
  2. close < EMA50
  3. RSI between 30 and 50
  4. ATR percentage >= 0.18
  5. candle_body_pct <= 75
  6. volume_spike_ratio >= 1.0
  7. distance_ema50_pct between -1.2 and 0.0

Paper trading only. Returns Signal.SELL (short entry).
"""

import logging
from typing import Any

from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)

SHORT_DISTANCE_EMA50_MIN_PCT = -1.2
SHORT_DISTANCE_EMA50_MAX_PCT = 0.0
SHORT_RSI_MIN = 30.0
SHORT_RSI_MAX = 50.0
SHORT_ATR_PCT_MIN = 0.18
SHORT_CANDLE_BODY_MAX_PCT = 75.0
SHORT_VOLUME_SPIKE_MIN = 1.0
SHORT_RSI_EXTREME = 25.0


class ShortMomentumV1Strategy(BaseStrategy):
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.last_rejection_reason: str = ""

    def generate_signal(
        self,
        df: Any = None,
        symbol: str | None = None,
        features: dict | None = None,
        score_decision: dict | None = None,
    ) -> Signal:
        self.last_rejection_reason = ""

        if not features:
            self.last_rejection_reason = "no_features"
            return Signal.HOLD

        trend = features.get("trend", "neutral")
        dist_ema50 = features.get("distance_ema50_pct")
        rsi = features.get("rsi")
        atr_pct = features.get("atr_pct")
        candle_body = features.get("candle_body_pct")
        vol_spike = features.get("volume_spike_ratio")
        close_price = features.get("close")
        ema_200 = features.get("ema_200")
        ema_50 = features.get("ema_50")

        # 1. Trend must be bearish
        if trend != "bearish":
            self.last_rejection_reason = "trend_not_bearish"
            return Signal.HOLD

        # 2. Close must be below EMA200
        if ema_200 is not None and close_price is not None and close_price >= ema_200:
            self.last_rejection_reason = "price_above_ema200"
            return Signal.HOLD

        # 3. Close must be below EMA50
        if ema_50 is not None and close_price is not None and close_price >= ema_50:
            self.last_rejection_reason = "price_above_ema50"
            return Signal.HOLD

        # 4. RSI range 30-50
        if rsi is None:
            self.last_rejection_reason = "rsi_out_of_range"
            return Signal.HOLD
        if rsi < SHORT_RSI_EXTREME:
            self.last_rejection_reason = "rsi_too_low_extreme"
            return Signal.HOLD
        if rsi < SHORT_RSI_MIN:
            self.last_rejection_reason = "rsi_too_low"
            return Signal.HOLD
        if rsi > SHORT_RSI_MAX:
            self.last_rejection_reason = "rsi_too_high"
            return Signal.HOLD

        # 5. Distance to EMA50: below but not too extended
        if dist_ema50 is None:
            self.last_rejection_reason = "not_near_ema50"
            return Signal.HOLD
        if dist_ema50 < SHORT_DISTANCE_EMA50_MIN_PCT:
            self.last_rejection_reason = "too_extended_below_ema50"
            return Signal.HOLD
        if dist_ema50 > SHORT_DISTANCE_EMA50_MAX_PCT:
            self.last_rejection_reason = "price_above_ema50"
            return Signal.HOLD

        # 6. ATR minimum
        if atr_pct is None or atr_pct < SHORT_ATR_PCT_MIN:
            self.last_rejection_reason = "atr_too_low"
            return Signal.HOLD

        # 7. Candle body max
        if candle_body is not None and candle_body > SHORT_CANDLE_BODY_MAX_PCT:
            self.last_rejection_reason = "candle_too_large"
            return Signal.HOLD

        # 8. Volume minimum
        if vol_spike is None or vol_spike < SHORT_VOLUME_SPIKE_MIN:
            self.last_rejection_reason = "volume_too_weak"
            return Signal.HOLD

        logger.info(
            "SHORT | %s | rsi=%.1f dist_ema50=%.2f%% atr=%.3f%% vol=%.2fx body=%.0f%%",
            symbol or "?", rsi, dist_ema50, atr_pct, vol_spike or 0, candle_body or 0,
        )
        return Signal.SELL
