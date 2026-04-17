import logging
from dataclasses import dataclass, field

import pandas as pd

from data.market_data import fetch_ohlcv_range

logger = logging.getLogger(__name__)


class DataLoaderError(Exception):
    pass


@dataclass
class AlignmentReport:
    bars_lost: int
    original_bars: dict[str, int]   # {symbol: bar sayısı, fetch sonrası}
    aligned_bars: int               # inner join sonrası ortak bar sayısı
    symbol_bar_counts: dict[str, int]
    narrowing_symbols: list[str]    # veri daraltmasına neden olan semboller

    def print_summary(self) -> None:
        print("\n── Veri Alignment Raporu ───────────────────────────")
        print(f"  Ortak bar sayısı  : {self.aligned_bars}")
        print(f"  Kaybedilen bar    : {self.bars_lost}")
        if self.narrowing_symbols:
            print(f"  Daraltan semboller: {', '.join(self.narrowing_symbols)}")
        print(f"\n  Sembol bazlı bar sayıları:")
        for sym, count in sorted(self.symbol_bar_counts.items()):
            marker = " ◄ daralma" if sym in self.narrowing_symbols else ""
            print(f"    {sym:<16}: {count}{marker}")
        print()


class DataLoader:
    """
    Multi-symbol OHLCV verisi çekme ve timestamp alignment.

    fetch → align → {symbol: df} döner.
    """

    def __init__(
        self,
        timeframe: str = "1h",
        chunk_size: int = 500,
        max_iterations: int = 200,
        exchange=None,
    ) -> None:
        self._timeframe = timeframe
        self._chunk_size = chunk_size
        self._max_iterations = max_iterations
        self._exchange = exchange  # None → ccxt.binance

    def load(
        self,
        symbols: list[str],
        since: int,
        until: int,
    ) -> tuple[dict[str, pd.DataFrame], AlignmentReport]:
        """
        Tüm sembolleri çeker ve timestamp'e göre inner-join ile hizalar.

        Döner: (aligned_dfs, report)
        """
        raw_dfs: dict[str, pd.DataFrame] = {}

        for symbol in symbols:
            logger.info("Veri çekiliyor: %s %s", symbol, self._timeframe)
            df = fetch_ohlcv_range(
                symbol, self._timeframe,
                since=since,
                until=until,
                exchange=self._exchange,
                chunk_size=self._chunk_size,
                max_iterations=self._max_iterations,
            )
            raw_dfs[symbol] = df
            logger.info("%s: %d bar", symbol, len(df))

        return self._align(raw_dfs)

    def _align(
        self,
        dfs: dict[str, pd.DataFrame],
    ) -> tuple[dict[str, pd.DataFrame], AlignmentReport]:
        """
        Tüm DataFrames'i ortak timestamp kümesine (inner join) hizala.
        Hangi sembolün veriyi daralttığını rapor eder.
        """
        if not dfs:
            raise DataLoaderError("_align: boş DataFrame dict.")

        original_bars = {sym: len(df) for sym, df in dfs.items()}

        # Her sembol için timestamp kümesi
        ts_sets: dict[str, set] = {
            sym: set(df["timestamp"].tolist())
            for sym, df in dfs.items()
        }

        # Inner join: tüm sembollerde ortak olan timestamp'ler
        common_ts = set.intersection(*ts_sets.values())

        if not common_ts:
            raise DataLoaderError(
                "Semboller arasında ortak timestamp bulunamadı — "
                "veri aralıkları örtüşmüyor."
            )

        common_sorted = sorted(common_ts)
        aligned_bars = len(common_sorted)
        common_set = set(common_sorted)

        # Hangi semboller daralma yaptı?
        max_original = max(original_bars.values())
        narrowing_symbols = [
            sym for sym, ts_set in ts_sets.items()
            if len(ts_set & common_set) < max_original
            and len(ts_set) < max_original
        ]

        # Her DataFrame'i sadece ortak timestamp'lere filtrele
        aligned: dict[str, pd.DataFrame] = {}
        symbol_bar_counts: dict[str, int] = {}
        for sym, df in dfs.items():
            filtered = df[df["timestamp"].isin(common_set)].reset_index(drop=True)
            aligned[sym] = filtered
            symbol_bar_counts[sym] = len(filtered)

        bars_lost = max_original - aligned_bars

        report = AlignmentReport(
            bars_lost=bars_lost,
            original_bars=original_bars,
            aligned_bars=aligned_bars,
            symbol_bar_counts=symbol_bar_counts,
            narrowing_symbols=narrowing_symbols,
        )

        logger.info(
            "Alignment tamamlandı | ortak=%d bar | kaybedilen=%d | daraltan=%s",
            aligned_bars, bars_lost,
            ", ".join(narrowing_symbols) if narrowing_symbols else "yok",
        )

        return aligned, report
