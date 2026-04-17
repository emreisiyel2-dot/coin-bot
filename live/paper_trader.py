"""
Paper trading runner — breakout_v2 / 4h

Her 4 saatte bir (4h mum kapanışında) çalıştırılır.
Durum live/paper_state.json dosyasında saklanır.

Kullanım:
    python -m live.paper_trader            # sinyal kontrol + işlem
    python -m live.paper_trader --status   # sadece durum göster
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd

from config.settings import BREAKOUT_V2_CONFIG
from live.risk_controls import (
    SizingResult,
    allocated_usdt,
    check_daily_loss_guard,
    compute_position_size,
    unrealized_pnl,
)
from strategy.breakout_v2 import MomentumBreakoutV2Strategy
from strategy.signals import Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Sabitler ──────────────────────────────────────────────────────────────────
APPROVED_SYMBOLS   = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT"]
TIMEFRAME          = "4h"
LOOKBACK_BARS      = 260
MAX_CONCURRENT     = 3
TAKE_PROFIT_PCT    = 0.03
STOP_LOSS_PCT      = 0.01
TRAILING_ACTIVATE  = 0.015
TRAILING_STOP_PCT  = 0.01
INITIAL_CASH       = 10_000.0

SYM_MIN_EVAL_TRADES = 3
SYM_MIN_WIN_RATE    = 0.30

STATE_FILE = Path(__file__).parent / "paper_state.json"

STRATEGY_CFG = {
    **BREAKOUT_V2_CONFIG,
    "adx_min": 20.0,
    "vol_multiplier": 2.0,
    "breakout_margin_pct": 0.002,
}


# ── State yönetimi ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "cash":                INITIAL_CASH,
        "peak_equity":         INITIAL_CASH,
        "positions":           {},
        "sym_wins":            {},
        "sym_losses":          {},
        "sym_pnl":             {},
        "disabled_symbols":    [],
        "daily_date":          "",
        "daily_start_equity":  INITIAL_CASH,
        "daily_pnl":           0.0,
        "daily_realized_loss": 0.0,   # sadece zarar eden trade'lerin toplamı
        "trade_log":           [],
        "equity_log":          [],    # her çalışmada eklenen equity snapshot'ı
    }


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── Veri çekme ────────────────────────────────────────────────────────────────

def _fetch_candles(symbol: str, exchange: ccxt.Exchange) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LOOKBACK_BARS)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    # Son mum henüz kapanmamış — sadece kapalı barları kullan
    return df.iloc[:-1].reset_index(drop=True)


# ── Portföy yardımcıları ──────────────────────────────────────────────────────

def _total_equity(state: dict, prices: dict[str, float]) -> float:
    eq = state["cash"]
    for sym, pos in state["positions"].items():
        eq += pos["quantity"] * prices.get(sym, pos["entry_price"])
    return eq


def _open_position(
    state: dict,
    symbol: str,
    price: float,
    equity: float,
    prices: dict[str, float],
    now: str,
) -> None:
    alloc  = allocated_usdt(state["positions"])
    sizing = compute_position_size(
        equity=equity,
        cash=state["cash"],
        price=price,
        current_allocated_usdt=alloc,
        stop_loss_pct=STOP_LOSS_PCT,
    )

    if not sizing.allowed:
        logger.warning("OPEN REDDEDİLDİ | %s | %s", symbol, sizing.reason)
        return

    cost = sizing.quantity * price
    state["cash"] -= cost
    state["positions"][symbol] = {
        "entry_price":  price,
        "quantity":     sizing.quantity,
        "size_usdt":    sizing.size_usdt,
        "tp":           price * (1 + TAKE_PROFIT_PCT),
        "sl":           price * (1 - STOP_LOSS_PCT),
        "trail_sl":     price * (1 - STOP_LOSS_PCT),
        "trail_active": False,
        "entry_time":   now,
    }
    logger.info(
        "OPEN  | %s | price=%.4f | qty=%.6f | size=%.2f USDT | risk≈%.2f USDT",
        symbol, price, sizing.quantity, sizing.size_usdt,
        sizing.size_usdt * STOP_LOSS_PCT,
    )
    state["trade_log"].append({
        "time": now, "symbol": symbol, "action": "OPEN",
        "price": price, "quantity": sizing.quantity, "size_usdt": sizing.size_usdt,
    })


def _close_position(
    state: dict, symbol: str, price: float, now: str, reason: str
) -> float:
    pos = state["positions"].pop(symbol, None)
    if pos is None:
        return 0.0
    proceeds = pos["quantity"] * price
    pnl      = proceeds - pos["quantity"] * pos["entry_price"]
    state["cash"] += proceeds
    state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl
    if pnl < 0:
        state["daily_realized_loss"] = state.get("daily_realized_loss", 0.0) + abs(pnl)

    # Per-symbol tracker
    if pnl > 0:
        state["sym_wins"][symbol]   = state["sym_wins"].get(symbol, 0) + 1
    else:
        state["sym_losses"][symbol] = state["sym_losses"].get(symbol, 0) + 1
    state["sym_pnl"][symbol] = state["sym_pnl"].get(symbol, 0.0) + pnl

    # Auto-disable
    total = state["sym_wins"].get(symbol, 0) + state["sym_losses"].get(symbol, 0)
    if total >= SYM_MIN_EVAL_TRADES and symbol not in state["disabled_symbols"]:
        wr = state["sym_wins"].get(symbol, 0) / total
        if wr < SYM_MIN_WIN_RATE or state["sym_pnl"].get(symbol, 0.0) < 0:
            state["disabled_symbols"].append(symbol)
            logger.warning(
                "SYM DISABLED | %s | trades=%d | wr=%.1f%% | pnl=%.2f",
                symbol, total, wr * 100, state["sym_pnl"].get(symbol, 0.0),
            )

    logger.info("CLOSE | %s | price=%.4f | pnl=%.2f | reason=%s", symbol, price, pnl, reason)
    state["trade_log"].append({
        "time": now, "symbol": symbol, "action": "CLOSE",
        "price": price, "pnl": round(pnl, 4), "reason": reason,
    })
    return pnl


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

def run(status_only: bool = False) -> None:
    state    = _load_state()
    exchange = ccxt.binance({"enableRateLimit": True})
    strategy = MomentumBreakoutV2Strategy(config=STRATEGY_CFG)
    now_str  = datetime.now(timezone.utc).isoformat()
    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prices:  dict[str, float]     = {}
    candles: dict[str, pd.DataFrame] = {}

    for symbol in APPROVED_SYMBOLS:
        try:
            df = _fetch_candles(symbol, exchange)
            prices[symbol]  = float(df["close"].iloc[-1])
            candles[symbol] = df
        except Exception as exc:
            logger.warning("Veri çekme hatası | %s | %s", symbol, exc)

    _maybe_reset_daily(state, today, prices)

    equity      = _total_equity(state, prices)
    drawdown    = equity - state["peak_equity"]
    dd_pct      = drawdown / state["peak_equity"] * 100 if state["peak_equity"] > 0 else 0.0
    if equity > state["peak_equity"]:
        state["peak_equity"] = equity

    if status_only:
        _print_status(state, prices, equity, drawdown, dd_pct)
        return

    # ── Açık pozisyonlar: TP / SL / Trailing ─────────────────────────────────
    for symbol in list(state["positions"].keys()):
        if symbol not in prices:
            continue
        price = prices[symbol]
        pos   = state["positions"][symbol]
        entry = pos["entry_price"]

        if not pos["trail_active"] and price >= entry * (1 + TRAILING_ACTIVATE):
            pos["trail_active"] = True
            logger.info("TRAILING AKTİF | %s | price=%.4f", symbol, price)

        if pos["trail_active"]:
            new_trail   = price * (1 - TRAILING_STOP_PCT)
            pos["trail_sl"] = max(pos["trail_sl"], new_trail)

        effective_sl = pos["trail_sl"] if pos["trail_active"] else pos["sl"]

        if price >= pos["tp"]:
            _close_position(state, symbol, price, now_str, "TP")
        elif price <= effective_sl:
            reason = "TRAILING" if pos["trail_active"] else "SL"
            _close_position(state, symbol, price, now_str, reason)

    # Equity güncelle (kapanan pozisyonlardan sonra)
    equity = _total_equity(state, prices)

    # ── Günlük kayıp koruması (yeni trade açmadan önce) ────────────────────────
    u_pnl  = unrealized_pnl(state["positions"], prices)
    guard  = check_daily_loss_guard(
        daily_start_equity  = state["daily_start_equity"],
        daily_realized_loss = state.get("daily_realized_loss", 0.0),
        unrealized_pnl      = u_pnl,
    )
    if guard.blocked:
        logger.warning("GÜNLÜK KORUMA AKTİF | Yeni trade açılmıyor | %s", guard.reason)

    # ── Yeni sinyal tara ──────────────────────────────────────────────────────
    open_count = len(state["positions"])

    for symbol in APPROVED_SYMBOLS:
        if symbol not in candles:
            continue
        if symbol in state["positions"]:
            continue
        if symbol in state["disabled_symbols"]:
            continue
        if open_count >= MAX_CONCURRENT:
            break
        if guard.blocked:
            break

        try:
            signal = strategy.generate_signal(candles[symbol])
        except ValueError as exc:
            logger.debug("Sinyal hatası | %s | %s", symbol, exc)
            continue

        if signal == Signal.BUY:
            _open_position(state, symbol, prices[symbol], equity, prices, now_str)
            open_count += 1

    equity = _total_equity(state, prices)
    if equity > state["peak_equity"]:
        state["peak_equity"] = equity

    # Equity snapshot — her çalışmada eklenir (performans takibi için)
    state.setdefault("equity_log", []).append({
        "time":        now_str,
        "equity":      round(equity, 2),
        "daily_pnl":   round(state.get("daily_pnl", 0.0), 2),
        "open_pos":    len(state["positions"]),
        "dd_pct":      round((equity - state["peak_equity"]) / state["peak_equity"] * 100, 3)
                       if state["peak_equity"] > 0 else 0.0,
    })

    _save_state(state)

    drawdown = equity - state["peak_equity"]
    dd_pct   = drawdown / state["peak_equity"] * 100 if state["peak_equity"] > 0 else 0.0
    _print_status(state, prices, equity, drawdown, dd_pct)


# ── Durum raporu ──────────────────────────────────────────────────────────────

def _print_status(
    state: dict,
    prices: dict[str, float],
    equity: float,
    drawdown: float,
    dd_pct: float,
) -> None:
    disabled = state.get("disabled_symbols", [])
    active   = [s for s in APPROVED_SYMBOLS if s not in disabled]
    alloc    = allocated_usdt(state["positions"])
    u_pnl    = unrealized_pnl(state["positions"], prices)
    guard    = check_daily_loss_guard(
        state["daily_start_equity"],
        state.get("daily_realized_loss", 0.0),
        u_pnl,
    )

    print("\n" + "═" * 62)
    print("  PAPER TRADER — BREAKOUT_V2 / 4H")
    print("═" * 62)
    print(f"  Zaman              : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Cash               : {state['cash']:>10,.2f} USDT")
    print(f"  Equity             : {equity:>10,.2f} USDT")
    print(f"  Peak equity        : {state['peak_equity']:>10,.2f} USDT")
    print(f"  Drawdown           : {drawdown:>+10.2f} USDT  ({dd_pct:+.2f}%)")
    print(f"  Günlük PnL         : {state.get('daily_pnl', 0.0):>+10.2f} USDT")
    print(f"  Günlük kayıp       : {state.get('daily_realized_loss', 0.0):>10.2f} USDT  (kayıp: %{guard.loss_pct:.2f})")
    print(f"  Açık alokasyon     : {alloc:>10.2f} USDT  ({alloc/equity*100:.1f}% equity)")
    print(f"  Unrealized PnL     : {u_pnl:>+10.2f} USDT")
    guard_str = f"AKTIF — {guard.reason}" if guard.blocked else "—"
    print(f"  Günlük koruma      : {guard_str}")
    print()
    print(f"  Aktif semboller    : {', '.join(active) if active else '—'}")
    print(f"  Devre dışı         : {', '.join(disabled) if disabled else '—'}")
    print()

    if state["positions"]:
        print(f"  {'Sembol':<14} {'Giriş':>10} {'Güncel':>10} {'PnL%':>7} {'TP':>10} {'SL':>10} {'Trail':>6}")
        print("  " + "-" * 70)
        for sym, pos in state["positions"].items():
            cur   = prices.get(sym, pos["entry_price"])
            pnl_p = (cur - pos["entry_price"]) / pos["entry_price"] * 100
            sl    = pos["trail_sl"] if pos["trail_active"] else pos["sl"]
            trail = "✓" if pos["trail_active"] else "—"
            print(
                f"  {sym:<14} {pos['entry_price']:>10.4f} {cur:>10.4f} "
                f"{pnl_p:>+6.2f}% {pos['tp']:>10.4f} {sl:>10.4f} {trail:>6}"
            )
    else:
        print("  Açık pozisyon yok.")

    print()
    print(f"  {'Sembol':<14} {'Trade':>5} {'W':>3} {'L':>3} {'WR%':>6} {'PnL':>9} {'Durum':>10}")
    print("  " + "-" * 55)
    for sym in APPROVED_SYMBOLS:
        w   = state["sym_wins"].get(sym, 0)
        l   = state["sym_losses"].get(sym, 0)
        t   = w + l
        wr  = (w / t * 100) if t > 0 else 0.0
        pnl = state["sym_pnl"].get(sym, 0.0)
        st  = "DISABLED" if sym in disabled else "active"
        print(f"  {sym:<14} {t:>5} {w:>3} {l:>3} {wr:>5.1f}% {pnl:>+9.2f} {st:>10}")

    trade_log    = state.get("trade_log", [])
    total_closed = len([t for t in trade_log if t["action"] == "CLOSE"])
    equity_log   = state.get("equity_log", [])
    sessions     = len(equity_log)

    # Trade frequency — son 7 gün içindeki kapalı trade sayısı
    from datetime import timedelta
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    trades_7d = len([t for t in trade_log if t["action"] == "CLOSE" and t.get("time", "") >= cutoff_7d])

    print()
    print(f"  Toplam tamamlanan trade : {total_closed}")
    print(f"  Son 7 günde trade       : {trades_7d}")
    print(f"  Çalışma sayısı (session): {sessions}")
    if sessions >= 2:
        first_eq = equity_log[0]["equity"]
        total_ret = (equity - first_eq) / first_eq * 100 if first_eq > 0 else 0.0
        print(f"  Başlangıçtan getiri     : {total_ret:+.2f}%")
    print("═" * 62 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="breakout_v2 paper trader")
    p.add_argument("--status", action="store_true", help="Sadece durum göster, işlem yapma")
    args = p.parse_args()
    run(status_only=args.status)


if __name__ == "__main__":
    main()
