import logging

import pandas as pd

from config.settings import PULLBACK_CONFIG
from indicators.indicators import compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class TrendPullbackStrategy(BaseStrategy):
    """
    Trend + Pullback stratejisi (V7).

    Giriş (sekizi aynı anda):
      1. EMA(50) > EMA(100)                                   — yükselen trend
      2. (EMA50 - EMA100) / EMA100 >= trend_strength_min       — trend güç filtresi
      3. EMA(50) > EMA(50)[prev]                               — trend slope pozitif
      4. close > EMA(100)                                      — uzun vadeli trend üstünde
      5. close >= EMA(50) * 0.995                              — çok aşağı sarkmamış
      6. abs(close - EMA(50)) / EMA(50) <= ema_proximity       — EMA50'ye yakın
      7. RSI rsi_low–rsi_high arasında                         — momentum ölçümü
      8. close > previous_close                                — reversal confirmation

    Çıkış: TP/SL fiyat bazlı — PaperEngine / MultiSymbolEngine tarafından yönetilir.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or PULLBACK_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_slow_period    = self.config["ema_slow_period"]
        ema_fast_period    = self.config["ema_fast_period"]
        rsi_period         = self.config["rsi_period"]
        rsi_low            = self.config["rsi_low"]
        rsi_high           = self.config["rsi_high"]
        proximity_pct      = self.config["ema_proximity_pct"]
        trend_strength_min = self.config["trend_strength_min"]

        min_bars = max(ema_slow_period, ema_fast_period, rsi_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"TrendPullbackStrategy: en az {min_bars} bar gerekli — "
                f"mevcut {len(df)}"
            )

        ema_slow = compute_ema(df, ema_slow_period)
        ema_fast = compute_ema(df, ema_fast_period)
        rsi      = compute_rsi(df, rsi_period)

        last_close     = float(df["close"].iloc[-1])
        prev_close     = float(df["close"].iloc[-2])
        last_ema_slow  = float(ema_slow.iloc[-1])
        last_ema_fast  = float(ema_fast.iloc[-1])
        prev_ema_fast  = float(ema_fast.iloc[-2])
        last_rsi       = float(rsi.iloc[-1])

        distance_pct   = abs(last_close - last_ema_fast) / last_ema_fast
        trend_strength = (last_ema_fast - last_ema_slow) / last_ema_slow

        logger.debug(
            "Pullback | close=%.4f prev_close=%.4f ema_fast=%.4f ema_slow=%.4f "
            "rsi=%.1f dist=%.4f%% strength=%.4f%%",
            last_close, prev_close, last_ema_fast, last_ema_slow,
            last_rsi, distance_pct * 100, trend_strength * 100,
        )

        trend_ok      = last_ema_fast > last_ema_slow
        strength_ok   = trend_strength >= trend_strength_min
        slope_ok      = last_ema_fast > prev_ema_fast
        above_slow    = last_close > last_ema_slow
        floor_ok      = last_close >= last_ema_fast * 0.995
        proximity_ok  = distance_pct <= proximity_pct
        rsi_ok        = rsi_low <= last_rsi <= rsi_high
        reversal_ok   = last_close > prev_close

        if trend_ok and strength_ok and slope_ok and above_slow and floor_ok and proximity_ok and rsi_ok and reversal_ok:
            logger.info(
                "BUY | close=%.4f > prev=%.4f | ema_fast=%.4f > ema_slow=%.4f | "
                "strength=%.2f%% | dist=%.2f%% | rsi=%.1f in [%.0f, %.0f]",
                last_close, prev_close, last_ema_fast, last_ema_slow,
                trend_strength * 100, distance_pct * 100,
                last_rsi, rsi_low, rsi_high,
            )
            return Signal.BUY

        return Signal.HOLD
