"""
Real-money risk control layer.

Paper trader ve gelecekteki live trader tarafından paylaşılır.
Strateji mantığından tamamen bağımsızdır.
"""

from dataclasses import dataclass

# ── Sabitler (ortam değişkeniyle ezilebilir) ─────────────────────────────────
RISK_PCT_PER_TRADE    = 0.015   # trade başına max equity riski (%1.5)
MAX_EXPOSURE_PCT      = 0.50    # toplam açık pozisyon / equity üst sınırı (%50)
DAILY_LOSS_GUARD_PCT  = 0.02    # günlük kayıp limiti (%2 equity — realized + unrealized)
MIN_TRADE_NOTIONAL    = 10.0    # USDT cinsinden minimum işlem büyüklüğü (Binance limiti)
TAKER_FEE_PCT         = 0.001   # Binance taker ücreti (%0.1)


# ── Pozisyon boyutlandırma ────────────────────────────────────────────────────

@dataclass
class SizingResult:
    allowed: bool
    size_usdt: float
    quantity: float
    reason: str = ""


def compute_position_size(
    equity: float,
    cash: float,
    price: float,
    current_allocated_usdt: float,
    stop_loss_pct: float,
    risk_pct: float = RISK_PCT_PER_TRADE,
    max_exposure_pct: float = MAX_EXPOSURE_PCT,
) -> SizingResult:
    """
    Risk tabanlı pozisyon boyutlandırma.

    Mantık:
      risk_amount   = equity * risk_pct           → kaybetmeyi göze aldığımız tutar
      max_by_risk   = risk_amount / stop_loss_pct → bu kayıpla tutarlı max boyut
      max_by_expose = equity * max_exposure - current_allocated
      size_usdt     = min(max_by_risk, cash, max_by_expose)

    Örnek: equity=10.000, risk=%1, SL=%1
      risk_amount  = 100 USDT
      max_by_risk  = 100 / 0.01 = 10.000 USDT
      max_by_expose = 10.000 * 0.50 - 0 = 5.000 USDT
      → size = 5.000 USDT  (SL'de gerçek kayıp = 50 USDT = %0.5 equity)
    """
    if price <= 0 or equity <= 0:
        return SizingResult(False, 0.0, 0.0, "Geçersiz fiyat veya equity")
    if stop_loss_pct <= 0:
        return SizingResult(False, 0.0, 0.0, "Geçersiz stop_loss_pct")

    risk_amount     = equity * risk_pct
    max_by_risk     = risk_amount / stop_loss_pct
    max_by_exposure = max(0.0, equity * max_exposure_pct - current_allocated_usdt)
    size_usdt       = min(max_by_risk, cash, max_by_exposure)

    if size_usdt < MIN_TRADE_NOTIONAL:
        return SizingResult(
            False, size_usdt, 0.0,
            f"Boyut çok küçük: {size_usdt:.2f} USDT < minimum {MIN_TRADE_NOTIONAL} USDT"
        )

    # Round-trip fee sanity check: ücret risk tutarının %30'unu geçmemeli
    round_trip_fee = size_usdt * TAKER_FEE_PCT * 2
    if round_trip_fee > risk_amount * 0.30:
        return SizingResult(
            False, size_usdt, 0.0,
            f"Ücret ({round_trip_fee:.2f} USDT) risk bütçesinin %30'unu aşıyor"
        )

    quantity = size_usdt / price
    return SizingResult(True, size_usdt, quantity)


# ── Günlük kayıp koruması ────────────────────────────────────────────────────

@dataclass
class DailyGuardResult:
    blocked: bool
    reason: str = ""
    loss_pct: float = 0.0


def check_daily_loss_guard(
    daily_start_equity: float,
    daily_realized_loss: float,   # sadece negatif trade'lerin abs değeri toplamı
    unrealized_pnl: float,        # açık pozisyonların anlık PnL (negatif = zarar)
    guard_pct: float = DAILY_LOSS_GUARD_PCT,
) -> DailyGuardResult:
    """
    Realized + unrealized kayıp birlikte %2'ye ulaşırsa yeni trade açmayı durdur.
    Unrealized kârlar sayılmaz — sadece zararlar korunmaya dahil edilir.
    """
    if daily_start_equity <= 0:
        return DailyGuardResult(False)

    unrealized_loss = max(0.0, -unrealized_pnl)   # kâr ise 0, zarar ise pozitif
    total_loss      = daily_realized_loss + unrealized_loss
    limit           = daily_start_equity * guard_pct
    loss_pct        = total_loss / daily_start_equity * 100

    if total_loss >= limit:
        return DailyGuardResult(
            True,
            f"Günlük kayıp limiti: {total_loss:.2f} USDT "
            f"(≥ {limit:.2f} USDT = %{guard_pct*100:.0f} limit, şu an %{loss_pct:.2f})",
            loss_pct,
        )
    return DailyGuardResult(False, loss_pct=loss_pct)


# ── Exposure kontrolü ─────────────────────────────────────────────────────────

def allocated_usdt(positions: dict) -> float:
    """Açık pozisyonların giriş fiyatından toplam alokasyonu hesapla."""
    return sum(p["entry_price"] * p["quantity"] for p in positions.values())


def unrealized_pnl(positions: dict, prices: dict[str, float]) -> float:
    """Açık pozisyonların anlık unrealized PnL'ini hesapla."""
    total = 0.0
    for sym, pos in positions.items():
        cur = prices.get(sym, pos["entry_price"])
        total += (cur - pos["entry_price"]) * pos["quantity"]
    return total
