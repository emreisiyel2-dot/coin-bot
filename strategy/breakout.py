import logging

import pandas as pd

from config.settings import BREAKOUT_CONFIG
from indicators.indicators import compute_ema
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class MomentumBreakoutStrategy(BaseStrategy):
    """
    Momentum breakout stratejisi.

    Giriş (beşi aynı anda):
      1. EMA(50) > EMA(100)                              — trend gücü
      2. close > EMA(100)                                — fiyat trend üstünde
      3. close > prev breakout_period barın max(high)    — kırılım
      4. volume >= avg(volume) * volume_multiplier        — hacim onayı
      5. (close - open) / open >= candle_strength_min    — güçlü kapanış mumu

    Çıkış: TP/SL fiyat bazlı — PaperEngine / MultiSymbolEngine tarafından yönetilir.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or BREAKOUT_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_period       = self.config["ema_period"]
        ema_fast_period  = self.config["ema_fast_period"]
        breakout_period  = self.config["breakout_period"]
        vol_avg_period   = self.config["volume_avg_period"]
        vol_multiplier   = self.config["volume_multiplier"]
        candle_strength  = self.config["candle_strength_min"]

        min_bars = max(ema_period, ema_fast_period, breakout_period + 1, vol_avg_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"MomentumBreakoutStrategy: en az {min_bars} bar gerekli — "
                f"mevcut {len(df)}"
            )

        ema_slow = compute_ema(df, ema_period)
        ema_fast = compute_ema(df, ema_fast_period)

        last_close    = float(df["close"].iloc[-1])
        last_open     = float(df["open"].iloc[-1])
        last_ema_slow = float(ema_slow.iloc[-1])
        last_ema_fast = float(ema_fast.iloc[-1])

        # Mevcut bar hariç önceki breakout_period barın direnç seviyesi
        resistance = float(df["high"].iloc[-(breakout_period + 1):-1].max())

        # Hacim: mevcut bar hariç önceki vol_avg_period barın ortalaması
        avg_volume     = float(df["volume"].iloc[-(vol_avg_period + 1):-1].mean())
        current_volume = float(df["volume"].iloc[-1])

        logger.debug(
            "Breakout | close=%.4f ema_fast=%.4f ema_slow=%.4f "
            "resistance=%.4f vol=%.0f avg_vol=%.0f",
            last_close, last_ema_fast, last_ema_slow,
            resistance, current_volume, avg_volume,
        )

        trend_ok    = last_ema_fast > last_ema_slow and last_close > last_ema_slow
        breakout_ok = last_close > resistance
        volume_ok   = avg_volume > 0 and current_volume >= avg_volume * vol_multiplier
        candle_ok   = last_open > 0 and (last_close - last_open) / last_open >= candle_strength

        if trend_ok and breakout_ok and volume_ok and candle_ok:
            logger.info(
                "BUY | close=%.4f | ema_fast=%.4f > ema_slow=%.4f | "
                "breakout > %.4f | vol %.0f >= avg %.0f * %.1f | candle=%.4f%%",
                last_close, last_ema_fast, last_ema_slow, resistance,
                current_volume, avg_volume, vol_multiplier,
                (last_close - last_open) / last_open * 100,
            )
            return Signal.BUY

        return Signal.HOLD
