import logging

import pandas as pd

from config.settings import BREAKOUT_V2_CONFIG
from indicators.indicators import compute_adx, compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class MomentumBreakoutV2Strategy(BaseStrategy):
    """
    Momentum Breakout V2 stratejisi — onaylı giriş.

    Giriş koşulları (tümü aynı anda):
      1. Kırılım mumu (N-1): close > highest(high, breakout_period) + margin
      2. Kırılım mumu (N-1): volume > avg_volume * vol_multiplier
      3. Kırılım mumu (N-1): gövde > %60 ve üst fitil baskın değil (sahte kırılım filtresi)
      4. Onay mumu (N):      close > close[N-1]  — kırılım devam ediyor
      5. EMA(50) > EMA(100), RSI > rsi_min, close > EMA200, ADX filtresi

    Çıkış: TP/SL fiyat bazlı — engine tarafından yönetilir (trailing dahil).
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

        # +2: kırılım mumu (N-1) + onay mumu (N) için gerekli
        min_bars = max(ema_slow_period, ema_fast_period, rsi_period + 1,
                       breakout_period + 2, vol_avg_period + 2, ema_regime_period,
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

        # Onay mumu (N = son bar)
        last_close      = float(df["close"].iloc[-1])
        last_ema_slow   = float(ema_slow.iloc[-1])
        last_ema_fast   = float(ema_fast.iloc[-1])
        last_ema_regime = float(ema_regime.iloc[-1])
        last_rsi        = float(rsi.iloc[-1])
        last_adx        = float(adx_result.adx.iloc[-1])
        last_plus_di    = float(adx_result.plus_di.iloc[-1])
        last_minus_di   = float(adx_result.minus_di.iloc[-1])

        # Kırılım mumu (N-1 = bir önceki bar)
        prev_close  = float(df["close"].iloc[-2])
        prev_open   = float(df["open"].iloc[-2])
        prev_high   = float(df["high"].iloc[-2])
        prev_low    = float(df["low"].iloc[-2])
        prev_volume = float(df["volume"].iloc[-2])

        # Eşikler kırılım mumuyla ilgili barlardan hesaplanır
        highest_high       = float(df["high"].iloc[-(breakout_period + 2):-2].max())
        avg_volume         = float(df["volume"].iloc[-(vol_avg_period + 2):-2].mean())
        breakout_threshold = highest_high * (1 + breakout_margin_pct)

        # 1. Kırılım + hacim (N-1 barında)
        breakout_ok = prev_close > breakout_threshold
        volume_ok   = avg_volume > 0 and prev_volume > avg_volume * vol_multiplier

        # 2. Mum gücü filtresi (N-1 barı): gövde > %60, üst fitil baskın değil
        candle_range = prev_high - prev_low
        if candle_range > 0:
            body         = abs(prev_close - prev_open)
            upper_wick   = prev_high - max(prev_close, prev_open)
            candle_ok    = (body / candle_range > 0.50) and (upper_wick / candle_range < 0.40)
        else:
            candle_ok = False

        # 3. Fiyat devamı onayı (N barında): close > önceki close
        continuation_ok = last_close > prev_close

        # 4. Trend + momentum + rejim + ADX + EMA200 slope (N barında)
        trend_ok  = last_ema_fast > last_ema_slow
        rsi_ok    = last_rsi > rsi_min
        regime_ok = last_close > last_ema_regime
        adx_ok    = (last_adx > adx_min and last_plus_di > last_minus_di) if adx_min > 0 else True
        # EMA200 yükselen mi? — son 5 bar öncesiyle karşılaştır
        slope_ok  = float(ema_regime.iloc[-1]) > float(ema_regime.iloc[-5])

        logger.debug(
            "BreakoutV2 | prev_close=%.4f threshold=%.4f breakout=%s candle=%s cont=%s "
            "vol=%s trend=%s rsi=%.1f adx=%.1f slope=%s",
            prev_close, breakout_threshold, breakout_ok, candle_ok, continuation_ok,
            volume_ok, trend_ok, last_rsi, last_adx, slope_ok,
        )

        if breakout_ok and volume_ok and candle_ok and continuation_ok and trend_ok and rsi_ok and regime_ok and adx_ok and slope_ok:
            logger.info(
                "BUY | onay=%.4f > kırılım=%.4f > eşik=%.4f | vol %.0f > avg %.0f * %.1f | "
                "ema_fast=%.4f > ema_slow=%.4f | rsi=%.1f > %.0f",
                last_close, prev_close, breakout_threshold,
                prev_volume, avg_volume, vol_multiplier,
                last_ema_fast, last_ema_slow, last_rsi, rsi_min,
            )
            return Signal.BUY

        return Signal.HOLD
