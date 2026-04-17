import os
from dotenv import load_dotenv

load_dotenv()


def _int(key: str, default: int) -> int:
    raw = os.getenv(key, str(default))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"settings: '{key}' must be an integer, got: {raw!r}")


def _float(key: str, default: float) -> float:
    raw = os.getenv(key, str(default))
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"settings: '{key}' must be a float, got: {raw!r}")


TRADING_CONFIG = {
    "symbols": [s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT").split(",")],
    "timeframe": os.getenv("TIMEFRAME", "1h"),
    "initial_balance_usdt": _float("INITIAL_BALANCE_USDT", 300.0),
}

STRATEGY_CONFIG = {
    "ema_period":              _int("EMA_PERIOD", 100),
    "rsi_period":              _int("RSI_PERIOD", 14),
    "rsi_oversold":            _float("RSI_OVERSOLD", 40.0),
    "rsi_midline":             _float("RSI_MIDLINE", 55.0),
    "cooldown_bars":           _int("COOLDOWN_BARS", 5),
    "max_open_positions":      _int("MAX_OPEN_POSITIONS", 2),
    "max_positions_per_symbol": _int("MAX_POSITIONS_PER_SYMBOL", 1),
    "max_trades_per_hour":     _int("MAX_TRADES_PER_HOUR", 3),
    "position_size_pct":       _float("POSITION_SIZE_PCT", 0.15),
    "stop_loss_pct":           _float("STOP_LOSS_PCT", 0.01),
    "take_profit_pct":         _float("TAKE_PROFIT_PCT", 0.02),
    "commission_pct":          _float("COMMISSION_PCT", 0.001),
    "slippage_pct":            _float("SLIPPAGE_PCT", 0.0005),
}

RISK_CONFIG = {
    "max_open_positions":   _int("RISK_MAX_OPEN_POS", 3),
    "cooldown_bars":        _int("RISK_COOLDOWN_BARS", 5),
    "daily_max_trades":     _int("RISK_DAILY_MAX_TRADES", 10),
    "daily_max_loss_pct":   _float("RISK_DAILY_MAX_LOSS_PCT", 0.03),
}

UNIVERSE_SYMBOLS = [s.strip() for s in os.getenv(
    "UNIVERSE_SYMBOLS",
    "BTC/USDT",
).split(",")]

SCANNER_CONFIG = {
    # Filtreler
    "min_atr_pct":              _float("SCAN_MIN_ATR_PCT", 0.002),   # fiyatın %0.2'si
    "min_volume":               _float("SCAN_MIN_VOLUME", 1_000_000.0),  # absolute USDT
    "volume_window":            _int("SCAN_VOLUME_WINDOW", 20),       # rolling avg penceresi
    "spread_penalty_threshold": _float("SCAN_SPREAD_PENALTY", 0.002), # %0.2 üstü ceza
    # Skor ağırlıkları (toplamı 1.0 olmalı)
    "rsi_weight":               _float("SCAN_RSI_WEIGHT", 0.50),
    "atr_weight":               _float("SCAN_ATR_WEIGHT", 0.30),
    "volume_weight":            _float("SCAN_VOL_WEIGHT", 0.20),
    # Seçim
    "top_n":                    _int("SCAN_TOP_N", 3),
}

BREAKOUT_CONFIG = {
    "ema_period":           _int("BREAKOUT_EMA_PERIOD", 100),
    "ema_fast_period":      _int("BREAKOUT_EMA_FAST_PERIOD", 50),
    "breakout_period":      _int("BREAKOUT_PERIOD", 25),
    "volume_avg_period":    _int("BREAKOUT_VOL_AVG_PERIOD", 20),
    "volume_multiplier":    _float("BREAKOUT_VOL_MULT", 1.7),
    "candle_strength_min":  _float("BREAKOUT_CANDLE_STRENGTH", 0.003),
    "take_profit_pct":      _float("BREAKOUT_TP_PCT", 0.035),
    "stop_loss_pct":        _float("BREAKOUT_SL_PCT", 0.012),
}

BREAKOUT_V2_CONFIG = {
    "ema_slow_period": _int("BV2_EMA_SLOW", 100),
    "ema_fast_period": _int("BV2_EMA_FAST", 50),
    "rsi_period":      _int("BV2_RSI_PERIOD", 14),
    "rsi_min":         _float("BV2_RSI_MIN", 55.0),
    "breakout_period": _int("BV2_BREAKOUT_PERIOD", 20),
    "vol_avg_period":  _int("BV2_VOL_AVG_PERIOD", 20),
    "vol_multiplier":  _float("BV2_VOL_MULT", 1.5),
    "take_profit_pct": _float("BV2_TP_PCT", 0.02),
    "stop_loss_pct":   _float("BV2_SL_PCT", 0.01),
    "partial_tp1_pct": _float("BV2_PARTIAL_TP1_PCT", 0.01),
    "partial_tp1_size":_float("BV2_PARTIAL_TP1_SIZE", 0.5),
    "trailing_stop_pct":_float("BV2_TRAILING_STOP", 0.005),
}

PULLBACK_CONFIG = {
    "ema_slow_period":   _int("PULLBACK_EMA_SLOW", 100),
    "ema_fast_period":   _int("PULLBACK_EMA_FAST", 50),
    "rsi_period":        _int("PULLBACK_RSI_PERIOD", 14),
    "rsi_low":              _float("PULLBACK_RSI_LOW", 45.0),
    "rsi_high":             _float("PULLBACK_RSI_HIGH", 55.0),
    "ema_proximity_pct":    _float("PULLBACK_EMA_PROXIMITY", 0.01),
    "trend_strength_min":   _float("PULLBACK_TREND_STRENGTH", 0.01),
    "take_profit_pct":   _float("PULLBACK_TP_PCT", 0.02),
    "stop_loss_pct":     _float("PULLBACK_SL_PCT", 0.015),
    "partial_tp1_pct":   _float("PULLBACK_PARTIAL_TP1_PCT", 0.01),
    "partial_tp1_size":  _float("PULLBACK_PARTIAL_TP1_SIZE", 0.5),
    "trailing_stop_pct": _float("PULLBACK_TRAILING_STOP", 0.01),
}

SCALPING_CONFIG = {
    "timeframe":               os.getenv("SCALP_TIMEFRAME", "5m"),
    "rsi_period":              _int("SCALP_RSI_PERIOD", 7),
    "rsi_oversold":            _float("SCALP_RSI_OVERSOLD", 30.0),
    "rsi_overbought":          _float("SCALP_RSI_OVERBOUGHT", 70.0),
    "rsi_exit_long":           _float("SCALP_RSI_EXIT_LONG", 60.0),
    "rsi_exit_short":          _float("SCALP_RSI_EXIT_SHORT", 40.0),
    "use_ema_filter":          os.getenv("SCALP_USE_EMA_FILTER", "true").lower() == "true",
    "ema_period":              _int("SCALP_EMA_PERIOD", 50),
    "use_atr_filter":          os.getenv("SCALP_USE_ATR_FILTER", "true").lower() == "true",
    "atr_period":              _int("SCALP_ATR_PERIOD", 14),
    "atr_min_threshold_pct":   _float("SCALP_ATR_MIN_PCT", 0.003),
    "vwap_warmup_bars":        _int("SCALP_VWAP_WARMUP", 12),
    "cooldown_bars":           _int("SCALP_COOLDOWN_BARS", 3),
    "max_open_positions":      _int("SCALP_MAX_OPEN_POS", 3),
    "max_positions_per_symbol": _int("SCALP_MAX_POS_SYM", 1),
    "max_trades_per_day":      _int("SCALP_MAX_TRADES_DAY", 10),
    "max_trades_per_hour":     _int("SCALP_MAX_TRADES_HOUR", 5),
    "position_size_pct":       _float("SCALP_POS_SIZE_PCT", 0.10),
    "stop_loss_pct":           _float("SCALP_SL_PCT", 0.012),
    "take_profit_pct":         _float("SCALP_TP_PCT", 0.024),
    "commission_pct":          _float("SCALP_COMMISSION_PCT", 0.001),
    "slippage_pct":            _float("SCALP_SLIPPAGE_PCT", 0.0005),
}
