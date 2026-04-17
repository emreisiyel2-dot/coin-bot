import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_ema(df: pd.DataFrame, period: int) -> pd.Series:
    if len(df) < period:
        raise ValueError(
            f"compute_ema: df en az {period} satır içermeli, mevcut: {len(df)}"
        )
    logger.debug("compute_ema | period=%d | rows=%d", period, len(df))
    close = df["close"].astype(np.float64)
    result = close.ewm(span=period, adjust=False).mean()
    result.iloc[: period - 1] = np.nan
    result.name = f"ema_{period}"
    return result


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if len(df) < period + 1:
        raise ValueError(
            f"compute_rsi: df en az {period + 1} satır içermeli, mevcut: {len(df)}"
        )
    logger.debug("compute_rsi | period=%d | rows=%d", period, len(df))
    close = df["close"].astype(np.float64)
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-diff).clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi.iloc[0] = np.nan
    rsi.name = "rsi"
    return rsi


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if len(df) < period + 1:
        raise ValueError(
            f"compute_atr: df en az {period + 1} satır içermeli, mevcut: {len(df)}"
        )
    logger.debug("compute_atr | period=%d | rows=%d", period, len(df))
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    close = df["close"].astype(np.float64)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(com=period - 1, adjust=False).mean()
    atr.iloc[0] = np.nan
    atr.name = "atr"
    return atr


class ADXResult:
    """ADX hesaplama sonucu: adx, plus_di, minus_di serileri."""
    __slots__ = ("adx", "plus_di", "minus_di")

    def __init__(self, adx: pd.Series, plus_di: pd.Series, minus_di: pd.Series) -> None:
        self.adx      = adx
        self.plus_di  = plus_di
        self.minus_di = minus_di


def compute_adx(df: pd.DataFrame, period: int = 14) -> ADXResult:
    if len(df) < period * 2 + 1:
        raise ValueError(
            f"compute_adx: df en az {period * 2 + 1} satır içermeli, mevcut: {len(df)}"
        )
    logger.debug("compute_adx | period=%d | rows=%d", period, len(df))
    high  = df["high"].astype(np.float64)
    low   = df["low"].astype(np.float64)
    close = df["close"].astype(np.float64)

    plus_dm  = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    overlap  = (plus_dm > 0) & (minus_dm > 0)
    plus_dm[overlap & (minus_dm >= plus_dm)]  = 0.0
    minus_dm[overlap & (plus_dm > minus_dm)]  = 0.0

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    atr_s    = tr.ewm(com=period - 1, adjust=False).mean()
    plus_di  = 100.0 * plus_dm.ewm(com=period - 1, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(com=period - 1, adjust=False).mean() / atr_s
    dx       = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx      = dx.ewm(com=period - 1, adjust=False).mean()
    adx.name      = f"adx_{period}"
    plus_di.name  = f"plus_di_{period}"
    minus_di.name = f"minus_di_{period}"
    return ADXResult(adx=adx, plus_di=plus_di, minus_di=minus_di)


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    if len(df) == 0:
        raise ValueError("compute_vwap: df boş olamaz")
    logger.debug("compute_vwap | rows=%d", len(df))
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    zero_vol = volume == 0.0
    tp = (high + low + close) / 3.0
    vwap = (tp * volume).cumsum() / volume.cumsum()
    vwap[zero_vol] = np.nan
    vwap.name = "vwap"
    return vwap
