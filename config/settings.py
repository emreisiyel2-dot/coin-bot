import os
from dotenv import load_dotenv

load_dotenv()

TRADING_CONFIG = {
    "symbols": os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,BNB/USDT").split(","),
    "timeframe": os.getenv("TIMEFRAME", "1h"),
    "initial_balance_usdt": float(os.getenv("INITIAL_BALANCE_USDT", "300.0")),
}

STRATEGY_CONFIG = {
    "ema_period": int(os.getenv("EMA_PERIOD", "200")),
    "rsi_period": int(os.getenv("RSI_PERIOD", "14")),
    "rsi_oversold": float(os.getenv("RSI_OVERSOLD", "35.0")),
    "rsi_midline": float(os.getenv("RSI_MIDLINE", "60.0")),
    "cooldown_bars": int(os.getenv("COOLDOWN_BARS", "5")),
    "max_open_positions": int(os.getenv("MAX_OPEN_POSITIONS", "2")),
    "max_positions_per_symbol": int(os.getenv("MAX_POSITIONS_PER_SYMBOL", "1")),
    "max_trades_per_hour": int(os.getenv("MAX_TRADES_PER_HOUR", "3")),
    "position_size_pct": float(os.getenv("POSITION_SIZE_PCT", "0.15")),
    "stop_loss_pct": float(os.getenv("STOP_LOSS_PCT", "0.025")),
    "take_profit_pct": float(os.getenv("TAKE_PROFIT_PCT", "0.04")),
    "commission_pct": float(os.getenv("COMMISSION_PCT", "0.001")),
    "slippage_pct": float(os.getenv("SLIPPAGE_PCT", "0.0005")),
}

SCALPING_CONFIG = {
    "timeframe": os.getenv("SCALP_TIMEFRAME", "5m"),
    "rsi_period": int(os.getenv("SCALP_RSI_PERIOD", "7")),
    "rsi_oversold": float(os.getenv("SCALP_RSI_OVERSOLD", "30.0")),
    "rsi_overbought": float(os.getenv("SCALP_RSI_OVERBOUGHT", "70.0")),
    "rsi_exit_long": float(os.getenv("SCALP_RSI_EXIT_LONG", "60.0")),
    "rsi_exit_short": float(os.getenv("SCALP_RSI_EXIT_SHORT", "40.0")),
    "use_ema_filter": os.getenv("SCALP_USE_EMA_FILTER", "true").lower() == "true",
    "ema_period": int(os.getenv("SCALP_EMA_PERIOD", "50")),
    "use_atr_filter": os.getenv("SCALP_USE_ATR_FILTER", "true").lower() == "true",
    "atr_period": int(os.getenv("SCALP_ATR_PERIOD", "14")),
    "atr_min_threshold_pct": float(os.getenv("SCALP_ATR_MIN_PCT", "0.003")),
    "vwap_warmup_bars": int(os.getenv("SCALP_VWAP_WARMUP", "12")),
    "cooldown_bars": int(os.getenv("SCALP_COOLDOWN_BARS", "3")),
    "max_open_positions": int(os.getenv("SCALP_MAX_OPEN_POS", "3")),
    "max_positions_per_symbol": int(os.getenv("SCALP_MAX_POS_SYM", "1")),
    "max_trades_per_day": int(os.getenv("SCALP_MAX_TRADES_DAY", "10")),
    "max_trades_per_hour": int(os.getenv("SCALP_MAX_TRADES_HOUR", "5")),
    "position_size_pct": float(os.getenv("SCALP_POS_SIZE_PCT", "0.10")),
    "stop_loss_pct": float(os.getenv("SCALP_SL_PCT", "0.012")),
    "take_profit_pct": float(os.getenv("SCALP_TP_PCT", "0.024")),
    "commission_pct": float(os.getenv("SCALP_COMMISSION_PCT", "0.001")),
    "slippage_pct": float(os.getenv("SCALP_SLIPPAGE_PCT", "0.0005")),
}
