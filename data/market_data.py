import logging
import time

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

_RETRY_MAX = 3
_RETRY_DELAY = 1  # saniye


class MarketDataError(Exception):
    pass


def _default_exchange() -> ccxt.Exchange:
    return ccxt.binance({"enableRateLimit": True})


def _normalize(raw: list) -> pd.DataFrame:
    if not raw:
        raise MarketDataError("Boş OHLCV verisi alındı — exchange veri döndürmedi.")

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).astype("datetime64[ns, UTC]")
    df = df.dropna()
    df = df.drop_duplicates(subset="timestamp", keep="first")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_ohlcv_range(
    symbol: str,
    timeframe: str,
    since: int,
    until: int,
    exchange: ccxt.Exchange | None = None,
    chunk_size: int = 500,
    max_iterations: int = 200,
) -> pd.DataFrame:
    """
    since..until aralığında tüm OHLCV barlarını sayfalayarak çeker.

    since / until: epoch millisaniye (UTC)
    max_iterations: sonsuz döngü koruması
    """
    if exchange is None:
        exchange = _default_exchange()

    all_candles: list = []
    current_since = since
    iteration = 0

    while current_since < until:
        if iteration >= max_iterations:
            raise MarketDataError(
                f"fetch_ohlcv_range: max_iterations={max_iterations} aşıldı — "
                "sonsuz döngü koruması devreye girdi."
            )

        last_error: Exception | None = None
        chunk = None
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                chunk = exchange.fetch_ohlcv(
                    symbol, timeframe,
                    since=current_since,
                    limit=chunk_size,
                )
                break
            except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                last_error = exc
                logger.warning("Ağ hatası (deneme %d/%d): %s", attempt, _RETRY_MAX, exc)
                if attempt < _RETRY_MAX:
                    time.sleep(_RETRY_DELAY)

        if chunk is None:
            raise MarketDataError(
                f"fetch_ohlcv_range: {_RETRY_MAX} denemeden sonra veri alınamadı — {last_error}"
            )

        if not chunk:
            break  # veri tükendi

        # Sonsuz döngü koruması: since ilerlemiyorsa dur
        new_since = chunk[-1][0] + 1
        if new_since <= current_since:
            raise MarketDataError(
                f"fetch_ohlcv_range: max_iterations={max_iterations} aşıldı — "
                "sonsuz döngü koruması devreye girdi."
            )

        all_candles.extend(chunk)
        current_since = new_since
        iteration += 1

        logger.debug(
            "fetch_ohlcv_range | %s | chunk=%d | toplam=%d | iter=%d",
            symbol, len(chunk), len(all_candles), iteration,
        )

    if not all_candles:
        raise MarketDataError(f"fetch_ohlcv_range: {symbol} için veri alınamadı.")

    df = _normalize(all_candles)

    # until sonrasını kes
    until_ts = pd.Timestamp(until, unit="ms", tz="UTC")
    df = df[df["timestamp"] <= until_ts].reset_index(drop=True)

    logger.info(
        "fetch_ohlcv_range tamamlandı | %s | %d bar | %d iterasyon",
        symbol, len(df), iteration,
    )
    return df


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int = 500,
    exchange: ccxt.Exchange | None = None,
) -> pd.DataFrame:
    if exchange is None:
        exchange = _default_exchange()

    last_error: Exception | None = None
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            logger.debug("fetch_ohlcv | %s %s | deneme %d", symbol, timeframe, attempt)
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = _normalize(raw)
            logger.info("fetch_ohlcv başarılı | %s | %d bar", symbol, len(df))
            return df
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            last_error = exc
            logger.warning("Ağ hatası (deneme %d/%d): %s", attempt, _RETRY_MAX, exc)
            if attempt < _RETRY_MAX:
                time.sleep(_RETRY_DELAY)
        except MarketDataError:
            raise

    raise MarketDataError(
        f"Ağ hatası: {_RETRY_MAX} denemeden sonra veri alınamadı — {last_error}"
    )
