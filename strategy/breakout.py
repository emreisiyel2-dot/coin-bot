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

    Giriş (üçü aynı anda):
      1. close > EMA(ema_period)          — trend filtresi
      2. close > prev breakout_period barın max(high)  — kırılım
      3. volume > avg(volume, volume_avg_period) * volume_multiplier  — hacim onayı

    Çıkış: TP/SL fiyat bazlı — PaperEngine / MultiSymbolEngine tarafından yönetilir.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or BREAKOUT_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_period        = self.config["ema_period"]
        breakout_period   = self.config["breakout_period"]
        vol_avg_period    = self.config["volume_avg_period"]
        vol_multiplier    = self.config["volume_multiplier"]

        min_bars = max(ema_period, breakout_period + 1, vol_avg_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"MomentumBreakoutStrategy: en az {min_bars} bar gerekli — "
                f"mevcut {len(df)}"
            )

        ema = compute_ema(df, ema_period)
        last_close = float(df["close"].iloc[-1])
        last_ema   = float(ema.iloc[-1])

        # Mevcut bar hariç önceki breakout_period barın direnç seviyesi
        resistance = float(df["high"].iloc[-(breakout_period + 1):-1].max())

        # Hacim: mevcut bar hariç önceki vol_avg_period barın ortalaması
        avg_volume     = float(df["volume"].iloc[-(vol_avg_period + 1):-1].mean())
        current_volume = float(df["volume"].iloc[-1])

        logger.debug(
            "Breakout | close=%.4f ema=%.4f resistance=%.4f "
            "vol=%.0f avg_vol=%.0f mult=%.1f",
            last_close, last_ema, resistance,
            current_volume, avg_volume, vol_multiplier,
        )

        trend_ok    = last_close > last_ema
        breakout_ok = last_close > resistance
        volume_ok   = avg_volume > 0 and current_volume >= avg_volume * vol_multiplier

        if trend_ok and breakout_ok and volume_ok:
            logger.info(
                "BUY | close=%.4f > ema=%.4f | breakout > %.4f | "
                "vol %.0f >= avg %.0f * %.1f",
                last_close, last_ema, resistance,
                current_volume, avg_volume, vol_multiplier,
            )
            return Signal.BUY

        return Signal.HOLD
