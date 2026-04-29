"""
Intraday Paper Trader — rsi_reversion / 15m

Her 15 dakikada bir çalıştırılır.
Durum live/intraday_state.json dosyasında ayrı saklanır.
Breakout trader ile aynı kodu paylaşmaz; bağımsız bakiye ve izleme.

Kullanım:
    python -m live.intraday_trader           # sinyal kontrol + işlem
    python -m live.intraday_trader --status  # sadece durum göster
"""

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd

from config.settings import RSI_REVERSION_CONFIG
from core.fast_exit import evaluate_exit, evaluate_position_scaling
from core.signal_score import score_signal, score_signal_short
from core.alerts import send_alert
from core.market_regime import apply_regime_filter, determine_market_regime
from live.strategy_router import select_strategies, get_strategy_allocations, blend_allocations, apply_kill_switch
from live.strategy_performance import compute_strategy_performance, compute_symbol_performance, get_blocked_symbols
from core.symbol_universe import filter_valid_symbols, get_symbols
from indicators.feature_engine import compute_feature_snapshot
from live.event_logger import EventLogger, capture_exception, write_dashboard_summary
from live.risk_controls import (
    SizingResult,
    allocated_usdt,
    check_daily_loss_guard,
    compute_position_size,
    unrealized_pnl,
)
from strategy.momentum_pullback_v1 import MomentumPullbackV1Strategy
from strategy.short_momentum_v1 import ShortMomentumV1Strategy
from strategy.trend_pullback_v1 import TrendPullbackV1Strategy, TREND_PULLBACK_SCORE_MIN
from strategy.rsi_reversion import EMA_TREND_PERIOD, RsiReversionStrategy
from strategy.signals import Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Sabitler ──────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL_MODE      = "core"
TIMEFRAME                = "15m"
LOOKBACK_BARS            = 250
MAX_CONCURRENT_POSITIONS = 3
MAX_POSITIONS_PER_SYMBOL = 1
RISK_PCT                 = 0.010   # breakout'un yarısı: %1.0
RISK_PER_TRADE_PCT       = 0.01
MAX_POSITION_PCT          = 0.20   # single position cannot exceed 20% of equity
TAKE_PROFIT_PCT          = RSI_REVERSION_CONFIG["take_profit_pct"]
STOP_LOSS_PCT            = RSI_REVERSION_CONFIG["stop_loss_pct"]
COOLDOWN_MINUTES         = RSI_REVERSION_CONFIG["cooldown_minutes"]
INITIAL_CASH             = 10_000.0
SCORE_MIN_TO_TRADE       = 60
SCORE_MIN_TO_TRADE_MOMENTUM = 45
INTRADAY_STRATEGY_MODE   = "auto"

# ── Post-exit cooldown per symbol ────────────────────────────────────────────
COOLDOWN_AFTER_STOP_LOSS_MINUTES      = 180
COOLDOWN_AFTER_MOMENTUM_DECAY_MINUTES = 120
COOLDOWN_AFTER_SOFT_PROFIT_MINUTES    = 60
COOLDOWN_AFTER_TAKE_PROFIT_MINUTES    = 30

# Fast exit config
PARTIAL_TP_ENABLED       = True
PARTIAL_TP_PCT           = 0.012
PARTIAL_TP_SIZE          = 0.50

TRAILING_ENABLED         = True
TRAILING_ACTIVATE_PCT    = 0.04
TRAILING_STOP_PCT        = 0.015

MAX_HOLDING_BARS_ENABLED = False
MAX_HOLDING_BARS         = 96

TF_MINUTES               = 15

# ── Position scaling config ──────────────────────────────────────────────────
SCALING_ENABLED           = True
SCALING_MIN_PNL_PCT       = 0.50
SCALING_ADD_PCT           = 0.10
SCALING_MAX_COUNT         = 2
SCALING_MIN_DISTANCE_PCT  = 0.30
MAX_TOTAL_EXPOSURE_PCT    = 0.80
MAX_SYMBOL_EXPOSURE_PCT   = 0.30

# ── Liquidity filter (dynamic mode) ──────────────────────────────────────────
LIQUIDITY_MIN_VOLUME_SMA  = 0       # coin-denominated; just check > 0
LIQUIDITY_MIN_ATR_PCT     = 0.10
LIQUIDITY_MAX_STALE_MIN   = 30.0

STATE_FILE = Path(__file__).parent / "intraday_state.json"


# ── State yönetimi ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return _fresh_state()


def _fresh_state() -> dict:
    return {
        "cash":                INITIAL_CASH,
        "peak_equity":         INITIAL_CASH,
        "positions":           {},
        "sym_wins":            {},
        "sym_losses":          {},
        "sym_pnl":             {},
        "sym_last_trade":      {},
        "daily_date":          "",
        "daily_start_equity":  INITIAL_CASH,
        "daily_pnl":           0.0,
        "daily_realized_loss": 0.0,
        "trade_log":           [],
        "equity_log":          [],
        "last_exit_by_symbol": {},
    }


def _reset_state() -> None:
    state = _fresh_state()
    _save_state(state)
    print(f"  State reset → cash=${INITIAL_CASH:,.2f} | positions=0 | trades cleared")
    print(f"  State file: {STATE_FILE}")


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _fetch_candles(symbol: str, exchange: ccxt.Exchange) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LOOKBACK_BARS)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df.iloc[:-1].reset_index(drop=True)   # son mum henüz kapanmamış


def _total_equity(state: dict, prices: dict[str, float]) -> float:
    eq = state["cash"]
    for sym, pos in state["positions"].items():
        current = prices.get(sym, pos["entry_price"])
        if pos.get("side") == "short":
            # margin + unrealized PnL
            eq += pos["quantity"] * pos["entry_price"]
            eq += (pos["entry_price"] - current) * pos["quantity"]
        else:
            eq += pos["quantity"] * current
    return eq


def _cooldown_ok(state: dict, symbol: str, now: datetime) -> bool:
    last = state["sym_last_trade"].get(symbol)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        return (now - last_dt) >= timedelta(minutes=COOLDOWN_MINUTES)
    except Exception:
        return True


def _exit_cooldown_minutes_for_reason(reason: str) -> int:
    """Map exit reason to cooldown duration in minutes."""
    r = reason.lower()
    if "stop_loss" in r:
        return COOLDOWN_AFTER_STOP_LOSS_MINUTES
    if "momentum_decay" in r:
        return COOLDOWN_AFTER_MOMENTUM_DECAY_MINUTES
    if "soft_profit_protect" in r:
        return COOLDOWN_AFTER_SOFT_PROFIT_MINUTES
    if "take_profit" in r or "trailing" in r:
        return COOLDOWN_AFTER_TAKE_PROFIT_MINUTES
    return 0


def _record_exit_cooldown(state: dict, symbol: str, reason: str, now: datetime) -> None:
    """Store per-symbol cooldown after an exit."""
    minutes = _exit_cooldown_minutes_for_reason(reason)
    if minutes <= 0:
        return
    cooldown_until = (now + timedelta(minutes=minutes)).isoformat()
    state.setdefault("last_exit_by_symbol", {})[symbol] = {
        "ts": now.isoformat(),
        "reason": reason,
        "cooldown_until": cooldown_until,
    }
    logger.info("POST-EXIT COOLDOWN | %s | %s → %d min (until %s)",
                symbol, reason, minutes, cooldown_until[:19])


def _check_exit_cooldown(state: dict, symbol: str, now: datetime) -> tuple[bool, dict]:
    """Check if symbol is in post-exit cooldown. Returns (blocked, info_dict)."""
    info = state.get("last_exit_by_symbol", {}).get(symbol)
    if not info:
        return False, {}
    try:
        cooldown_until = datetime.fromisoformat(info["cooldown_until"])
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
        if now < cooldown_until:
            return True, {
                "cooldown_until": info["cooldown_until"],
                "last_exit_reason": info.get("reason", ""),
            }
    except (ValueError, KeyError):
        pass
    return False, {}


# ── Pozisyon aç/kapa ──────────────────────────────────────────────────────────

def _open_position(
    state: dict,
    symbol: str,
    price: float,
    equity: float,
    now_str: str,
    evt: EventLogger | None = None,
    strategy_name: str = "rsi_reversion",
    side: str = "long",
) -> None:
    alloc  = allocated_usdt(state["positions"])
    sizing = compute_position_size(
        equity=equity,
        cash=state["cash"],
        price=price,
        current_allocated_usdt=alloc,
        stop_loss_pct=STOP_LOSS_PCT,
        risk_pct=RISK_PCT,
    )
    if not sizing.allowed:
        logger.warning("OPEN REDDEDİLDİ | %s | %s", symbol, sizing.reason)
        if evt:
            evt.log_scan(symbol, "candidate_rejected",
                         reject_reason="max_risk_exceeded:" + sizing.reason,
                         checks={"sizing_allowed": False})
        return

    # Hard cap: position size cannot exceed MAX_POSITION_PCT of equity
    max_size = equity * MAX_POSITION_PCT
    if sizing.size_usdt > max_size:
        capped_qty = max_size / price
        logger.info(
            "SIZE CAPPED | %s | risk_size=%.2f → cap=%.2f (%.0f%% equity) | qty=%.6f → %.6f",
            symbol, sizing.size_usdt, max_size, MAX_POSITION_PCT * 100,
            sizing.quantity, capped_qty,
        )
        if evt:
            evt.log_scan(symbol, "size_capped_by_max_position_pct",
                         checks={"risk_size": round(sizing.size_usdt, 2),
                                 "capped_size": round(max_size, 2),
                                 "max_pct": MAX_POSITION_PCT})
        sizing = SizingResult(allowed=True, quantity=capped_qty,
                              size_usdt=max_size, reason="capped")

    cost = sizing.quantity * price
    state["cash"] -= cost

    is_short = side == "short"
    if is_short:
        tp_price = price * (1 - TAKE_PROFIT_PCT)
        sl_price = price * (1 + STOP_LOSS_PCT)
    else:
        tp_price = price * (1 + TAKE_PROFIT_PCT)
        sl_price = price * (1 - STOP_LOSS_PCT)

    state["positions"][symbol] = {
        "entry_price": price,
        "quantity":    sizing.quantity,
        "size_usdt":   sizing.size_usdt,
        "tp":          tp_price,
        "sl":          sl_price,
        "entry_time":  now_str,
        "strategy":    strategy_name,
        "side":        "short" if is_short else "long",
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
    }
    state["sym_last_trade"][symbol] = now_str

    logger.info(
        "OPEN  | %s | price=%.4f | qty=%.6f | size=%.2f USDT | risk≈%.2f USDT",
        symbol, price, sizing.quantity, sizing.size_usdt,
        sizing.size_usdt * STOP_LOSS_PCT,
    )
    state["trade_log"].append({
        "time": now_str, "symbol": symbol, "action": "OPEN",
        "price": price, "quantity": sizing.quantity, "size_usdt": sizing.size_usdt,
        "strategy": strategy_name,
        "side": "short" if is_short else "long",
        "entry_price": price,
        "tp": tp_price,
        "sl": sl_price,
    })

    send_alert("SHORT_OPENED" if is_short else "TRADE_OPENED",
               {"symbol": symbol, "strategy": strategy_name,
                "action": "SHORT_OPEN" if is_short else "OPEN",
                "price": price, "size_usdt": sizing.size_usdt,
                "side": "short" if is_short else "long"})


def _close_position(
    state: dict, symbol: str, price: float, now_str: str, reason: str
) -> float:
    pos = state["positions"].pop(symbol, None)
    if pos is None:
        return 0.0

    is_short = pos.get("side") == "short"
    proceeds = pos["quantity"] * price
    if is_short:
        pnl = pos["quantity"] * pos["entry_price"] - proceeds
        # Return margin + realized PnL
        state["cash"] += pos["quantity"] * pos["entry_price"] + pnl
    else:
        pnl = proceeds - pos["quantity"] * pos["entry_price"]
        state["cash"] += proceeds
    state["daily_pnl"]           = state.get("daily_pnl", 0.0) + pnl
    if pnl < 0:
        state["daily_realized_loss"] = state.get("daily_realized_loss", 0.0) + abs(pnl)

    if pnl > 0:
        state["sym_wins"][symbol]   = state["sym_wins"].get(symbol, 0) + 1
    else:
        state["sym_losses"][symbol] = state["sym_losses"].get(symbol, 0) + 1
    state["sym_pnl"][symbol] = state["sym_pnl"].get(symbol, 0.0) + pnl

    entry_strategy = pos.get("strategy", "rsi_reversion")
    entry_price = pos["entry_price"]
    if entry_price > 0:
        pnl_pct = (entry_price - price) / entry_price * 100 if is_short else (price - entry_price) / entry_price * 100
    else:
        pnl_pct = 0.0
    holding_min = 0.0
    try:
        entry_dt = datetime.fromisoformat(pos.get("entry_time", now_str))
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        holding_min = (datetime.fromisoformat(now_str) - entry_dt).total_seconds() / 60.0
    except Exception:
        pass

    logger.info("CLOSE | %s | price=%.4f | pnl=%.2f | reason=%s | strategy=%s",
                symbol, price, pnl, reason, entry_strategy)
    state["trade_log"].append({
        "time": now_str, "symbol": symbol, "action": "CLOSE",
        "price": price, "pnl": round(pnl, 4), "reason": reason,
        "strategy": entry_strategy,
        "entry_strategy": entry_strategy,
        "exit_runner": "intraday_trader",
        "entry_price": entry_price,
        "pnl_pct": round(pnl_pct, 4),
        "holding_minutes": round(holding_min, 1),
        "side": "short" if is_short else "long",
    })

    # Record post-exit cooldown
    try:
        now_dt = datetime.fromisoformat(now_str)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        _record_exit_cooldown(state, symbol, reason, now_dt)
    except Exception:
        pass

    reason_lower = reason.lower()
    short_prefix = "SHORT_" if is_short else ""
    if "stop_loss" in reason_lower:
        alert_type = f"{short_prefix}STOP_LOSS"
    elif "early_momentum_failure" in reason_lower:
        alert_type = "EARLY_MOMENTUM_FAILURE"
    elif "momentum_decay" in reason_lower:
        alert_type = f"{short_prefix}MOMENTUM_DECAY_EXIT"
    elif "soft_profit_protect" in reason_lower:
        alert_type = f"{short_prefix}SOFT_PROFIT_PROTECT"
    elif "take_profit" in reason_lower or "partial_take_profit" in reason_lower:
        alert_type = f"{short_prefix}TAKE_PROFIT"
    elif "trailing" in reason_lower:
        alert_type = f"{short_prefix}TRAILING_STOP"
    else:
        alert_type = "SHORT_CLOSED" if is_short else "TRADE_CLOSED"
    send_alert(alert_type, {"symbol": symbol, "strategy": entry_strategy,
               "action": "CLOSE", "price": price, "pnl": round(pnl, 4),
               "pnl_pct": round(pnl_pct, 4), "reason": reason})
    return pnl


def _partial_close_position(
    state: dict,
    symbol: str,
    price: float,
    exit_pct: float,
    now_str: str,
    reason: str,
) -> float:
    """Close a fraction of a position, keeping remainder open."""
    pos = state["positions"].get(symbol)
    if pos is None:
        return 0.0

    is_short = pos.get("side") == "short"
    close_qty = pos["quantity"] * exit_pct
    proceeds  = close_qty * price
    if is_short:
        pnl = close_qty * pos["entry_price"] - proceeds
        # Return proportional margin + PnL
        state["cash"] += close_qty * pos["entry_price"] + pnl
    else:
        pnl = proceeds - close_qty * pos["entry_price"]
        state["cash"] += proceeds
    state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl
    if pnl < 0:
        state["daily_realized_loss"] = state.get("daily_realized_loss", 0.0) + abs(pnl)

    # Update position in-place
    remaining_qty = pos["quantity"] - close_qty
    if remaining_qty <= 0:
        return _close_position(state, symbol, price, now_str, reason)

    pos["quantity"]  = remaining_qty
    pos["size_usdt"] = remaining_qty * pos["entry_price"]
    pos["partial_tp_done"] = True

    if pnl > 0:
        state["sym_wins"][symbol] = state["sym_wins"].get(symbol, 0) + 1
    else:
        state["sym_losses"][symbol] = state["sym_losses"].get(symbol, 0) + 1
    state["sym_pnl"][symbol] = state["sym_pnl"].get(symbol, 0.0) + pnl

    logger.info(
        "PARTIAL CLOSE | %s | price=%.4f | qty=%.6f (%.0f%%) | pnl=%.2f | reason=%s",
        symbol, price, close_qty, exit_pct * 100, pnl, reason,
    )
    state["trade_log"].append({
        "time": now_str, "symbol": symbol, "action": "PARTIAL_CLOSE",
        "price": price, "quantity": round(close_qty, 8),
        "pnl": round(pnl, 4), "exit_pct": exit_pct,
        "reason": reason, "strategy": pos.get("strategy", "rsi_reversion"),
        "side": "short" if is_short else "long",
    })
    send_alert("PARTIAL_CLOSE", {"symbol": symbol,
               "strategy": pos.get("strategy", "rsi_reversion"),
               "action": "PARTIAL_CLOSE", "price": price, "pnl": round(pnl, 4),
               "reason": reason})
    return pnl


def _scale_position(
    state: dict,
    symbol: str,
    price: float,
    equity: float,
    add_size_usdt: float,
    now_str: str,
    evt: EventLogger | None = None,
) -> None:
    """Add to an existing winning position."""
    pos = state["positions"].get(symbol)
    if pos is None:
        return

    # Cash check
    if state["cash"] < add_size_usdt:
        logger.warning("SCALE SKIP | %s | insufficient cash (%.2f < %.2f)",
                        symbol, state["cash"], add_size_usdt)
        return

    add_qty = add_size_usdt / price
    old_qty = pos["quantity"]
    old_entry = pos["entry_price"]
    old_size = pos["size_usdt"]

    # Update position: weighted average entry price
    new_qty = old_qty + add_qty
    new_entry = (old_entry * old_qty + price * add_qty) / new_qty
    new_size = old_size + add_size_usdt

    pos["entry_price"] = new_entry
    pos["quantity"] = new_qty
    pos["size_usdt"] = new_size
    pos["scale_count"] = pos.get("scale_count", 0) + 1
    pos["last_scale_price"] = price
    # Recalculate TP/SL from new average entry
    pos["tp"] = new_entry * (1 + TAKE_PROFIT_PCT)
    pos["sl"] = new_entry * (1 - STOP_LOSS_PCT)

    state["cash"] -= add_size_usdt

    logger.info(
        "SCALE IN | %s | price=%.4f | added=%.2f USDT | new_total=%.2f USDT | count=%d | avg_entry=%.4f",
        symbol, price, add_size_usdt, new_size, pos["scale_count"], new_entry,
    )
    state["trade_log"].append({
        "time": now_str, "symbol": symbol, "action": "SCALE_IN",
        "price": price, "quantity": add_qty, "size_usdt": add_size_usdt,
        "new_total_size": new_size, "scale_count": pos["scale_count"],
        "avg_entry_price": new_entry,
        "strategy": pos.get("strategy", "rsi_reversion"),
    })

    evt and evt.log_scan(symbol, "scale_in",
                         price=round(price, 4),
                         added_size_usdt=round(add_size_usdt, 2),
                         new_total_size=round(new_size, 2),
                         scale_count=pos["scale_count"],
                         checks={"pnl_before_scale": round((price - old_entry) / old_entry * 100, 4)})

    send_alert("SCALE_IN", {"symbol": symbol,
               "strategy": pos.get("strategy", "rsi_reversion"),
               "action": "SCALE_IN", "price": price,
               "size_usdt": round(add_size_usdt, 2),
               "reason": f"count={pos['scale_count']}"})


# ── Günlük sıfırlama ──────────────────────────────────────────────────────────

def _maybe_reset_daily(state: dict, today: str, prices: dict[str, float]) -> None:
    if state.get("daily_date") != today:
        eq = _total_equity(state, prices)
        state["daily_date"]          = today
        state["daily_start_equity"]  = eq
        state["daily_pnl"]           = 0.0
        state["daily_realized_loss"] = 0.0
        logger.info("Günlük sıfırlandı | %s | equity=%.2f", today, eq)


# ── Ana döngü ─────────────────────────────────────────────────────────────────

def run(status_only: bool = False, strategy_mode: str = INTRADAY_STRATEGY_MODE,
        dry_run: bool = False, symbol_mode: str = DEFAULT_SYMBOL_MODE) -> None:
    valid_modes = ("auto", "rsi_reversion", "momentum_pullback_v1", "short_momentum_v1", "trend_pullback_v1")
    if strategy_mode not in valid_modes:
        raise ValueError(f"Unknown strategy_mode: {strategy_mode!r}. Use: {valid_modes}")
    if symbol_mode not in ("core", "expanded", "dynamic"):
        raise ValueError(f"Unknown symbol_mode: {symbol_mode!r}. Use: 'core', 'expanded', 'dynamic'")

    active_strategies: list[str] = []
    is_auto = strategy_mode == "auto"
    if not is_auto:
        active_strategies = [strategy_mode]

    # Resolve symbols via universe module
    symbols_requested = get_symbols(symbol_mode)
    symbols_valid, symbols_rejected, symbols_warnings = filter_valid_symbols(symbols_requested)
    APPROVED_SYMBOLS = symbols_valid

    logger.info(
        "SYMBOL UNIVERSE | mode=%s | requested=%d | valid=%d | rejected=%d | warnings=%d",
        symbol_mode, len(symbols_requested), len(symbols_valid),
        len(symbols_rejected), len(symbols_warnings),
    )

    evt      = EventLogger(strategy_mode if is_auto else strategy_mode)
    state    = _load_state()
    exchange = ccxt.binance({"enableRateLimit": True})
    now      = datetime.now(timezone.utc)
    now_str  = now.isoformat()
    today    = now.strftime("%Y-%m-%d")

    prices:  dict[str, float]        = {}
    candles: dict[str, pd.DataFrame] = {}
    features_map: dict[str, dict]    = {}
    scores_map:   dict[str, dict]    = {}

    evt.log_run("started",
                symbol_mode=symbol_mode,
                symbols_requested=symbols_requested,
                symbols_validated=symbols_valid,
                symbols_rejected=symbols_rejected)

    try:
        symbols_blocked_liquidity: list[str] = []
        for symbol in APPROVED_SYMBOLS:
            try:
                df = _fetch_candles(symbol, exchange)

                # Liquidity filter (applies to all modes, strictest for dynamic)
                if len(df) < 2:
                    symbols_blocked_liquidity.append(symbol)
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="symbol_not_available",
                                 checks={"bars_loaded": len(df)})
                    continue

                last_ts = df["timestamp"].iloc[-1]
                if hasattr(last_ts, "tzinfo") and last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                stale_min = (now - last_ts).total_seconds() / 60.0
                if stale_min > LIQUIDITY_MAX_STALE_MIN:
                    symbols_blocked_liquidity.append(symbol)
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="stale_market_data",
                                 checks={"stale_minutes": round(stale_min, 1),
                                         "max_allowed": LIQUIDITY_MAX_STALE_MIN})
                    continue

                feat = compute_feature_snapshot(df, symbol=symbol, timeframe=TIMEFRAME)
                score = score_signal(feat)

                # Volume and ATR liquidity check
                vol_sma = feat.get("volume_sma")
                atr_pct = feat.get("atr_pct")
                if vol_sma is not None and vol_sma <= LIQUIDITY_MIN_VOLUME_SMA:
                    symbols_blocked_liquidity.append(symbol)
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="liquidity_too_low",
                                 checks={"volume_sma": round(vol_sma, 2),
                                         "min_required": "positive"})
                    continue
                if atr_pct is not None and atr_pct < LIQUIDITY_MIN_ATR_PCT:
                    symbols_blocked_liquidity.append(symbol)
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="volatility_too_low",
                                 checks={"atr_pct": round(atr_pct, 4),
                                         "min_required": LIQUIDITY_MIN_ATR_PCT})
                    continue

                prices[symbol]  = float(df["close"].iloc[-1])
                candles[symbol] = df
                features_map[symbol] = feat
                scores_map[symbol]   = score

                evt.log_scan(symbol, "scanned",
                             bars_loaded=len(df),
                             last_candle_time=str(df["timestamp"].iloc[-1]),
                             strategy_mode=strategy_mode,
                             symbol_mode=symbol_mode,
                             features=feat,
                             score_decision=score)
            except Exception as exc:
                logger.warning("Veri çekme hatası | %s | %s", symbol, exc)
                capture_exception(evt, context=f"fetch_candles:{symbol}")

        # Determine market regime from BTC/ETH features
        market_regime = determine_market_regime(features_map)

        logger.info(
            "MARKET REGIME | %s (conf=%d) | symbols_scanned=%d | liquidity_blocked=%d",
            market_regime["regime"], market_regime["confidence"],
            len(candles), len(symbols_blocked_liquidity),
        )

        _maybe_reset_daily(state, today, prices)

        equity = _total_equity(state, prices)
        if equity > state["peak_equity"]:
            state["peak_equity"] = equity

        if status_only:
            _print_status(state, prices, equity, APPROVED_SYMBOLS,
                          strategy_mode=strategy_mode,
                          active_strategies=active_strategies if is_auto else [strategy_mode])
            return

        # ── Resolve active strategies ─────────────────────────────────────────
        if is_auto:
            active_strategies = select_strategies(market_regime["regime"])
        # else: active_strategies already set to [strategy_mode]

        # ── Strategy capital allocation (adaptive + kill switch) ──────────────────
        base_allocs = get_strategy_allocations(
            active_strategies, market_regime["regime"], strategy_mode)
        strategy_perf = compute_strategy_performance(state.get("trade_log", []))
        symbol_perf = compute_symbol_performance(state.get("trade_log", []))
        blocked_symbols = get_blocked_symbols(symbol_perf)
        if blocked_symbols:
            total_blocked = sum(len(v) for v in blocked_symbols.values())
            logger.info("SYMBOL PERFORMANCE FILTER | %d symbol(s) blocked across %d strategy(ies)",
                        total_blocked, len(blocked_symbols))
        blended = blend_allocations(base_allocs, strategy_perf)
        strategy_allocs, kill_switch_status = apply_kill_switch(blended, strategy_perf)
        logger.info("STRATEGY ALLOCATION | active=%s | allocations=%s",
                    active_strategies, strategy_allocs)

        # ── Açık pozisyonlar: Fast Exit Engine ───────────────────────────────────
        for symbol in list(state["positions"].keys()):
            if symbol not in prices:
                continue
            price = prices[symbol]
            pos   = state["positions"][symbol]

            # Get latest features for momentum-aware exits
            pos_features = features_map.get(symbol)

            decision, pos_updates = evaluate_exit(
                pos, price, now, TF_MINUTES,
                features=pos_features,
                partial_tp_enabled=PARTIAL_TP_ENABLED,
                partial_tp_pct=PARTIAL_TP_PCT,
                partial_tp_size=PARTIAL_TP_SIZE,
                trailing_enabled=TRAILING_ENABLED,
                trailing_activate_pct=TRAILING_ACTIVATE_PCT,
                trailing_stop_pct=TRAILING_STOP_PCT,
                max_holding_bars_enabled=MAX_HOLDING_BARS_ENABLED,
                max_holding_bars=MAX_HOLDING_BARS,
            )

            # Apply trailing state updates even if no exit
            for k, v in pos_updates.items():
                pos[k] = v

            tl_before = len(state["trade_log"])

            if decision.should_exit and decision.exit_type == "partial_take_profit":
                _partial_close_position(
                    state, symbol, price, decision.exit_pct, now_str, decision.reason)
            elif decision.should_exit:
                _close_position(state, symbol, price, now_str, decision.reason)

            if len(state["trade_log"]) > tl_before:
                e = state["trade_log"][-1]
                evt.log_trade(symbol, e["action"],
                              price=e.get("price"), pnl=e.get("pnl"),
                              quantity=e.get("quantity"),
                              exit_pct=e.get("exit_pct"),
                              reason=e.get("reason"), strategy=e.get("strategy", strategy_mode))

            evt.log_scan(symbol, "exit_check",
                         exit_type=decision.exit_type,
                         should_exit=decision.should_exit,
                         price=round(price, 4),
                         checks=decision.checks)

            # ── Position scaling (only if not exited and not dry-run) ──────────
            if not decision.should_exit and not dry_run and symbol in state["positions"]:
                alloc = allocated_usdt(state["positions"])
                scale_eval = evaluate_position_scaling(
                    position=state["positions"][symbol],
                    price=price,
                    features=pos_features,
                    equity=equity,
                    total_allocated_usdt=alloc,
                )
                if scale_eval["should_scale"]:
                    _scale_position(
                        state, symbol, price, equity,
                        scale_eval["scale_size_usdt"], now_str, evt=evt,
                    )

        equity = _total_equity(state, prices)

        # ── Günlük kayıp koruması ─────────────────────────────────────────────────
        u_pnl = unrealized_pnl(state["positions"], prices)
        guard = check_daily_loss_guard(
            daily_start_equity  = state["daily_start_equity"],
            daily_realized_loss = state.get("daily_realized_loss", 0.0),
            unrealized_pnl      = u_pnl,
        )
        if guard.blocked:
            logger.warning("GÜNLÜK KORUMA AKTİF | %s", guard.reason)

        # ── Yeni sinyal tara (per strategy) ──────────────────────────────────────
        open_count = len(state["positions"])

        for sm in active_strategies:
            # Instantiate strategy for this loop iteration
            if sm == "rsi_reversion":
                strategy = RsiReversionStrategy()
            elif sm == "short_momentum_v1":
                strategy = ShortMomentumV1Strategy()
            elif sm == "trend_pullback_v1":
                strategy = TrendPullbackV1Strategy()
            else:
                strategy = MomentumPullbackV1Strategy()

            sm_is_short = sm == "short_momentum_v1"

            # Re-score with short-aware model for short_momentum_v1
            if sm_is_short:
                for sym in list(scores_map.keys()):
                    scores_map[sym] = score_signal_short(
                        features_map[sym], market_regime=market_regime)

            logger.info("STRATEGY SCAN | %s | symbols=%d", sm, len(APPROVED_SYMBOLS))

            for symbol in APPROVED_SYMBOLS:
                if symbol not in candles:
                    continue
                _feat  = features_map.get(symbol, {})
                _score = scores_map.get(symbol, {})

                if symbol in state["positions"]:
                    existing_sm = state["positions"][symbol].get("strategy", "unknown")
                    logger.info("SIGNAL CONFLICT | %s | existing=%s | new=%s",
                                symbol, existing_sm, sm)
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="signal_conflict",
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 features=_feat, score_decision=_score,
                                 checks={"position_open": True,
                                         "existing_strategy": existing_sm,
                                         "new_strategy": sm})
                    continue
                # Post-exit cooldown check
                blocked, cooldown_info = _check_exit_cooldown(state, symbol, now)
                if blocked:
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="post_exit_cooldown",
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 features=_feat, score_decision=_score,
                                 checks={"cooldown_until": cooldown_info.get("cooldown_until"),
                                         "last_exit_reason": cooldown_info.get("last_exit_reason")})
                    send_alert("TRADE_BLOCKED_COOLDOWN", {"symbol": symbol,
                               "reason": f"post_exit_cooldown: {cooldown_info.get('last_exit_reason', '')}"})
                    continue
                if open_count >= MAX_CONCURRENT_POSITIONS:
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="position_limit_reached",
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 features=_feat, score_decision=_score,
                                 checks={"open_positions": open_count, "max_concurrent": MAX_CONCURRENT_POSITIONS})
                    break
                if guard.blocked:
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="daily_loss_guard",
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 features=_feat, score_decision=_score,
                                 checks={"daily_loss_guard": False})
                    break
                if not _cooldown_ok(state, symbol, now):
                    evt.log_scan(symbol, "candidate_rejected",
                                 reject_reason="cooldown_active",
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 features=_feat, score_decision=_score,
                                 checks={"cooldown_ok": False},
                                 cooldown_minutes=COOLDOWN_MINUTES)
                    continue

                # Regime filter
                if sm == "momentum_pullback_v1":
                    regime_allowed, regime_reason = apply_regime_filter(
                        market_regime, symbol, _feat, _score.get("score", 0))
                    if not regime_allowed:
                        evt.log_scan(symbol, "candidate_rejected",
                                     reject_reason=regime_reason,
                                     timeframe=TIMEFRAME, strategy_mode=sm,
                                     features=_feat, score_decision=_score,
                                     checks={"market_regime": market_regime["regime"],
                                             "regime_confidence": market_regime["confidence"]})
                        continue
                elif sm == "short_momentum_v1":
                    if market_regime.get("regime") != "bearish":
                        evt.log_scan(symbol, "candidate_rejected",
                                     reject_reason="regime_not_bearish",
                                     timeframe=TIMEFRAME, strategy_mode=sm,
                                     features=_feat, score_decision=_score,
                                     checks={"market_regime": market_regime.get("regime"),
                                             "regime_confidence": market_regime.get("confidence")})
                        continue
                # trend_pullback_v1: no external regime filter — strategy handles it internally

                bars_loaded = len(candles[symbol])

                # Strategy dispatch
                if sm == "momentum_pullback_v1":
                    signal = strategy.generate_signal(
                        symbol=symbol, features=_feat, score_decision=_score)
                elif sm == "short_momentum_v1":
                    signal = strategy.generate_signal(
                        symbol=symbol, features=_feat, score_decision=_score)
                elif sm == "trend_pullback_v1":
                    signal = strategy.generate_signal(
                        symbol=symbol, features=_feat, score_decision=_score,
                        market_regime=market_regime)
                else:
                    min_bars_required = max(
                        EMA_TREND_PERIOD + 1,
                        RSI_REVERSION_CONFIG.get("ema_slow_period", 50) + 1,
                        RSI_REVERSION_CONFIG.get("rsi_period", 7) + 1,
                    )
                    try:
                        signal = strategy.generate_signal(candles[symbol])
                    except ValueError as exc:
                        logger.debug("Sinyal hatası | %s | %s", symbol, exc)
                        evt.log_scan(symbol, "candidate_rejected",
                                     reject_reason="insufficient_bars",
                                     bars_loaded=bars_loaded, min_bars_required=min_bars_required,
                                     fetch_limit=LOOKBACK_BARS,
                                     timeframe=TIMEFRAME, strategy_mode=sm,
                                     features=_feat, score_decision=_score,
                                     checks={"bars_ok": False})
                        continue

                if signal in (Signal.BUY, Signal.SELL, Signal.SHORT):
                    is_short_signal = signal in (Signal.SELL, Signal.SHORT)
                    trade_side = "short" if is_short_signal else "long"

                    # Score check — skip for trend_pullback_v1 (uses internal scoring)
                    if sm != "trend_pullback_v1":
                        score_val = _score.get("score", 0)
                        score_threshold = (SCORE_MIN_TO_TRADE_MOMENTUM
                                           if sm == "momentum_pullback_v1"
                                           else SCORE_MIN_TO_TRADE)
                        if score_val < score_threshold:
                            evt.log_scan(symbol, "candidate_rejected",
                                         reject_reason="score_below_trade_threshold",
                                         bars_loaded=bars_loaded,
                                         timeframe=TIMEFRAME, strategy_mode=sm,
                                         features=_feat, score_decision=_score,
                                         score_used_for_trade=False,
                                         score_threshold_used=score_threshold,
                                         checks={"strategy_signal": signal.value,
                                                 "score": score_val,
                                                 "threshold": score_threshold})
                            logger.info("SCORE BLOCK | %s | score=%d < threshold=%d (%s)",
                                        symbol, score_val, score_threshold, sm)
                            continue
                    else:
                        score_val = getattr(strategy, "last_score", 0)
                        score_threshold = TREND_PULLBACK_SCORE_MIN

                    signal_label = "SHORT" if is_short_signal else "BUY"
                    # Include strategy's internal decision for trend_pullback_v1
                    tp_decision = getattr(strategy, "last_decision", {})

                    evt.log_scan(symbol, "signal_accepted",
                                 bars_loaded=bars_loaded,
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 side=trade_side,
                                 market_regime=market_regime,
                                 features=_feat, score_decision=_score,
                                 score_used_for_trade=True,
                                 score_threshold_used=score_threshold,
                                 decision=tp_decision if sm == "trend_pullback_v1" else None,
                                 checks={"strategy_signal": signal_label,
                                         "score": score_val,
                                         "threshold": score_threshold,
                                         "side": trade_side})

                    if dry_run:
                        logger.info("DRY-RUN | %s | would %s @ %.4f (skipped)",
                                    symbol, signal_label, prices[symbol])
                        open_count += 1
                        continue

                    # Allocation check
                    alloc_limit = strategy_allocs.get(sm, 0.0) * equity
                    alloc_used = sum(
                        p["entry_price"] * p["quantity"]
                        for p in state["positions"].values()
                        if p.get("strategy") == sm
                    )
                    if alloc_used >= alloc_limit:
                        logger.info(
                            "ALLOCATION BLOCK | %s | strategy=%s | used=%.2f | limit=%.2f",
                            symbol, sm, alloc_used, alloc_limit)
                        evt.log_scan(symbol, "candidate_rejected",
                                     reject_reason="allocation_exceeded",
                                     timeframe=TIMEFRAME, strategy_mode=sm,
                                     features=_feat, score_decision=_score,
                                     checks={"strategy": sm,
                                             "alloc_used": round(alloc_used, 2),
                                             "alloc_limit": round(alloc_limit, 2)})
                        continue

                    # Symbol-level performance block (applies in all modes including manual)
                    sym_stats = symbol_perf.get(sm, {}).get(symbol)
                    if sym_stats and sym_stats.get("status") == "blocked":
                        logger.info(
                            "SYMBOL PERFORMANCE BLOCK | strategy=%s | symbol=%s | "
                            "trades=%d | wr=%.1f%% | pnl=%.2f",
                            sm, symbol,
                            sym_stats["total_trades"],
                            sym_stats["win_rate"] * 100,
                            sym_stats["total_pnl"],
                        )
                        evt.log_scan(symbol, "candidate_rejected",
                                     reject_reason="symbol_performance_blocked",
                                     timeframe=TIMEFRAME, strategy_mode=sm,
                                     features=_feat, score_decision=_score,
                                     checks={"strategy": sm,
                                             "symbol_performance": sym_stats})
                        continue

                    tl_before = len(state["trade_log"])
                    _open_position(state, symbol, prices[symbol], equity, now_str, evt=evt,
                                   strategy_name=sm, side=trade_side)
                    open_count += 1
                    if len(state["trade_log"]) > tl_before:
                        e = state["trade_log"][-1]
                        evt.log_trade(symbol, e["action"],
                                      price=e.get("price"), quantity=e.get("quantity"),
                                      size_usdt=e.get("size_usdt"), strategy=sm)
                else:
                    reason = getattr(strategy, "last_rejection_reason", "")
                    tp_decision = getattr(strategy, "last_decision", {})
                    evt.log_scan(symbol, "no_signal" if not reason else "candidate_rejected",
                                 reject_reason=reason or None,
                                 bars_loaded=bars_loaded,
                                 timeframe=TIMEFRAME, strategy_mode=sm,
                                 side=getattr(strategy, "last_side", "") or "none",
                                 market_regime=market_regime,
                                 features=_feat, score_decision=_score,
                                 decision=tp_decision if sm == "trend_pullback_v1" else None)

        equity = _total_equity(state, prices)
        if equity > state["peak_equity"]:
            state["peak_equity"] = equity

        state.setdefault("equity_log", []).append({
            "time":      now_str,
            "equity":    round(equity, 2),
            "daily_pnl": round(state.get("daily_pnl", 0.0), 2),
            "open_pos":  len(state["positions"]),
            "dd_pct":    round((equity - state["peak_equity"]) / state["peak_equity"] * 100, 3)
                         if state["peak_equity"] > 0 else 0.0,
        })

        _save_state(state)
        _print_status(state, prices, equity, APPROVED_SYMBOLS,
                      strategy_mode=strategy_mode,
                      active_strategies=active_strategies)

        evt.log_run("completed",
                    symbols_scanned=len(candles),
                    open_positions=len(state["positions"]),
                    symbol_mode=symbol_mode,
                    symbols_scanned_list=list(candles.keys()),
                    duration_sec=evt.elapsed())

        # Collect short-side stats
        short_positions = {s: p for s, p in state["positions"].items() if p.get("side") == "short"}
        long_positions = {s: p for s, p in state["positions"].items() if p.get("side") != "short"}
        short_trades = [t for t in state.get("trade_log", []) if t.get("side") == "short"]
        long_trades = [t for t in state.get("trade_log", []) if t.get("side") != "short"]
        short_pnl = sum(t.get("pnl", 0) for t in short_trades if t.get("action") == "CLOSE")
        long_pnl = sum(t.get("pnl", 0) for t in long_trades if t.get("action") == "CLOSE")

        write_dashboard_summary(
            extra_fields={
                "strategy": strategy_mode,
                "active_strategies": active_strategies,
                "base_allocations": base_allocs,
                "strategy_allocations": strategy_allocs,
                "strategy_performance": strategy_perf,
                "strategy_kill_switch": kill_switch_status,
                "per_strategy_open_allocation": {
                    sm: round(sum(
                        p["entry_price"] * p["quantity"]
                        for p in state["positions"].values()
                        if p.get("strategy") == sm
                    ), 2)
                    for sm in active_strategies
                },
                "symbol_mode": symbol_mode,
                "total_symbols_requested": len(symbols_requested),
                "total_symbols_valid": len(symbols_valid),
                "total_symbols_rejected": len(symbols_rejected) + len(symbols_blocked_liquidity),
                "symbols_scanned_count": len(candles),
                "symbols_scanned": list(candles.keys()),
                "top_scanned_symbols": list(candles.keys())[:5],
                "symbols_blocked_by_liquidity": symbols_blocked_liquidity,
                "market_regime": market_regime["regime"],
                "regime_confidence": market_regime["confidence"],
                "long_positions": len(long_positions),
                "short_positions": len(short_positions),
                "pnl_by_side": {
                    "long": round(long_pnl, 4),
                    "short": round(short_pnl, 4),
                },
                "trades_by_side": {
                    "long_closed": len([t for t in long_trades if t.get("action") == "CLOSE"]),
                    "short_closed": len([t for t in short_trades if t.get("action") == "CLOSE"]),
                },
                "symbol_performance": symbol_perf,
                "blocked_symbols": blocked_symbols,
            }
        )

    except Exception as exc:
        capture_exception(evt, context="run:rsi_reversion")
        evt.log_run("error", error=str(exc), duration_sec=evt.elapsed())
        send_alert("ERROR", {"reason": str(exc)})
        raise


# ── Durum raporu ──────────────────────────────────────────────────────────────

def _print_status(state: dict, prices: dict[str, float], equity: float,
                  symbols: list[str] | None = None,
                  strategy_mode: str = "rsi_reversion",
                  active_strategies: list[str] | None = None) -> None:
    alloc  = allocated_usdt(state["positions"])
    u_pnl  = unrealized_pnl(state["positions"], prices)
    guard  = check_daily_loss_guard(
        state["daily_start_equity"],
        state.get("daily_realized_loss", 0.0),
        u_pnl,
    )
    peak   = state.get("peak_equity", INITIAL_CASH)
    dd_pct = (equity - peak) / peak * 100 if peak > 0 else 0.0

    trade_log    = state.get("trade_log", [])
    total_closed = len([t for t in trade_log if t["action"] == "CLOSE"])

    print("\n" + "═" * 62)
    label = strategy_mode.upper()
    if strategy_mode == "auto":
        active = active_strategies if active_strategies else ["?"]
        label = "AUTO [" + ", ".join(active) + "]"
    print(f"  INTRADAY TRADER — {label} / 15M")
    print("═" * 62)
    print(f"  Zaman          : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Cash           : {state['cash']:>10,.2f} USDT")
    print(f"  Equity         : {equity:>10,.2f} USDT")
    print(f"  Drawdown       : {equity - peak:>+10.2f} USDT  ({dd_pct:+.2f}%)")
    print(f"  Günlük PnL     : {state.get('daily_pnl', 0.0):>+10.2f} USDT")
    print(f"  Günlük koruma  : {'AKTIF' if guard.blocked else '—'} (kayıp: %{guard.loss_pct:.2f})")
    print(f"  Açık alokasyon : {alloc:>10.2f} USDT")
    print()

    if state["positions"]:
        print(f"  {'Sembol':<14} {'Side':>5} {'Giriş':>10} {'Güncel':>10} {'TP':>10} {'SL':>10}")
        print("  " + "-" * 62)
        for sym, pos in state["positions"].items():
            cur = prices.get(sym, pos["entry_price"])
            is_short = pos.get("side") == "short"
            if is_short:
                pnl_p = (pos["entry_price"] - cur) / pos["entry_price"] * 100
            else:
                pnl_p = (cur - pos["entry_price"]) / pos["entry_price"] * 100
            side_label = "SHORT" if is_short else "LONG"
            print(
                f"  {sym:<14} {side_label:>5} {pos['entry_price']:>10.4f} {cur:>10.4f} "
                f"{pos['tp']:>10.4f} {pos['sl']:>10.4f}  ({pnl_p:+.2f}%)"
            )
    else:
        print("  Açık pozisyon yok.")

    print()
    print(f"  {'Sembol':<14} {'Trade':>5} {'W':>3} {'L':>3} {'WR%':>6} {'PnL':>9}")
    print("  " + "-" * 45)
    for sym in (symbols or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT"]):
        w   = state["sym_wins"].get(sym, 0)
        l   = state["sym_losses"].get(sym, 0)
        t   = w + l
        wr  = (w / t * 100) if t > 0 else 0.0
        pnl = state["sym_pnl"].get(sym, 0.0)
        print(f"  {sym:<14} {t:>5} {w:>3} {l:>3} {wr:>5.1f}% {pnl:>+9.2f}")

    print()
    print(f"  Toplam tamamlanan trade: {total_closed}")
    print("═" * 62 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

# ── Loop mode ──────────────────────────────────────────────────────────────────

def _run_loop(strategy_mode: str, dry_run: bool, symbol_mode: str, interval: int) -> None:
    """Run scan/trade cycle continuously with sleep between cycles."""
    print(f"\n  LOOP MODE ENABLED | strategy={strategy_mode} | symbols={symbol_mode} | interval={interval}s")
    print(f"  Paper trading only. Press Ctrl+C to stop.\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            cycle_start = time.monotonic()
            logger.info("LOOP CYCLE #%d | strategy=%s | interval=%ds", cycle, strategy_mode, interval)

            try:
                run(strategy_mode=strategy_mode, dry_run=dry_run, symbol_mode=symbol_mode)
            except Exception as exc:
                logger.error("LOOP CYCLE #%d ERROR | %s — continuing", cycle, exc)

            elapsed = time.monotonic() - cycle_start
            sleep_sec = max(0, interval - elapsed)

            # Update dashboard with loop status
            try:
                dashboard_path = Path(__file__).parent.parent / "logs" / "dashboard_summary.json"
                if dashboard_path.exists():
                    d = json.loads(dashboard_path.read_text())
                    d["loop_mode"] = True
                    d["loop_interval_sec"] = interval
                    d["loop_cycle"] = cycle
                    d["last_loop_run"] = datetime.now(timezone.utc).isoformat()
                    with open(dashboard_path, "w") as f:
                        json.dump(d, f, indent=2, default=str)
            except Exception:
                pass

            if sleep_sec > 0:
                logger.info("LOOP SLEEP | %.0fs until next cycle", sleep_sec)
                time.sleep(sleep_sec)
    except KeyboardInterrupt:
        print(f"\n  Loop stopped by user after {cycle} cycle(s).")
        logger.info("LOOP STOPPED | %d cycles completed", cycle)


def main() -> None:
    p = argparse.ArgumentParser(description="intraday paper trader")
    p.add_argument("--status", action="store_true", help="Sadece durum göster")
    p.add_argument("--reset", action="store_true", help="State'i sıfırla (başlangıç capitaline dön)")
    p.add_argument("--strategy", default=INTRADAY_STRATEGY_MODE,
                   choices=("auto", "rsi_reversion", "momentum_pullback_v1", "short_momentum_v1", "trend_pullback_v1"),
                   help=f"Strategy mode: 'auto' selects by regime (default: {INTRADAY_STRATEGY_MODE})")
    p.add_argument("--dry-run", action="store_true",
                   help="Scan and log decisions but do NOT open trades")
    p.add_argument("--symbols", default=DEFAULT_SYMBOL_MODE,
                   choices=("core", "expanded", "dynamic"),
                   help=f"Symbol universe: 'core' (4), 'expanded' (13), or 'dynamic' (40). Default: {DEFAULT_SYMBOL_MODE}")
    p.add_argument("--loop", action="store_true",
                   help="Run continuously in loop mode until Ctrl+C")
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between loop cycles (default: 60)")
    args = p.parse_args()
    if args.reset:
        _reset_state()
        return
    if args.loop:
        _run_loop(strategy_mode=args.strategy, dry_run=args.dry_run,
                  symbol_mode=args.symbols, interval=args.interval)
    else:
        run(status_only=args.status, strategy_mode=args.strategy,
            dry_run=args.dry_run, symbol_mode=args.symbols)


if __name__ == "__main__":
    main()
