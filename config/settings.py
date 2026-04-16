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
    "symbols": [s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,BNB/USDT").split(",")],
    "timeframe": os.getenv("TIMEFRAME", "1h"),
    "initial_balance_usdt": _float("INITIAL_BALANCE_USDT", 300.0),
}

STRATEGY_CONFIG = {
    "ema_period":              _int("EMA_PERIOD", 200),
    "rsi_period":              _int("RSI_PERIOD", 14),
    "rsi_oversold":            _float("RSI_OVERSOLD", 35.0),
    "rsi_midline":             _float("RSI_MIDLINE", 60.0),
    "cooldown_bars":           _int("COOLDOWN_BARS", 5),
    "max_open_positions":      _int("MAX_OPEN_POSITIONS", 2),
    "max_positions_per_symbol": _int("MAX_POSITIONS_PER_SYMBOL", 1),
    "max_trades_per_hour":     _int("MAX_TRADES_PER_HOUR", 3),
    "position_size_pct":       _float("POSITION_SIZE_PCT", 0.15),
    "stop_loss_pct":           _float("STOP_LOSS_PCT", 0.025),
    "take_profit_pct":         _float("TAKE_PROFIT_PCT", 0.04),
    "commission_pct":          _float("COMMISSION_PCT", 0.001),
    "slippage_pct":            _float("SLIPPAGE_PCT", 0.0005),
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
