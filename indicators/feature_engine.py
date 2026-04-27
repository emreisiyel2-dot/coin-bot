"""
Feature Engine — reusable feature snapshot for observability and scoring.

Produces a dict of computed features from a candle DataFrame.
Never raises on insufficient data — returns partial snapshot with
available fields set to None.
"""

import logging
from typing import Any

import numpy as np
import pandas as pd

from indicators.indicators import compute_atr, compute_ema, compute_rsi

logger = logging.getLogger(__name__)

RSI_PERIOD       = 14
EMA_FAST_PERIOD  = 50
EMA_SLOW_PERIOD  = 200
ATR_PERIOD       = 14
VOLUME_SMA_LEN   = 20
RECENT_LOOKBACK  = 20   # bars for recent_high / recent_low


def compute_feature_snapshot(
    df: pd.DataFrame,
    symbol: str = "",
    timeframe: str = "",
) -> dict[str, Any]:
    """
    Compute a feature snapshot from candle data.

    Returns a dict. Missing values are set to None — caller should
    check before relying on any field.
    """
    snap: dict[str, Any] = {
        "symbol":              symbol,
        "timeframe":           timeframe,
        "close":               None,
        "rsi":                 None,
        "ema_50":              None,
        "ema_200":             None,
        "trend":               "neutral",
        "atr":                 None,
        "atr_pct":             None,
        "volume_sma":          None,
        "volume_spike_ratio":  None,
        "candle_body_pct":     None,
        "recent_high":         None,
        "recent_low":          None,
        "distance_ema50_pct":  None,
        "distance_ema200_pct": None,
    }

    if df is None or len(df) < 2:
        return snap

    last_close = float(df["close"].iloc[-1])
    snap["close"] = round(last_close, 6)

    # RSI
    try:
        rsi = compute_rsi(df, RSI_PERIOD)
        snap["rsi"] = round(float(rsi.iloc[-1]), 2)
    except (ValueError, IndexError):
        pass

    # EMA 50
    try:
        ema50 = compute_ema(df, EMA_FAST_PERIOD)
        val = float(ema50.iloc[-1])
        if np.isfinite(val):
            snap["ema_50"] = round(val, 6)
            snap["distance_ema50_pct"] = round(
                (last_close - val) / val * 100, 4
            ) if val > 0 else None
    except (ValueError, IndexError):
        pass

    # EMA 200
    try:
        ema200 = compute_ema(df, EMA_SLOW_PERIOD)
        val = float(ema200.iloc[-1])
        if np.isfinite(val):
            snap["ema_200"] = round(val, 6)
            snap["distance_ema200_pct"] = round(
                (last_close - val) / val * 100, 4
            ) if val > 0 else None
            if snap["ema_200"] is not None:
                snap["trend"] = "bullish" if last_close > val else "bearish"
    except (ValueError, IndexError):
        pass

    # ATR
    try:
        atr = compute_atr(df, ATR_PERIOD)
        val = float(atr.iloc[-1])
        if np.isfinite(val):
            snap["atr"] = round(val, 6)
            snap["atr_pct"] = round(val / last_close * 100, 4) if last_close > 0 else None
    except (ValueError, IndexError):
        pass

    # Volume SMA + spike ratio
    if len(df) >= VOLUME_SMA_LEN + 1:
        vol = df["volume"].astype(np.float64)
        sma = float(vol.iloc[-(VOLUME_SMA_LEN + 1):-1].mean())
        last_vol = float(vol.iloc[-1])
        snap["volume_sma"] = round(sma, 2)
        snap["volume_spike_ratio"] = round(last_vol / sma, 3) if sma > 0 else None
    elif len(df) >= 2:
        vol = df["volume"].astype(np.float64)
        sma = float(vol.iloc[:-1].mean())
        last_vol = float(vol.iloc[-1])
        snap["volume_sma"] = round(sma, 2)
        snap["volume_spike_ratio"] = round(last_vol / sma, 3) if sma > 0 else None

    # Candle body pct
    last_high  = float(df["high"].iloc[-1])
    last_low   = float(df["low"].iloc[-1])
    last_open  = float(df["open"].iloc[-1])
    candle_range = last_high - last_low
    if candle_range > 0:
        snap["candle_body_pct"] = round(
            abs(last_close - last_open) / candle_range * 100, 2
        )

    # Recent high / low
    lookback = min(RECENT_LOOKBACK, len(df))
    snap["recent_high"] = round(float(df["high"].iloc[-lookback:].max()), 6)
    snap["recent_low"]  = round(float(df["low"].iloc[-lookback:].min()), 6)

    return snap
