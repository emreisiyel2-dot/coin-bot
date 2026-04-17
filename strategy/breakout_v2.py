import logging

import pandas as pd

from config.settings import BREAKOUT_V2_CONFIG
from indicators.indicators import compute_adx, compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class MomentumBreakoutV2Strategy(BaseStrategy):
    """
    Momentum Breakout V2 stratejisi.

    Giriş (dördü aynı anda):
      1. close > highest(high, breakout_period)  — yeni yüksek kırılımı
      2. volume > avg(volume, vol_avg_period) * vol_multiplier  — hacim onayı
      3. EMA(50) > EMA(100)                      — yükselen trend
      4. RSI > rsi_min                           — momentum pozitif

    Çıkış: TP/SL fiyat bazlı — engine tarafından yönetilir (partial TP + trailing dahil).
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or BREAKOUT_V2_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_slow_period = self.config["ema_slow_period"]
        ema_fast_period = self.config["ema_fast_period"]
        rsi_period      = self.config["rsi_period"]
        rsi_min         = self.config["rsi_min"]
        breakout_period    = self.config["breakout_period"]
        vol_avg_period     = self.config["vol_avg_period"]
        vol_multiplier     = self.config["vol_multiplier"]
        breakout_margin_pct = self.config.get("breakout_margin_pct", 0.0)
        ema_regime_period   = self.config.get("ema_regime_period", 200)
        adx_period          = self.config.get("adx_period", 14)
        adx_min             = self.config.get("adx_min", 0.0)

        min_bars = max(ema_slow_period, ema_fast_period, rsi_period + 1,
                       breakout_period + 1, vol_avg_period + 1, ema_regime_period,
                       adx_period * 2 + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"MomentumBreakoutV2: en az {min_bars} bar gerekli — mevcut {len(df)}"
            )

        ema_slow   = compute_ema(df, ema_slow_period)
        ema_fast   = compute_ema(df, ema_fast_period)
        ema_regime = compute_ema(df, ema_regime_period)
        rsi        = compute_rsi(df, rsi_period)
        adx_result = compute_adx(df, adx_period)

        last_close      = float(df["close"].iloc[-1])
        last_ema_slow   = float(ema_slow.iloc[-1])
        last_ema_fast   = float(ema_fast.iloc[-1])
        last_ema_regime = float(ema_regime.iloc[-1])
        last_rsi        = float(rsi.iloc[-1])
        last_adx        = float(adx_result.adx.iloc[-1])
        last_plus_di    = float(adx_result.plus_di.iloc[-1])
        last_minus_di   = float(adx_result.minus_di.iloc[-1])

        # Mevcut bar hariç önceki breakout_period barın en yüksek high'ı
        highest_high       = float(df["high"].iloc[-(breakout_period + 1):-1].max())
        avg_volume         = float(df["volume"].iloc[-(vol_avg_period + 1):-1].mean())
        current_volume     = float(df["volume"].iloc[-1])
        breakout_threshold = highest_high * (1 + breakout_margin_pct)

        logger.debug(
            "BreakoutV2 | close=%.4f threshold=%.4f ema_fast=%.4f ema_slow=%.4f "
            "ema_regime=%.4f rsi=%.1f vol=%.0f avg_vol=%.0f",
            last_close, breakout_threshold, last_ema_fast, last_ema_slow,
            last_ema_regime, last_rsi, current_volume, avg_volume,
        )
        breakout_ok = last_close > breakout_threshold
        volume_ok   = avg_volume > 0 and current_volume > avg_volume * vol_multiplier
        trend_ok    = last_ema_fast > last_ema_slow
        rsi_ok      = last_rsi > rsi_min
        regime_ok   = last_close > last_ema_regime
        adx_ok      = (last_adx > adx_min and last_plus_di > last_minus_di) if adx_min > 0 else True

        if breakout_ok and volume_ok and trend_ok and rsi_ok and regime_ok and adx_ok:
            logger.info(
                "BUY | close=%.4f > high20=%.4f | vol %.0f > avg %.0f * %.1f | "
                "ema_fast=%.4f > ema_slow=%.4f | rsi=%.1f > %.0f",
                last_close, highest_high, current_volume, avg_volume, vol_multiplier,
                last_ema_fast, last_ema_slow, last_rsi, rsi_min,
            )
            return Signal.BUY

        return Signal.HOLD
