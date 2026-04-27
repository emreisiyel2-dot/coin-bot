"""
Momentum Pullback v1 — bullish pullback entry.

Tightened entry filters for higher quality signals:
  1. trend bullish  (close > EMA200)
  2. close > EMA50
  3. RSI between 50 and 68
  4. ATR percentage >= 0.18
  5. candle_body_pct <= 75
  6. volume_spike_ratio >= 1.0
  7. distance_ema50_pct between 0.0 and +1.0

No shorting. Returns HOLD if conditions fail with explicit reject reason.
"""

import logging
from typing import Any

from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)

DISTANCE_EMA50_MIN_PCT     = 0.0      # was -0.75 — must be ABOVE EMA50
DISTANCE_EMA50_MAX_PCT     = 1.0      # was 1.25 — not too extended
RSI_MIN                     = 50.0     # was 45.0
RSI_MAX                     = 68.0
ATR_PCT_MIN                 = 0.18     # was 0.20
CANDLE_BODY_MAX_PCT         = 75.0     # was 85.0
VOLUME_SPIKE_MIN            = 1.0      # was 0.80


class MomentumPullbackV1Strategy(BaseStrategy):
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

        # 1. Trend filter: must be bullish (close > EMA200)
        close_price = features.get("close")
        ema_200 = features.get("ema_200")
        if trend != "bullish" or close_price is None or ema_200 is None or close_price <= ema_200:
            self.last_rejection_reason = "trend_not_bullish"
            return Signal.HOLD

        # 2. Close must be above EMA50
        ema_50 = features.get("ema_50")
        if ema_50 is not None and close_price is not None and close_price <= ema_50:
            self.last_rejection_reason = "price_below_ema50"
            return Signal.HOLD

        # 3. Distance to EMA50: must be above but not overextended
        if dist_ema50 is None:
            self.last_rejection_reason = "not_near_ema50"
            return Signal.HOLD
        if dist_ema50 < DISTANCE_EMA50_MIN_PCT:
            logger.debug("REJECT | %s | below_ema50 | dist=%.3f%% < min=%.3f%%",
                         symbol, dist_ema50, DISTANCE_EMA50_MIN_PCT)
            self.last_rejection_reason = "price_below_ema50"
            return Signal.HOLD
        if dist_ema50 > DISTANCE_EMA50_MAX_PCT:
            logger.debug("REJECT | %s | too_extended | dist=%.3f%% > max=%.3f%%",
                         symbol, dist_ema50, DISTANCE_EMA50_MAX_PCT)
            self.last_rejection_reason = "too_extended_from_ema50"
            return Signal.HOLD

        # 4. RSI range: 50-68 (was 45-68)
        if rsi is None:
            self.last_rejection_reason = "rsi_out_of_range"
            return Signal.HOLD
        if rsi < RSI_MIN:
            logger.debug("REJECT | %s | rsi_too_low | rsi=%.1f < min=%.1f",
                         symbol, rsi, RSI_MIN)
            self.last_rejection_reason = "rsi_too_low"
            return Signal.HOLD
        if rsi > RSI_MAX:
            self.last_rejection_reason = "rsi_out_of_range"
            return Signal.HOLD

        # 5. ATR minimum (was 0.20, now 0.18)
        if atr_pct is None or atr_pct < ATR_PCT_MIN:
            logger.debug("REJECT | %s | atr_too_low | atr=%.3f%% < min=%.3f%%",
                         symbol, atr_pct or 0, ATR_PCT_MIN)
            self.last_rejection_reason = "atr_too_low"
            return Signal.HOLD

        # 6. Candle body max (was 85, now 75)
        if candle_body is not None and candle_body > CANDLE_BODY_MAX_PCT:
            logger.debug("REJECT | %s | candle_too_large | body=%.0f%% > max=%.0f%%",
                         symbol, candle_body, CANDLE_BODY_MAX_PCT)
            self.last_rejection_reason = "candle_too_large"
            return Signal.HOLD

        # 7. Volume minimum (was 0.80, now 1.0)
        if vol_spike is None or vol_spike < VOLUME_SPIKE_MIN:
            logger.debug("REJECT | %s | volume_too_weak | vol=%.2fx < min=%.2fx",
                         symbol, vol_spike or 0, VOLUME_SPIKE_MIN)
            self.last_rejection_reason = "volume_too_weak"
            return Signal.HOLD

        logger.info(
            "BUY | %s | rsi=%.1f dist_ema50=%.2f%% atr=%.3f%% vol=%.2fx body=%.0f%%",
            symbol or "?", rsi, dist_ema50, atr_pct, vol_spike or 0, candle_body or 0,
        )
        return Signal.BUY
