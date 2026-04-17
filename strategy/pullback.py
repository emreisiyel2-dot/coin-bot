import logging

import pandas as pd

from config.settings import PULLBACK_CONFIG
from indicators.indicators import compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class TrendPullbackStrategy(BaseStrategy):
    """
    Trend + Pullback stratejisi.

    Giriş (beşi aynı anda):
      1. EMA(50) > EMA(100)                          — yükselen trend
      2. close > EMA(100)                            — fiyat uzun vadeli trend üstünde
      3. close <= EMA(50)                            — gerçek pullback (EMA50 altına inmiş)
      4. close, EMA(50)'ye yakın (±ema_proximity_pct) — aşırı uzaklaşmamış
      5. RSI rsi_low–rsi_high arasında               — momentumun dinginleşmesi

    Çıkış: TP/SL fiyat bazlı — PaperEngine / MultiSymbolEngine tarafından yönetilir.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or PULLBACK_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_slow_period  = self.config["ema_slow_period"]
        ema_fast_period  = self.config["ema_fast_period"]
        rsi_period       = self.config["rsi_period"]
        rsi_low          = self.config["rsi_low"]
        rsi_high         = self.config["rsi_high"]
        proximity_pct    = self.config["ema_proximity_pct"]

        min_bars = max(ema_slow_period, ema_fast_period, rsi_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"TrendPullbackStrategy: en az {min_bars} bar gerekli — "
                f"mevcut {len(df)}"
            )

        ema_slow = compute_ema(df, ema_slow_period)
        ema_fast = compute_ema(df, ema_fast_period)
        rsi      = compute_rsi(df, rsi_period)

        last_close    = float(df["close"].iloc[-1])
        last_ema_slow = float(ema_slow.iloc[-1])
        last_ema_fast = float(ema_fast.iloc[-1])
        last_rsi      = float(rsi.iloc[-1])

        # Fiyatın EMA(50)'ye olan uzaklığı (oransal)
        distance_pct = abs(last_close - last_ema_fast) / last_ema_fast

        logger.debug(
            "Pullback | close=%.4f ema_fast=%.4f ema_slow=%.4f "
            "rsi=%.1f dist=%.4f%%",
            last_close, last_ema_fast, last_ema_slow,
            last_rsi, distance_pct * 100,
        )

        trend_ok      = last_ema_fast > last_ema_slow
        above_slow    = last_close > last_ema_slow
        below_fast    = last_close <= last_ema_fast   # gerçek pullback
        proximity_ok  = distance_pct <= proximity_pct
        rsi_ok        = rsi_low <= last_rsi <= rsi_high

        if trend_ok and above_slow and below_fast and proximity_ok and rsi_ok:
            logger.info(
                "BUY | close=%.4f <= ema_fast=%.4f > ema_slow=%.4f | "
                "dist=%.2f%% <= %.2f%% | rsi=%.1f in [%.0f, %.0f]",
                last_close, last_ema_fast, last_ema_slow,
                distance_pct * 100, proximity_pct * 100,
                last_rsi, rsi_low, rsi_high,
            )
            return Signal.BUY

        return Signal.HOLD
