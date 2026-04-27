"""
RSI Mean Reversion Strategy — 15m intraday.

Giriş koşulları (tümü aynı anda):
  1. RSI(7) < 33  — aşırı satım bölgesi
  2. close > EMA(50) — orta vadeli yükselen trend içinde dip alımı
  3. EMA(20) > EMA(50) — kısa vadeli momentum hâlâ yukarı
  4. close > EMA(200) — geniş trend yukarı (trend filtresi)

Çıkış: sabit TP %1.5 / SL %0.8 — engine tarafından yönetilir.
Trailing yok — çok kısa vadeli pozisyon.
"""

import logging

import pandas as pd

from config.settings import RSI_REVERSION_CONFIG
from indicators.indicators import compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)

EMA_TREND_PERIOD = 200


class RsiReversionStrategy(BaseStrategy):
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or RSI_REVERSION_CONFIG
        self.last_rejection_reason: str = ""

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        self.last_rejection_reason = ""
        rsi_period      = self.config["rsi_period"]
        rsi_oversold    = self.config["rsi_oversold"]
        ema_fast_period = self.config["ema_fast_period"]
        ema_slow_period = self.config["ema_slow_period"]

        min_bars = max(EMA_TREND_PERIOD + 1, ema_slow_period + 1, rsi_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"RsiReversion: en az {min_bars} bar gerekli, mevcut {len(df)}"
            )

        ema_fast  = compute_ema(df, ema_fast_period)
        ema_slow  = compute_ema(df, ema_slow_period)
        ema_trend = compute_ema(df, EMA_TREND_PERIOD)
        rsi       = compute_rsi(df, rsi_period)

        last_close    = float(df["close"].iloc[-1])
        last_rsi      = float(rsi.iloc[-1])
        last_ema_fast = float(ema_fast.iloc[-1])
        last_ema_slow = float(ema_slow.iloc[-1])
        last_ema_trend = float(ema_trend.iloc[-1])

        rsi_ok       = last_rsi < rsi_oversold
        trend_ok     = last_close > last_ema_slow
        ema_ok       = last_ema_fast > last_ema_slow
        trend_filter = last_close > last_ema_trend

        logger.debug(
            "RsiReversion | rsi=%.1f(<%s)=%s | close>ema50=%s | ema20>ema50=%s | close>ema200=%s",
            last_rsi, rsi_oversold, rsi_ok, trend_ok, ema_ok, trend_filter,
        )

        if not trend_filter:
            self.last_rejection_reason = "trend_filter_blocked"
            logger.debug(
                "TREND FILTER | close=%.4f <= ema200=%.4f",
                last_close, last_ema_trend,
            )
            return Signal.HOLD

        if rsi_ok and trend_ok and ema_ok:
            logger.info(
                "BUY | rsi=%.1f < %.0f | close=%.4f > ema50=%.4f | ema20=%.4f > ema50=%.4f | ema200=%.4f",
                last_rsi, rsi_oversold, last_close,
                last_ema_slow, last_ema_fast, last_ema_slow, last_ema_trend,
            )
            return Signal.BUY

        return Signal.HOLD
