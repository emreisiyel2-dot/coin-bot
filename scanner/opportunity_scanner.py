import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from indicators.indicators import compute_rsi, compute_atr
from strategy.signals import Signal

logger = logging.getLogger(__name__)


@dataclass
class Opportunity:
    symbol: str
    signal: Signal
    score: float                        # 0.0 – 1.0, clamp edilmiş
    score_breakdown: dict[str, float]   # {"rsi_depth", "atr", "volume", "spread_penalty"}
    reason: str


class OpportunityScanner:
    """
    Multi-symbol fırsat tarayıcısı.

    Her symbol için:
      1. Strategy sinyali BUY değilse → elensin
      2. Volume filtresi (rolling avg) → yetersizse elensin
      3. ATR filtresi → volatilite çok düşükse elensin
      4. Skor hesapla: RSI derinliği + ATR normalize + Volume normalize
      5. Spread cezası uygula
      6. Skora göre sırala → top_n döndür

    Tasarım: Strategy'den tamamen bağımsız; sadece sinyali alır.
    """

    def __init__(self, config: dict) -> None:
        self._min_atr_pct: float = config.get("min_atr_pct", 0.005)
        self._min_volume: float = config.get("min_volume", 1_000_000.0)
        self._volume_window: int = config.get("volume_window", 20)
        self._spread_penalty_threshold: float = config.get("spread_penalty_threshold", 0.002)
        self._rsi_weight: float = config.get("rsi_weight", 0.50)
        self._atr_weight: float = config.get("atr_weight", 0.30)
        self._volume_weight: float = config.get("volume_weight", 0.20)
        self._top_n: int = config.get("top_n", 3)

    def scan(
        self,
        candidates: dict,  # {symbol: (strategy, df)}
        spread_overrides: dict[str, float] | None = None,
    ) -> list[Opportunity]:
        """
        candidates: {symbol: (strategy_instance, df_slice)}
        spread_overrides: {symbol: spread_pct} — test ve backteste override için
        """
        if not candidates:
            return []

        spread_overrides = spread_overrides or {}
        opportunities: list[Opportunity] = []

        for symbol, (strategy, df) in candidates.items():
            opp = self._evaluate(symbol, strategy, df, spread_overrides.get(symbol, 0.0))
            if opp is not None:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.score, reverse=True)
        return opportunities[: self._top_n]

    # ── İç değerlendirme ─────────────────────────────────────────────────────

    def _evaluate(
        self,
        symbol: str,
        strategy,
        df: pd.DataFrame,
        spread_pct: float,
    ) -> Opportunity | None:
        # 1. Sinyal kontrolü
        try:
            signal = strategy.generate_signal(df)
        except (ValueError, Exception) as exc:
            logger.debug("Scanner: %s sinyal hatası — %s", symbol, exc)
            return None

        if signal != Signal.BUY:
            return None

        # 2. Volume filtresi (rolling avg)
        if not self._passes_volume(df):
            logger.debug("Scanner: %s volume filtresi geçemedi", symbol)
            return None

        # 3. ATR filtresi
        atr_pct = self._compute_atr_pct(df)
        if atr_pct is None or atr_pct < self._min_atr_pct:
            logger.debug("Scanner: %s ATR filtresi geçemedi (%.4f < %.4f)", symbol, atr_pct or 0, self._min_atr_pct)
            return None

        # 4. Skor bileşenleri
        rsi_score = self._rsi_score(df)
        atr_score = self._atr_score(atr_pct)
        vol_score = self._volume_score(df)

        raw_score = (
            self._rsi_weight * rsi_score
            + self._atr_weight * atr_score
            + self._volume_weight * vol_score
        )

        # 5. Spread cezası
        penalty = self._spread_penalty(spread_pct)
        final_score = float(np.clip(raw_score - penalty, 0.0, 1.0))

        breakdown = {
            "rsi_depth": round(rsi_score, 4),
            "atr": round(atr_score, 4),
            "volume": round(vol_score, 4),
            "spread_penalty": round(penalty, 4),
        }

        reason = (
            f"rsi={rsi_score:.2f} atr={atr_score:.2f} "
            f"vol={vol_score:.2f} penalty={penalty:.3f} → score={final_score:.3f}"
        )

        return Opportunity(
            symbol=symbol,
            signal=signal,
            score=final_score,
            score_breakdown=breakdown,
            reason=reason,
        )

    # ── Filtreler ─────────────────────────────────────────────────────────────

    def _passes_volume(self, df: pd.DataFrame) -> bool:
        # volume Binance'te base currency — USDT'ye çevir (volume * close)
        window = min(self._volume_window, len(df))
        tail = df.iloc[-window:]
        rolling_avg_usdt = (tail["volume"] * tail["close"]).mean()
        return float(rolling_avg_usdt) >= self._min_volume

    def _compute_atr_pct(self, df: pd.DataFrame) -> float | None:
        try:
            atr_series = compute_atr(df, period=14)
            atr_val = float(atr_series.iloc[-1])
            if np.isnan(atr_val):
                return None
            close = float(df["close"].iloc[-1])
            return atr_val / close if close > 0 else None
        except (ValueError, Exception):
            return None

    # ── Skor fonksiyonları (0–1 normalize) ───────────────────────────────────

    def _rsi_score(self, df: pd.DataFrame) -> float:
        """LONG için: (30 - rsi) / 30 — RSI ne kadar düşük, skor o kadar yüksek."""
        try:
            rsi_series = compute_rsi(df, period=14)
            rsi = float(rsi_series.iloc[-1])
            if np.isnan(rsi):
                return 0.0
            # RSI 30'un altı oversold; 30'un üstü negatif skor → clamp 0'a
            score = (30.0 - rsi) / 30.0
            return float(np.clip(score, 0.0, 1.0))
        except (ValueError, Exception):
            return 0.0

    def _atr_score(self, atr_pct: float) -> float:
        """ATR yükseldikçe skor artar; %5 üstü maksimum."""
        score = atr_pct / 0.05
        return float(np.clip(score, 0.0, 1.0))

    def _volume_score(self, df: pd.DataFrame) -> float:
        """Rolling avg USDT volume / (min_volume * 10) → normalize."""
        window = min(self._volume_window, len(df))
        tail = df.iloc[-window:]
        rolling_avg_usdt = float((tail["volume"] * tail["close"]).mean())
        target = self._min_volume * 10.0
        score = rolling_avg_usdt / target if target > 0 else 0.0
        return float(np.clip(score, 0.0, 1.0))

    def _spread_penalty(self, spread_pct: float) -> float:
        """Eşik üstündeki spread ceza olarak skora eklenir; max 0.5 ceza."""
        if spread_pct <= self._spread_penalty_threshold:
            return 0.0
        excess = spread_pct - self._spread_penalty_threshold
        return float(np.clip(excess * 50, 0.0, 0.5))
