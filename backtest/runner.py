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
    RISK_CONFIG,
    SCANNER_CONFIG,
    STRATEGY_CONFIG,
    TRADING_CONFIG,
    UNIVERSE_SYMBOLS,
)
from execution.simulator import ExecutionSimulator
from storage.database import Database
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
    p.add_argument("--strategy", choices=["swing", "scalp"], default="swing")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--smoke", action="store_true", help="2 coin / 30 gün hızlı test")
    p.add_argument("--symbols", nargs="+", default=None, help="Coin listesi (default: UNIVERSE)")
    p.add_argument("--cash", type=float, default=10_000.0)
    p.add_argument("--db", default="backtest.db", help="SQLite dosyası")
    p.add_argument("--no-db", action="store_true", help="DB'ye yazma")
    return p.parse_args()


def _make_strategy_factory(strategy: str):
    if strategy == "swing":
        def factory():
            return SwingStrategy(config=STRATEGY_CONFIG)
        return factory, "swing_1h", TRADING_CONFIG["timeframe"]

    if strategy == "scalp":
        # Scalp için import burada — modül hazır olduğunda
        try:
            from strategy.scalp import ScalpStrategy
            from config.settings import SCALPING_CONFIG as SC
            def factory():
                return ScalpStrategy(config=SC)
            return factory, "scalp_5m", "5m"
        except ImportError:
            logger.error("ScalpStrategy henüz implement edilmedi. Swing kullanılıyor.")
            return _make_strategy_factory("swing")

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

    strategy_factory, strategy_name, timeframe = _make_strategy_factory(args.strategy)

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
