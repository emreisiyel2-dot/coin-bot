"""
Multi-symbol backtest CLI runner.

Kullanım:
    # Smoke run (2 coin, 30 gün, swing)
    python -m backtest.runner --strategy swing --days 30 --smoke

    # Full run (8 coin, 90 gün, swing)
    python -m backtest.runner --strategy swing --days 90

    # Full run (8 coin, 180 gün, scalp)
    python -m backtest.runner --strategy scalp --days 180

    # Belirli coinler
    python -m backtest.runner --strategy swing --days 90 --symbols BTC/USDT ETH/USDT
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest.data_loader import DataLoader
from backtest.multi_backtest import MultiBacktest
from config.settings import (
    BREAKOUT_CONFIG,
    BREAKOUT_V2_CONFIG,
    PULLBACK_CONFIG,
    RISK_CONFIG,
    SCANNER_CONFIG,
    STRATEGY_CONFIG,
    TRADING_CONFIG,
    UNIVERSE_SYMBOLS,
)
from execution.simulator import ExecutionSimulator
from storage.database import Database
from strategy.breakout import MomentumBreakoutStrategy
from strategy.breakout_v2 import MomentumBreakoutV2Strategy
from strategy.pullback import TrendPullbackStrategy
from strategy.swing import SwingStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Smoke run sabitleri ───────────────────────────────────────────────────────
SMOKE_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
SMOKE_DAYS = 30

# ── Timeframe → bar sayısı / gün ─────────────────────────────────────────────
BARS_PER_DAY = {"1h": 24, "4h": 6, "1d": 1, "5m": 288, "15m": 96}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-symbol paper trading backtest")
    p.add_argument("--strategy", choices=["swing", "scalp", "breakout", "pullback", "breakout_v2"], default="swing")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--smoke", action="store_true", help="2 coin / 30 gün hızlı test")
    p.add_argument("--symbols", nargs="+", default=None, help="Coin listesi (default: UNIVERSE)")
    p.add_argument("--cash", type=float, default=10_000.0)
    p.add_argument("--db", default="backtest.db", help="SQLite dosyası")
    p.add_argument("--no-db", action="store_true", help="DB'ye yazma")
    # Exit parametre override'ları (config değerlerini ezer)
    p.add_argument("--take-profit",       type=float, default=None, help="TP yüzdesi (örn: 0.03)")
    p.add_argument("--stop-loss",         type=float, default=None, help="SL yüzdesi (örn: 0.01)")
    p.add_argument("--partial-tp",        type=float, default=None, help="Partial TP1 yüzdesi (0.0 = kapalı)")
    p.add_argument("--trailing-activate", type=float, default=None, help="Trailing aktivasyon eşiği (örn: 0.015)")
    p.add_argument("--trailing-stop",     type=float, default=None, help="Trailing stop mesafesi (örn: 0.01)")
    # Entry parametre override'ları (sadece breakout_v2)
    p.add_argument("--rsi-min",           type=float, default=None, help="RSI minimum eşiği (örn: 60)")
    p.add_argument("--vol-multiplier",    type=float, default=None, help="Hacim çarpanı (örn: 2.0)")
    p.add_argument("--breakout-margin",   type=float, default=None, help="Breakout margin %'si (örn: 0.002)")
    p.add_argument("--adx-min",           type=float, default=None, help="ADX minimum eşiği (örn: 25)")
    p.add_argument("--timeframe",         type=str,   default=None, help="Timeframe override (örn: 4h, 1h, 15m)")
    return p.parse_args()


def _make_strategy_factory(strategy: str, entry_overrides: dict | None = None, timeframe_override: str | None = None):
    if strategy == "swing":
        def factory():
            return SwingStrategy(config=STRATEGY_CONFIG)
        return factory, "swing_1h", TRADING_CONFIG["timeframe"]

    if strategy == "scalp":
        try:
            from strategy.scalp import ScalpStrategy
            from config.settings import SCALPING_CONFIG as SC
            def factory():
                return ScalpStrategy(config=SC)
            return factory, "scalp_5m", "5m"
        except ImportError:
            logger.error("ScalpStrategy henüz implement edilmedi. Swing kullanılıyor.")
            return _make_strategy_factory("swing")

    if strategy == "breakout":
        def factory():
            return MomentumBreakoutStrategy(config=BREAKOUT_CONFIG)
        return factory, "breakout_1h", TRADING_CONFIG["timeframe"]

    if strategy == "pullback":
        def factory():
            return TrendPullbackStrategy(config=PULLBACK_CONFIG)
        return factory, "pullback_1h", TRADING_CONFIG["timeframe"]

    if strategy == "breakout_v2":
        cfg = {**BREAKOUT_V2_CONFIG, **(entry_overrides or {})}
        tf = timeframe_override or TRADING_CONFIG["timeframe"]
        def factory():
            return MomentumBreakoutV2Strategy(config=cfg)
        return factory, f"breakout_v2_{tf}", tf

    raise ValueError(f"Bilinmeyen strateji: {strategy}")


def main() -> None:
    args = _parse_args()

    # ── Parametreler ──────────────────────────────────────────────────────────
    if args.smoke:
        symbols = SMOKE_SYMBOLS
        days = SMOKE_DAYS
        logger.info("SMOKE RUN: %d coin / %d gün", len(symbols), days)
    else:
        symbols = args.symbols or UNIVERSE_SYMBOLS
        days = args.days

    # Entry override'ları (breakout_v2 için)
    entry_overrides: dict = {}
    if args.rsi_min         is not None: entry_overrides["rsi_min"]            = args.rsi_min
    if args.vol_multiplier  is not None: entry_overrides["vol_multiplier"]     = args.vol_multiplier
    if args.breakout_margin is not None: entry_overrides["breakout_margin_pct"] = args.breakout_margin
    if args.adx_min         is not None: entry_overrides["adx_min"]            = args.adx_min

    strategy_factory, strategy_name, timeframe = _make_strategy_factory(args.strategy, entry_overrides, args.timeframe)

    # TP/SL + partial exit her strateji için kendi config'inden gelir
    # (take_profit_pct, stop_loss_pct, partial_tp1_pct, partial_tp1_size, trailing_stop_pct, trailing_activate_pct)
    _exit_map = {
        "swing":    (STRATEGY_CONFIG.get("take_profit_pct", 0.02), STRATEGY_CONFIG.get("stop_loss_pct", 0.01), 0.0, 0.5, 0.0, 0.0),
        "breakout": (BREAKOUT_CONFIG["take_profit_pct"], BREAKOUT_CONFIG["stop_loss_pct"], 0.0, 0.5, 0.0, 0.0),
        "pullback": (
            PULLBACK_CONFIG["take_profit_pct"],
            PULLBACK_CONFIG["stop_loss_pct"],
            PULLBACK_CONFIG["partial_tp1_pct"],
            PULLBACK_CONFIG["partial_tp1_size"],
            PULLBACK_CONFIG["trailing_stop_pct"],
            0.0,
        ),
        "breakout_v2": (
            BREAKOUT_V2_CONFIG["take_profit_pct"],
            BREAKOUT_V2_CONFIG["stop_loss_pct"],
            BREAKOUT_V2_CONFIG["partial_tp1_pct"],
            BREAKOUT_V2_CONFIG["partial_tp1_size"],
            BREAKOUT_V2_CONFIG["trailing_stop_pct"],
            BREAKOUT_V2_CONFIG["trailing_activate_pct"],
        ),
        "scalp":    (0.024, 0.012, 0.0, 0.5, 0.0, 0.0),
    }
    take_profit_pct, stop_loss_pct, partial_tp1_pct, partial_tp1_size, trailing_stop_pct, trailing_activate_pct = \
        _exit_map.get(args.strategy, (0.02, 0.01, 0.0, 0.5, 0.0, 0.0))

    # CLI override'ları uygula
    if args.take_profit       is not None: take_profit_pct      = args.take_profit
    if args.stop_loss         is not None: stop_loss_pct        = args.stop_loss
    if args.partial_tp        is not None: partial_tp1_pct      = args.partial_tp
    if args.trailing_activate is not None: trailing_activate_pct = args.trailing_activate
    if args.trailing_stop     is not None: trailing_stop_pct    = args.trailing_stop

    until_dt = datetime.now(timezone.utc)
    since_dt = until_dt - timedelta(days=days)
    since_ms = int(since_dt.timestamp() * 1000)
    until_ms = int(until_dt.timestamp() * 1000)

    logger.info(
        "Backtest parametreleri | strateji=%s | timeframe=%s | "
        "dönem=%s → %s | coinler=%s",
        strategy_name, timeframe,
        since_dt.strftime("%Y-%m-%d"), until_dt.strftime("%Y-%m-%d"),
        ", ".join(symbols),
    )

    # ── Veri çekme ────────────────────────────────────────────────────────────
    loader = DataLoader(timeframe=timeframe)
    print(f"\nVeri çekiliyor: {len(symbols)} coin × {days} gün × {timeframe}...")

    try:
        data_dict, alignment_report = loader.load(symbols, since=since_ms, until=until_ms)
    except Exception as exc:
        logger.error("Veri çekme hatası: %s", exc)
        sys.exit(1)

    alignment_report.print_summary()

    if not data_dict:
        logger.error("Hiç veri alınamadı. Çıkılıyor.")
        sys.exit(1)

    # ── Backtest ──────────────────────────────────────────────────────────────
    db = None if args.no_db else Database(args.db)

    simulator = ExecutionSimulator()

    backtest = MultiBacktest(
        strategy_factory=strategy_factory,
        strategy_name=strategy_name,
        initial_cash=args.cash,
        position_size_pct=0.10,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        partial_tp1_pct=partial_tp1_pct,
        partial_tp1_size=partial_tp1_size,
        trailing_stop_pct=trailing_stop_pct,
        trailing_activate_pct=trailing_activate_pct,
        scanner_config=SCANNER_CONFIG,
        risk_config=RISK_CONFIG,
        simulator=simulator,
        db=db,
    )

    print(f"Backtest çalışıyor: {len(data_dict)} coin, {next(iter(data_dict.values())).__len__()} bar...")

    try:
        result = backtest.run(data_dict)
    except Exception as exc:
        logger.error("Backtest hatası: %s", exc, exc_info=True)
        if db:
            db.close()
        sys.exit(1)

    result.print_summary()

    if db:
        print(f"Sonuçlar kaydedildi: {args.db} (run_id={result.run_id})")
        db.close()


if __name__ == "__main__":
    main()
