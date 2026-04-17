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

RISK_CONFIG = {
    "max_open_positions":   _int("RISK_MAX_OPEN_POS", 3),
    "cooldown_bars":        _int("RISK_COOLDOWN_BARS", 5),
    "daily_max_trades":     _int("RISK_DAILY_MAX_TRADES", 10),
    "daily_max_loss_pct":   _float("RISK_DAILY_MAX_LOSS_PCT", 0.03),
}

UNIVERSE_SYMBOLS = [s.strip() for s in os.getenv(
    "UNIVERSE_SYMBOLS",
    "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,AVAX/USDT,ADA/USDT,DOGE/USDT",
).split(",")]

SCANNER_CONFIG = {
    # Filtreler
    "min_atr_pct":              _float("SCAN_MIN_ATR_PCT", 0.005),   # fiyatın %0.5'i
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
