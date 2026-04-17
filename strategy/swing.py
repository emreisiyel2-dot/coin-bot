import logging

import pandas as pd

from config.settings import STRATEGY_CONFIG
from indicators.indicators import compute_ema, compute_rsi
from strategy.base import BaseStrategy
from strategy.signals import Signal

logger = logging.getLogger(__name__)


class SwingStrategy(BaseStrategy):
    """
    1h trend-following swing stratejisi.

    Giriş: close > EMA(ema_period) VE RSI < rsi_oversold
    Çıkış: TP/SL fiyat bazlı — PaperEngine tarafından yönetilir
    Diğer: HOLD

    Risk yönetimi (cooldown, max pozisyon) bu modülde değil, RiskManager'da.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or STRATEGY_CONFIG

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        ema_period = self.config["ema_period"]
        rsi_period = self.config["rsi_period"]
        rsi_oversold = self.config["rsi_oversold"]

        min_bars = max(ema_period, rsi_period + 1)
        if len(df) < min_bars:
            raise ValueError(
                f"SwingStrategy: en az {min_bars} bar yeterli değil — "
                f"mevcut {len(df)} bar (ema_period={ema_period})"
            )

        ema = compute_ema(df, ema_period)
        rsi = compute_rsi(df, rsi_period)

        last_close = float(df["close"].iloc[-1])
        last_ema = float(ema.iloc[-1])
        last_rsi = float(rsi.iloc[-1])

        logger.debug(
            "SwingStrategy | close=%.2f ema=%.2f rsi=%.2f | oversold=%.1f",
            last_close, last_ema, last_rsi, rsi_oversold,
        )

        bullish_trend = last_close > last_ema

        if bullish_trend and last_rsi < rsi_oversold:
            logger.info("BUY | close=%.2f > ema=%.2f, rsi=%.1f < %.1f",
                        last_close, last_ema, last_rsi, rsi_oversold)
            return Signal.BUY

        return Signal.HOLD
