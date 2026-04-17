import logging
from dataclasses import dataclass, field

import pandas as pd

from portfolio.portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    code: str  # "", "MAX_POS", "DUPLICATE", "COOLDOWN", "DAILY_LOSS", "DAILY_TRADE_LIMIT"


_ALLOW = RiskDecision(allowed=True, reason="", code="")


class RiskManager:
    """
    Trade açılmadan önce son kontrol katmanı.

    Strategy BUY dese bile bu sınıf veto edebilir.
    Engine'e inject edilir; Strategy'den tamamen bağımsızdır.

    Günlük sayaçlar (realized_loss, trade_count) tarih değişimini
    otomatik algılar — dışarıdan reset_daily() çağrısı gerekmez.
    """

    def __init__(self, portfolio: Portfolio, config: dict) -> None:
        self._portfolio = portfolio
        self._max_open_positions: int = config.get("max_open_positions", 3)
        self._cooldown_bars: int = config.get("cooldown_bars", 5)
        self._daily_max_trades: int = config.get("daily_max_trades", 10)
        self._daily_max_loss_pct: float = config.get("daily_max_loss_pct", 0.03)

        # Symbol → son kapanış bar index (cooldown için)
        self._last_close_bar: dict[str, int] = {}

        # Günlük takip
        self._current_date: pd.Timestamp | None = None
        self._daily_realized_loss: float = 0.0  # sadece negatif PnL toplanır
        self._daily_trade_count: int = 0         # round-trip (CLOSE) sayısı
        self._daily_start_equity: float = 0.0

    # ── Ana kontrol ───────────────────────────────────────────────────────────

    def can_open(
        self,
        symbol: str,
        bar_index: int,
        bar_ts: pd.Timestamp,
    ) -> RiskDecision:
        """Trade açılabilir mi? İzin verirse RiskDecision(allowed=True), yoksa veto + sebep."""
        self._maybe_reset_daily(bar_ts)

        # 1. Duplicate kontrolü (max_pos'tan önce — daha spesifik hata kodu)
        if symbol in self._portfolio.positions:
            return RiskDecision(
                allowed=False,
                reason=f"{symbol} için zaten açık pozisyon var",
                code="DUPLICATE",
            )

        # 2. Max açık pozisyon
        if len(self._portfolio.positions) >= self._max_open_positions:
            return RiskDecision(
                allowed=False,
                reason=(
                    f"Maksimum açık pozisyon limitine ulaşıldı "
                    f"({len(self._portfolio.positions)}/{self._max_open_positions})"
                ),
                code="MAX_POS",
            )

        # 3. Symbol cooldown
        last_close = self._last_close_bar.get(symbol)
        if last_close is not None and (bar_index - last_close) < self._cooldown_bars:
            bars_left = self._cooldown_bars - (bar_index - last_close)
            return RiskDecision(
                allowed=False,
                reason=f"{symbol} cooldown aktif — {bars_left} bar daha beklenmeli",
                code="COOLDOWN",
            )

        # 4. Günlük max loss
        loss_limit = self._daily_start_equity * self._daily_max_loss_pct
        if self._daily_realized_loss >= loss_limit:
            return RiskDecision(
                allowed=False,
                reason=(
                    f"Günlük kayıp limiti aşıldı "
                    f"({self._daily_realized_loss:.2f} >= {loss_limit:.2f})"
                ),
                code="DAILY_LOSS",
            )

        # 5. Günlük trade limiti
        if self._daily_trade_count >= self._daily_max_trades:
            return RiskDecision(
                allowed=False,
                reason=(
                    f"Günlük trade limiti doldu "
                    f"({self._daily_trade_count}/{self._daily_max_trades})"
                ),
                code="DAILY_TRADE_LIMIT",
            )

        return _ALLOW

    # ── Durum güncellemeleri ──────────────────────────────────────────────────

    def record_open(self, symbol: str, bar_index: int, bar_ts: pd.Timestamp) -> None:
        self._maybe_reset_daily(bar_ts)
        logger.debug("RiskManager: OPEN kaydedildi | %s | bar=%d", symbol, bar_index)

    def record_close(
        self,
        symbol: str,
        pnl: float,
        bar_index: int,
        bar_ts: pd.Timestamp,
    ) -> None:
        self._maybe_reset_daily(bar_ts)
        self._last_close_bar[symbol] = bar_index
        self._daily_trade_count += 1
        if pnl < 0:
            self._daily_realized_loss += abs(pnl)
        logger.debug(
            "RiskManager: CLOSE kaydedildi | %s | pnl=%.2f | günlük_kayıp=%.2f | günlük_trade=%d",
            symbol, pnl, self._daily_realized_loss, self._daily_trade_count,
        )

    # ── İç yardımcılar ───────────────────────────────────────────────────────

    def _maybe_reset_daily(self, bar_ts: pd.Timestamp) -> None:
        """Tarih değişmişse günlük sayaçları otomatik sıfırla."""
        today = bar_ts.normalize()  # gün başına yuvarla
        if self._current_date is None or today > self._current_date:
            self._current_date = today
            self._daily_realized_loss = 0.0
            self._daily_trade_count = 0
            self._daily_start_equity = self._portfolio.total_equity
            logger.info("RiskManager: Günlük sayaçlar sıfırlandı | tarih=%s | equity=%.2f", today, self._daily_start_equity)
