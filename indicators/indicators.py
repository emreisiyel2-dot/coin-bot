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
