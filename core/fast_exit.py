"""
Fast Exit Engine — evaluates open paper positions for exit decisions.

Supports:
  1. Stop loss (fixed)
  2. Take profit (fixed)
  3. Partial take profit (reduce position, keep remainder)
  4. Trailing stop (activate after gain, ratchet up)
  5. Momentum decay exit (stale + weak momentum only)
  6. Soft profit protect (lock in gains before they evaporate)
  7. Time-based exit (max holding bars, disabled by default)

Winners are allowed to run — momentum_decay only fires when progress is weak
AND momentum indicators are fading.

Returns a decision dict. Never executes anything itself.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Momentum decay config ─────────────────────────────────────────────────────
MOMENTUM_DECAY_MIN_HOLDING_MINUTES = 180
MOMENTUM_DECAY_MIN_PROGRESS_PCT = 0.30

# ── Early momentum failure config ─────────────────────────────────────────────
EARLY_FAILURE_MIN_HOLDING_MINUTES = 45
EARLY_FAILURE_MAX_PNL_PCT = -0.50
EARLY_FAILURE_MAX_RSI = 40
EARLY_FAILURE_MAX_DISTANCE_TO_SL_PCT = 0.25

# ── Soft profit protect config ────────────────────────────────────────────────
SOFT_PROFIT_ACTIVATE_PCT = 0.60
SOFT_PROFIT_PROTECT_PCT = 0.25
SOFT_PROFIT_MIN_HOLDING_MINUTES = 60

# ── Position scaling config ───────────────────────────────────────────────────
SCALING_ENABLED = True
SCALING_MIN_PNL_PCT = 0.50
SCALING_ADD_PCT = 0.10
SCALING_MAX_COUNT = 2
SCALING_MIN_DISTANCE_PCT = 0.30
MAX_TOTAL_EXPOSURE_PCT = 0.80
MAX_SYMBOL_EXPOSURE_PCT = 0.30


@dataclass
class ExitDecision:
    should_exit: bool = False
    exit_type: str = "hold"
    exit_pct: float = 1.0
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


def evaluate_stop_loss(
    price: float,
    sl_price: float,
) -> ExitDecision:
    if price <= sl_price:
        return ExitDecision(
            should_exit=True,
            exit_type="stop_loss",
            exit_pct=1.0,
            reason="stop_loss",
            checks={"price": round(price, 4), "sl": round(sl_price, 4)},
        )
    return ExitDecision(checks={"price": round(price, 4), "sl": round(sl_price, 4)})


def evaluate_early_momentum_failure(
    entry_price: float,
    sl_price: float,
    price: float,
    entry_time: str,
    now: datetime,
    features: dict[str, Any] | None,
) -> ExitDecision:
    """Exit failing trades early when momentum is clearly broken.

    Triggers when ALL are true:
      1. holding_minutes >= EARLY_FAILURE_MIN_HOLDING_MINUTES
      2. unrealized_pnl_pct <= EARLY_FAILURE_MAX_PNL_PCT
      3. trend != bullish OR price < EMA50
      4. RSI < EARLY_FAILURE_MAX_RSI
      5. Price is near SL: distance_to_sl_pct <= EARLY_FAILURE_MAX_DISTANCE_TO_SL_PCT

    Never triggers on winning trades.
    For long positions only.
    """
    holding_min = _compute_holding_minutes(entry_time, now)
    pnl_pct = (price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0
    distance_to_sl_pct = ((price - sl_price) / price) * 100 if price > 0 else 999.0

    checks: dict[str, Any] = {
        "holding_minutes": round(holding_min, 1),
        "unrealized_pnl_pct": round(pnl_pct, 4),
        "distance_to_sl_pct": round(distance_to_sl_pct, 4),
        "early_failure_enabled": True,
    }

    # 1. Must be held long enough
    if holding_min < EARLY_FAILURE_MIN_HOLDING_MINUTES:
        checks["early_failure_skip"] = "holding_too_short"
        return ExitDecision(checks=checks)

    # 2. Must be losing
    if pnl_pct > EARLY_FAILURE_MAX_PNL_PCT:
        checks["early_failure_skip"] = "not_losing_enough"
        return ExitDecision(checks=checks)

    # 3-4. Feature checks
    if not features:
        checks["early_failure_skip"] = "no_features"
        return ExitDecision(checks=checks)

    trend = features.get("trend")
    ema50 = features.get("ema_50")
    rsi = features.get("rsi")

    trend_broken = (trend is not None and trend != "bullish") or \
                   (ema50 is not None and price < ema50)
    rsi_weak = rsi is not None and rsi < EARLY_FAILURE_MAX_RSI

    checks["trend"] = trend
    checks["price_above_ema50"] = ema50 is not None and price > ema50
    checks["rsi"] = rsi

    if not trend_broken:
        checks["early_failure_skip"] = "trend_intact"
        return ExitDecision(checks=checks)

    if not rsi_weak:
        checks["early_failure_skip"] = "rsi_not_weak"
        return ExitDecision(checks=checks)

    # 5. Must be near SL
    if distance_to_sl_pct > EARLY_FAILURE_MAX_DISTANCE_TO_SL_PCT:
        checks["early_failure_skip"] = "not_near_sl"
        return ExitDecision(checks=checks)

    # All conditions met
    logger.info(
        "EARLY MOMENTUM FAILURE | pnl=%.2f%% | held=%.0fmin | rsi=%.1f | dist_to_sl=%.2f%%",
        pnl_pct, holding_min, rsi or 0, distance_to_sl_pct,
    )
    return ExitDecision(
        should_exit=True,
        exit_type="early_momentum_failure",
        exit_pct=1.0,
        reason=f"early_momentum_failure (pnl={pnl_pct:+.2f}%, held={holding_min:.0f}min, rsi={rsi:.1f})",
        checks=checks,
    )


def evaluate_take_profit(
    price: float,
    tp_price: float,
) -> ExitDecision:
    if price >= tp_price:
        return ExitDecision(
            should_exit=True,
            exit_type="take_profit",
            exit_pct=1.0,
            reason="take_profit",
            checks={"price": round(price, 4), "tp": round(tp_price, 4)},
        )
    return ExitDecision(checks={"price": round(price, 4), "tp": round(tp_price, 4)})


def evaluate_partial_tp(
    price: float,
    entry_price: float,
    partial_tp_done: bool,
    partial_tp_enabled: bool,
    partial_tp_pct: float,
    partial_tp_size: float,
) -> ExitDecision:
    if not partial_tp_enabled or partial_tp_done:
        return ExitDecision(checks={"partial_tp_enabled": partial_tp_enabled,
                                     "partial_tp_done": partial_tp_done})

    target = entry_price * (1 + partial_tp_pct)
    if price >= target:
        return ExitDecision(
            should_exit=True,
            exit_type="partial_take_profit",
            exit_pct=partial_tp_size,
            reason="partial_take_profit",
            checks={"price": round(price, 4), "target": round(target, 4),
                     "partial_pct": partial_tp_pct, "exit_size": partial_tp_size},
        )
    return ExitDecision(checks={"price": round(price, 4), "target": round(target, 4)})


def evaluate_trailing(
    price: float,
    entry_price: float,
    trail_active: bool,
    trail_sl: float,
    trailing_enabled: bool,
    trailing_activate_pct: float,
    trailing_stop_pct: float,
) -> tuple[ExitDecision, bool, float]:
    if not trailing_enabled:
        return ExitDecision(checks={"trailing_enabled": False}), trail_active, trail_sl

    checks: dict[str, Any] = {"trailing_enabled": True, "trail_active": trail_active}

    new_active = trail_active
    new_trail_sl = trail_sl

    if not trail_active and price >= entry_price * (1 + trailing_activate_pct):
        new_active = True
        logger.info("TRAILING AKTIF | price=%.4f >= activate=%.4f",
                     price, entry_price * (1 + trailing_activate_pct))

    if new_active:
        candidate = price * (1 - trailing_stop_pct)
        new_trail_sl = max(trail_sl, candidate)
        checks["trail_sl"] = round(new_trail_sl, 4)

    if new_active and price <= new_trail_sl:
        return ExitDecision(
            should_exit=True,
            exit_type="trailing_stop",
            exit_pct=1.0,
            reason="trailing_stop",
            checks=checks,
        ), new_active, new_trail_sl

    return ExitDecision(checks=checks), new_active, new_trail_sl


def evaluate_time_exit(
    entry_time: str,
    timeframe_minutes: int,
    now: datetime,
    max_holding_bars_enabled: bool,
    max_holding_bars: int,
) -> ExitDecision:
    if not max_holding_bars_enabled:
        return ExitDecision(checks={"time_exit_enabled": False})

    try:
        entry_dt = datetime.fromisoformat(entry_time)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ExitDecision(checks={"time_exit_enabled": True, "entry_time_invalid": True})

    elapsed_min = (now - entry_dt).total_seconds() / 60.0
    max_min = max_holding_bars * timeframe_minutes

    if elapsed_min >= max_min:
        return ExitDecision(
            should_exit=True,
            exit_type="time_exit",
            exit_pct=1.0,
            reason=f"time_exit ({elapsed_min:.0f} min >= {max_min:.0f} min)",
            checks={"elapsed_min": round(elapsed_min, 1), "max_min": max_min,
                     "bars": max_holding_bars, "tf_min": timeframe_minutes},
        )
    return ExitDecision(checks={"elapsed_min": round(elapsed_min, 1), "max_min": max_min})


def _compute_holding_minutes(entry_time: str, now: datetime) -> float:
    try:
        entry_dt = datetime.fromisoformat(entry_time)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        return (now - entry_dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return 0.0


def evaluate_momentum_decay(
    entry_price: float,
    price: float,
    entry_time: str,
    now: datetime,
    features: dict[str, Any] | None,
    partial_tp_done: bool,
    side: str = "long",
) -> ExitDecision:
    """Exit stale trades only when momentum is dead and progress is weak.

    Never exits just because time passed. Requires ALL of:
      1. Held longer than MOMENTUM_DECAY_MIN_HOLDING_MINUTES
      2. unrealized_pnl_pct < MOMENTUM_DECAY_MIN_PROGRESS_PCT
      3. At least 2 of 5 momentum weakness signals are true

    Winners are always protected — never exited by this rule.
    For long: price above EMA50 + pnl >= 0.
    For short: price below EMA50 + pnl >= 0.
    """
    is_short = side == "short"
    holding_min = _compute_holding_minutes(entry_time, now)
    if entry_price > 0:
        pnl_pct = (entry_price - price) / entry_price * 100 if is_short else (price - entry_price) / entry_price * 100
    else:
        pnl_pct = 0.0

    checks: dict[str, Any] = {
        "holding_minutes": round(holding_min, 1),
        "unrealized_pnl_pct": round(pnl_pct, 4),
        "momentum_decay_enabled": True,
        "side": side,
    }

    # Condition 1: must be held long enough
    if holding_min < MOMENTUM_DECAY_MIN_HOLDING_MINUTES:
        checks["momentum_decay_skip"] = "holding_too_short"
        return ExitDecision(checks=checks)

    # Condition 2: progress is weak
    if pnl_pct >= MOMENTUM_DECAY_MIN_PROGRESS_PCT:
        checks["momentum_decay_skip"] = "sufficient_progress"
        return ExitDecision(checks=checks)

    # Winner protection
    if features:
        ema50 = features.get("ema_50")
        if is_short:
            if ema50 is not None and price < ema50 and pnl_pct >= 0.0:
                checks["momentum_decay_skip"] = "winner_protected_below_ema50"
                return ExitDecision(checks=checks)
        else:
            if ema50 is not None and price > ema50 and pnl_pct >= 0.0:
                checks["momentum_decay_skip"] = "winner_protected_above_ema50"
                return ExitDecision(checks=checks)

    # Condition 3: count momentum weakness signals
    weak_count = 0
    weak_signals: dict[str, bool] = {}

    if features:
        if is_short:
            # Short momentum fading signals
            ema50 = features.get("ema_50")
            weak_signals["price_above_ema50"] = ema50 is not None and price > ema50

            rsi = features.get("rsi")
            weak_signals["rsi_above_55"] = rsi is not None and rsi > 55

            vol_ratio = features.get("volume_spike_ratio")
            weak_signals["volume_low"] = vol_ratio is not None and vol_ratio < 0.80

            close_val = features.get("close")
            prev_close = features.get("previous_close")
            weak_signals["close_above_prev"] = (
                prev_close is not None and close_val is not None and close_val > prev_close
            )

            trend = features.get("trend")
            weak_signals["trend_not_bearish"] = trend is not None and trend != "bearish"
        else:
            # Long momentum fading signals
            ema50 = features.get("ema_50")
            weak_signals["price_below_ema50"] = ema50 is not None and price < ema50

            rsi = features.get("rsi")
            weak_signals["rsi_below_50"] = rsi is not None and rsi < 50

            vol_ratio = features.get("volume_spike_ratio")
            weak_signals["volume_low"] = vol_ratio is not None and vol_ratio < 0.80

            close_val = features.get("close")
            prev_close = features.get("previous_close")
            weak_signals["close_below_prev"] = (
                prev_close is not None and close_val is not None and close_val < prev_close
            )

            trend = features.get("trend")
            weak_signals["trend_not_bullish"] = trend is not None and trend != "bullish"
    else:
        checks["momentum_decay_skip"] = "no_features"
        return ExitDecision(checks=checks)

    weak_count = sum(1 for v in weak_signals.values() if v)
    checks["weak_signals"] = weak_signals
    checks["momentum_weak_count"] = weak_count

    if weak_count < 2:
        checks["momentum_decay_skip"] = "momentum_still_ok"
        return ExitDecision(checks=checks)

    # All conditions met — exit
    checks["momentum_decay_skip"] = None
    logger.info(
        "MOMENTUM DECAY (%s) | pnl=%.2f%% | held=%.0fmin | weak=%d/5",
        side, pnl_pct, holding_min, weak_count,
    )
    return ExitDecision(
        should_exit=True,
        exit_type="momentum_decay_exit",
        exit_pct=1.0,
        reason=f"momentum_decay_exit ({side} pnl={pnl_pct:+.2f}%, held={holding_min:.0f}min, weak={weak_count}/5)",
        checks=checks,
    )


def evaluate_soft_profit_protect(
    entry_price: float,
    price: float,
    entry_time: str,
    now: datetime,
    max_unrealized_pnl_pct: float | None,
    soft_profit_active: bool,
    partial_tp_done: bool,
    side: str = "long",
) -> tuple[ExitDecision, float, bool]:
    """Soft profit protect — lock in gains before they evaporate.

    If a trade reached SOFT_PROFIT_ACTIVATE_PCT but faded back to
    SOFT_PROFIT_PROTECT_PCT, close it before it becomes a loser.

    For shorts: profit when price drops, so pnl = (entry - price) / entry * 100.

    Returns (decision, updated_max_unrealized_pnl_pct, updated_soft_profit_active).
    """
    is_short = side == "short"
    holding_min = _compute_holding_minutes(entry_time, now)
    if entry_price > 0:
        pnl_pct = (entry_price - price) / entry_price * 100 if is_short else (price - entry_price) / entry_price * 100
    else:
        pnl_pct = 0.0

    # Track peak profit
    current_max = max_unrealized_pnl_pct if max_unrealized_pnl_pct is not None else pnl_pct
    new_max = max(current_max, pnl_pct)
    new_active = soft_profit_active or (new_max >= SOFT_PROFIT_ACTIVATE_PCT)

    checks: dict[str, Any] = {
        "unrealized_pnl_pct": round(pnl_pct, 4),
        "max_unrealized_pnl_pct": round(new_max, 4),
        "soft_profit_active": new_active,
        "holding_minutes": round(holding_min, 1),
    }

    # Activate threshold
    if not new_active:
        return ExitDecision(checks=checks), new_max, new_active

    # Must have been held long enough
    if holding_min < SOFT_PROFIT_MIN_HOLDING_MINUTES:
        checks["soft_profit_skip"] = "holding_too_short"
        return ExitDecision(checks=checks), new_max, new_active

    # Still above protect level — keep running
    if pnl_pct >= SOFT_PROFIT_PROTECT_PCT:
        checks["soft_profit_skip"] = "still_above_protect"
        return ExitDecision(checks=checks), new_max, new_active

    # Faded below protect — exit
    logger.info(
        "SOFT PROFIT PROTECT | max=%.2f%% → now=%.2f%%",
        new_max, pnl_pct,
    )
    return ExitDecision(
        should_exit=True,
        exit_type="soft_profit_protect",
        exit_pct=1.0,
        reason=f"soft_profit_protect (max={new_max:+.2f}% → now={pnl_pct:+.2f}%)",
        checks=checks,
    ), new_max, new_active


def evaluate_position_scaling(
    position: dict,
    price: float,
    features: dict[str, Any] | None,
    equity: float,
    total_allocated_usdt: float,
) -> dict[str, Any]:
    """Evaluate whether to add to a winning position.

    ONLY scales when:
      - Position is profitable (pnl >= SCALING_MIN_PNL_PCT)
      - Trend is bullish
      - Price above EMA50
      - Volume is healthy (>0.80x average)
      - Scale count not exceeded
      - Sufficient distance from last scale
      - Exposure limits not breached

    Returns dict with should_scale, reason, scale_size_usdt, checks.
    """
    if not SCALING_ENABLED:
        return {"should_scale": False, "reason": "scaling_disabled", "checks": {}}

    entry_price = position["entry_price"]
    scale_count = position.get("scale_count", 0)
    last_scale_price = position.get("last_scale_price")
    current_size = position.get("size_usdt", 0)
    strategy = position.get("strategy", "")

    pnl_pct = (price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0

    checks: dict[str, Any] = {
        "pnl_pct": round(pnl_pct, 4),
        "scale_count": scale_count,
        "scaling_enabled": True,
    }

    # 1. Must be profitable
    if pnl_pct < SCALING_MIN_PNL_PCT:
        checks["scale_skip"] = f"pnl_too_low ({pnl_pct:+.2f}% < {SCALING_MIN_PNL_PCT}%)"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    # 2. Feature checks
    if not features:
        checks["scale_skip"] = "no_features"
        return {"should_scale": False, "reason": "no_features",
                "scale_size_usdt": 0, "checks": checks}

    trend = features.get("trend")
    if trend != "bullish":
        checks["scale_skip"] = f"trend_not_bullish ({trend})"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    ema50 = features.get("ema_50")
    if ema50 is not None and price <= ema50:
        checks["scale_skip"] = "price_not_above_ema50"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    vol_ratio = features.get("volume_spike_ratio")
    if vol_ratio is not None and vol_ratio < 0.80:
        checks["scale_skip"] = f"volume_too_weak ({vol_ratio:.2f})"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    # 3. Scale count limit
    if scale_count >= SCALING_MAX_COUNT:
        checks["scale_skip"] = f"max_scales_reached ({scale_count}/{SCALING_MAX_COUNT})"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    # 4. Distance from last scale
    if last_scale_price is not None:
        distance_pct = abs(price - last_scale_price) / price * 100 if price > 0 else 0.0
        if distance_pct < SCALING_MIN_DISTANCE_PCT:
            checks["scale_skip"] = f"too_close_to_last_scale ({distance_pct:.2f}% < {SCALING_MIN_DISTANCE_PCT}%)"
            return {"should_scale": False, "reason": checks["scale_skip"],
                    "scale_size_usdt": 0, "checks": checks}

    # 5. Exposure limits
    add_size = equity * SCALING_ADD_PCT
    new_symbol_total = current_size + add_size
    new_total_alloc = total_allocated_usdt + add_size

    if new_symbol_total > equity * MAX_SYMBOL_EXPOSURE_PCT:
        checks["scale_skip"] = f"symbol_exposure_exceeded ({new_symbol_total:.2f} > {equity * MAX_SYMBOL_EXPOSURE_PCT:.2f})"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    if new_total_alloc > equity * MAX_TOTAL_EXPOSURE_PCT:
        checks["scale_skip"] = f"total_exposure_exceeded ({new_total_alloc:.2f} > {equity * MAX_TOTAL_EXPOSURE_PCT:.2f})"
        return {"should_scale": False, "reason": checks["scale_skip"],
                "scale_size_usdt": 0, "checks": checks}

    # All checks passed
    checks["trend"] = trend
    checks["price_above_ema50"] = ema50 is None or price > ema50
    checks["volume_spike_ratio"] = vol_ratio
    checks["add_size_usdt"] = round(add_size, 2)
    checks["new_symbol_total"] = round(new_symbol_total, 2)

    logger.info(
        "SCALE CANDIDATE | %s | pnl=%.2f%% | count=%d | add=%.2f USDT",
        position.get("symbol", "?"), pnl_pct, scale_count, add_size,
    )

    return {
        "should_scale": True,
        "reason": f"pnl={pnl_pct:+.2f}% trend={trend} count={scale_count}",
        "scale_size_usdt": add_size,
        "checks": checks,
    }


def evaluate_exit(
    pos: dict,
    price: float,
    now: datetime,
    timeframe_minutes: int,
    features: dict[str, Any] | None = None,
    # Config
    partial_tp_enabled: bool = True,
    partial_tp_pct: float = 0.012,
    partial_tp_size: float = 0.50,
    trailing_enabled: bool = True,
    trailing_activate_pct: float = 0.04,
    trailing_stop_pct: float = 0.015,
    max_holding_bars_enabled: bool = False,
    max_holding_bars: int = 96,
) -> tuple[ExitDecision, dict]:
    """
    Run all exit checks in priority order.

    Returns (decision, updated_position_fields).
    updated_position_fields contains trail_active, trail_sl,
    max_unrealized_pnl_pct, soft_profit_active if changed.
    """
    entry_price = pos["entry_price"]
    tp_price = pos["tp"]
    sl_price = pos["sl"]
    entry_time = pos.get("entry_time", "")
    partial_tp_done = pos.get("partial_tp_done", False)
    trail_active = pos.get("trail_active", False)
    trail_sl = pos.get("trail_sl", sl_price)
    max_unrealized_pnl_pct = pos.get("max_unrealized_pnl_pct")
    soft_profit_active = pos.get("soft_profit_active", False)
    side = pos.get("side", "long")

    is_short = side == "short"

    # PnL: long = (price - entry), short = (entry - price)
    if entry_price > 0:
        pnl_pct = ((entry_price - price) / entry_price * 100
                    if is_short else (price - entry_price) / entry_price * 100)
    else:
        pnl_pct = 0.0
    holding_min = _compute_holding_minutes(entry_time, now)

    pos_updates: dict[str, Any] = {}

    # Always track max profit
    new_max = max(max_unrealized_pnl_pct if max_unrealized_pnl_pct is not None else pnl_pct, pnl_pct)
    if new_max != max_unrealized_pnl_pct:
        pos_updates["max_unrealized_pnl_pct"] = new_max

    # Build common checks for final hold output
    feature_checks: dict[str, Any] = {"side": side}
    if features:
        feature_checks["price_above_ema50"] = (
            features.get("ema_50") is not None and price > features["ema_50"]
        )
        feature_checks["rsi"] = features.get("rsi")
        feature_checks["trend"] = features.get("trend")
        feature_checks["volume_spike_ratio"] = features.get("volume_spike_ratio")
        feature_checks["soft_profit_active"] = soft_profit_active
        feature_checks["partial_tp_done"] = partial_tp_done

    # 1. Stop loss — highest priority (long: price<=sl, short: price>=sl)
    if is_short:
        sl_hit = price >= sl_price
    else:
        sl_hit = price <= sl_price
    if sl_hit:
        d = ExitDecision(
            should_exit=True, exit_type="stop_loss", exit_pct=1.0,
            reason="stop_loss",
            checks={"price": round(price, 4), "sl": round(sl_price, 4), "side": side})
        d.checks.update(feature_checks)
        return d, pos_updates

    # 2. Early momentum failure (long only for now)
    if not is_short:
        d = evaluate_early_momentum_failure(
            entry_price, sl_price, price, entry_time, now, features,
        )
        if d.should_exit:
            d.checks.update(feature_checks)
            return d, pos_updates

    # 3. Trailing stop (long: trail up, short: trail down)
    if is_short:
        # Short trailing: activate when price drops enough, trail_sl moves down
        if trailing_enabled:
            if not trail_active and price <= entry_price * (1 - trailing_activate_pct):
                new_active = True
                new_trail_sl = price * (1 + trailing_stop_pct)
                logger.info("TRAILING AKTIF (SHORT) | price=%.4f", price)
            elif trail_active:
                new_active = True
                candidate = price * (1 + trailing_stop_pct)
                new_trail_sl = min(trail_sl, candidate)
            else:
                new_active = False
                new_trail_sl = trail_sl
            if new_active != trail_active or new_trail_sl != trail_sl:
                pos_updates["trail_active"] = new_active
                pos_updates["trail_sl"] = new_trail_sl
            if new_active and price >= new_trail_sl:
                d = ExitDecision(
                    should_exit=True, exit_type="trailing_stop", exit_pct=1.0,
                    reason="trailing_stop",
                    checks={"trailing_enabled": True, "trail_active": new_active,
                            "trail_sl": round(new_trail_sl, 4), "side": "short"})
                d.checks.update(feature_checks)
                return d, pos_updates
    else:
        d, new_active, new_sl = evaluate_trailing(
            price, entry_price, trail_active, trail_sl,
            trailing_enabled, trailing_activate_pct, trailing_stop_pct,
        )
        if new_active != trail_active or new_sl != trail_sl:
            pos_updates["trail_active"] = new_active
            pos_updates["trail_sl"] = new_sl
        if d.should_exit:
            d.checks.update(feature_checks)
            return d, pos_updates

    # 4. Partial take profit (long: price>=target, short: price<=target)
    if partial_tp_enabled and not partial_tp_done:
        if is_short:
            target = entry_price * (1 - partial_tp_pct)
            if price <= target:
                pos_updates["partial_tp_done"] = True
                d = ExitDecision(
                    should_exit=True, exit_type="partial_take_profit",
                    exit_pct=partial_tp_size, reason="partial_take_profit",
                    checks={"price": round(price, 4), "target": round(target, 4),
                             "partial_pct": partial_tp_pct, "exit_size": partial_tp_size,
                             "side": "short"})
                d.checks.update(feature_checks)
                return d, pos_updates
        else:
            d = evaluate_partial_tp(
                price, entry_price, partial_tp_done,
                partial_tp_enabled, partial_tp_pct, partial_tp_size,
            )
            if d.should_exit:
                pos_updates["partial_tp_done"] = True
                d.checks.update(feature_checks)
                return d, pos_updates

    # 5. Full take profit (long: price>=tp, short: price<=tp)
    if is_short:
        tp_hit = price <= tp_price
    else:
        tp_hit = price >= tp_price
    if tp_hit:
        d = ExitDecision(
            should_exit=True, exit_type="take_profit", exit_pct=1.0,
            reason="take_profit",
            checks={"price": round(price, 4), "tp": round(tp_price, 4), "side": side})
        d.checks.update(feature_checks)
        return d, pos_updates

    # 6. Soft profit protect
    d, new_max_pnl, new_sp_active = evaluate_soft_profit_protect(
        entry_price, price, entry_time, now,
        max_unrealized_pnl_pct, soft_profit_active, partial_tp_done,
        side=side,
    )
    if new_max_pnl != max_unrealized_pnl_pct:
        pos_updates["max_unrealized_pnl_pct"] = new_max_pnl
    if new_sp_active != soft_profit_active:
        pos_updates["soft_profit_active"] = new_sp_active
    if d.should_exit:
        d.checks.update(feature_checks)
        return d, pos_updates

    # 7. Momentum decay exit
    d = evaluate_momentum_decay(
        entry_price, price, entry_time, now,
        features, partial_tp_done,
        side=side,
    )
    if d.should_exit:
        d.checks.update(feature_checks)
        return d, pos_updates

    # 8. Time-based exit (disabled by default)
    d = evaluate_time_exit(
        entry_time, timeframe_minutes, now,
        max_holding_bars_enabled, max_holding_bars,
    )
    if d.should_exit:
        d.checks.update(feature_checks)
        return d, pos_updates

    # Hold — return enriched checks
    return ExitDecision(
        exit_type="hold",
        reason="hold",
        checks={
            "unrealized_pnl_pct": round(pnl_pct, 4),
            "max_unrealized_pnl_pct": round(new_max, 4),
            "holding_minutes": round(holding_min, 1),
            **feature_checks,
            **evaluate_stop_loss(price, sl_price).checks,
            "trail_active": trail_active,
            "partial_tp_done": partial_tp_done,
        },
    ), pos_updates
