"""
TDD — Portfolio modülü testleri.

Beklenen davranışlar:
  - Pozisyon açma/kapatma nakit akışı
  - Realized / unrealized PnL doğruluğu
  - Total equity hesabı
  - Koruma davranışları (duplicate, nonexistent, yetersiz nakit)
  - Fiyat güncelleme → unrealized PnL yansıması

Sayısal beklentiler:
  commission_pct = 0.001, slippage_pct = 0.0  (varsayılan test fixture)
  entry_cost     = quantity * price * (1 + commission_pct)
  exit_proceeds  = quantity * price * (1 - commission_pct)
"""
import pytest

from portfolio.portfolio import Portfolio, PortfolioError

INITIAL_CASH = 10_000.0
COMM = 0.001   # %0.1
SLIP = 0.0     # testlerde basitlik için sıfır

SYM = "BTC/USDT"
PRICE_ENTRY = 50_000.0
QTY = 0.1

# Türetilmiş sabitler
ENTRY_COST = QTY * PRICE_ENTRY * (1 + COMM)     # 5005.0
CASH_AFTER_OPEN = INITIAL_CASH - ENTRY_COST      # 4995.0


@pytest.fixture
def portfolio():
    return Portfolio(initial_cash=INITIAL_CASH, commission_pct=COMM, slippage_pct=SLIP)


# ─── Pozisyon Açma ────────────────────────────────────────────────────────────

class TestOpenLong:

    def test_open_long_deducts_cash(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert pytest.approx(portfolio.cash, rel=1e-9) == CASH_AFTER_OPEN

    def test_open_long_creates_position(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert SYM in portfolio.positions

    def test_open_long_records_quantity(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert pytest.approx(portfolio.positions[SYM].quantity) == QTY

    def test_open_long_records_entry_price(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert pytest.approx(portfolio.positions[SYM].entry_price) == PRICE_ENTRY

    def test_open_long_records_entry_cost(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert pytest.approx(portfolio.positions[SYM].entry_cost) == ENTRY_COST

    def test_open_long_side_is_long(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        assert portfolio.positions[SYM].side == "LONG"

    def test_open_long_insufficient_cash_raises(self, portfolio):
        huge_qty = (INITIAL_CASH / PRICE_ENTRY) * 10  # çok pahalı
        with pytest.raises(PortfolioError, match="Yetersiz"):
            portfolio.open_long(SYM, PRICE_ENTRY, huge_qty)

    def test_open_long_duplicate_symbol_raises(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        with pytest.raises(PortfolioError, match="açık pozisyon"):
            portfolio.open_long(SYM, PRICE_ENTRY, QTY)


# ─── Pozisyon Kapatma ─────────────────────────────────────────────────────────

class TestCloseLong:

    def test_close_long_removes_position(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        portfolio.close_long(SYM, PRICE_ENTRY)
        assert SYM not in portfolio.positions

    def test_close_long_returns_realized_pnl(self, portfolio):
        PRICE_EXIT = 55_000.0
        exit_proceeds = QTY * PRICE_EXIT * (1 - COMM)   # 5494.5
        expected_rpnl = exit_proceeds - ENTRY_COST       # 489.5
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        rpnl = portfolio.close_long(SYM, PRICE_EXIT)
        assert pytest.approx(rpnl, rel=1e-9) == expected_rpnl

    def test_close_long_adds_proceeds_to_cash(self, portfolio):
        PRICE_EXIT = 55_000.0
        exit_proceeds = QTY * PRICE_EXIT * (1 - COMM)
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        portfolio.close_long(SYM, PRICE_EXIT)
        assert pytest.approx(portfolio.cash, rel=1e-9) == CASH_AFTER_OPEN + exit_proceeds

    def test_close_long_loss(self, portfolio):
        PRICE_EXIT = 45_000.0
        exit_proceeds = QTY * PRICE_EXIT * (1 - COMM)   # 4495.5
        expected_rpnl = exit_proceeds - ENTRY_COST       # -509.5
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        rpnl = portfolio.close_long(SYM, PRICE_EXIT)
        assert pytest.approx(rpnl, rel=1e-9) == expected_rpnl
        assert rpnl < 0

    def test_close_nonexistent_position_raises(self, portfolio):
        with pytest.raises(PortfolioError, match="Pozisyon bulunamadı"):
            portfolio.close_long("ETH/USDT", 3000.0)

    def test_realized_pnl_accumulates_across_trades(self, portfolio):
        """İki ardışık trade'in realized PnL'i birikir."""
        SYM2 = "ETH/USDT"
        PRICE_ETH = 3_000.0
        QTY_ETH = 1.0

        # Trade 1: BTC kâr
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        rpnl1 = portfolio.close_long(SYM, 55_000.0)

        # Trade 2: ETH zarar
        portfolio.open_long(SYM2, PRICE_ETH, QTY_ETH)
        portfolio.update_price(SYM2, PRICE_ETH)
        rpnl2 = portfolio.close_long(SYM2, 2_800.0)

        assert pytest.approx(portfolio.realized_pnl, rel=1e-9) == rpnl1 + rpnl2


# ─── Fiyat Güncelleme ve Unrealized PnL ─────────────────────────────────────

class TestPriceAndUnrealizedPnl:

    def test_update_price_changes_unrealized_pnl(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, 55_000.0)
        expected = QTY * 55_000.0 - ENTRY_COST  # 5500 - 5005 = 495
        assert pytest.approx(portfolio.unrealized_pnl, rel=1e-9) == expected

    def test_unrealized_pnl_zero_when_no_positions(self, portfolio):
        assert portfolio.unrealized_pnl == 0.0

    def test_unrealized_pnl_negative_when_price_drops(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, 45_000.0)
        assert portfolio.unrealized_pnl < 0

    def test_unrealized_pnl_sums_multiple_positions(self, portfolio):
        SYM2 = "ETH/USDT"
        ETH_ENTRY = 3_000.0
        ETH_QTY = 1.0
        eth_cost = ETH_QTY * ETH_ENTRY * (1 + COMM)

        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.open_long(SYM2, ETH_ENTRY, ETH_QTY)
        portfolio.update_price(SYM, 55_000.0)
        portfolio.update_price(SYM2, 3_300.0)

        btc_upnl = QTY * 55_000.0 - ENTRY_COST
        eth_upnl = ETH_QTY * 3_300.0 - eth_cost
        assert pytest.approx(portfolio.unrealized_pnl, rel=1e-9) == btc_upnl + eth_upnl

    def test_update_price_for_position_without_open_is_ignored(self, portfolio):
        """Açık pozisyon olmayan sembol için fiyat güncellemesi hata fırlatmamalı."""
        portfolio.update_price("XRP/USDT", 1.0)  # pozisyon yok → sessizce geçer


# ─── Total Equity ─────────────────────────────────────────────────────────────

class TestTotalEquity:

    def test_equity_equals_cash_when_no_positions(self, portfolio):
        assert pytest.approx(portfolio.total_equity) == INITIAL_CASH

    def test_equity_includes_open_position_value(self, portfolio):
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, 55_000.0)
        # equity = cash + position market value
        pos_value = QTY * 55_000.0
        assert pytest.approx(portfolio.total_equity, rel=1e-9) == CASH_AFTER_OPEN + pos_value

    def test_equity_after_profitable_close(self, portfolio):
        """Kârlı kapatma sonrası equity artmış olmalı."""
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        portfolio.close_long(SYM, 55_000.0)
        assert portfolio.total_equity > INITIAL_CASH

    def test_equity_after_losing_close(self, portfolio):
        """Zararlı kapatma sonrası equity azalmış olmalı."""
        portfolio.open_long(SYM, PRICE_ENTRY, QTY)
        portfolio.update_price(SYM, PRICE_ENTRY)
        portfolio.close_long(SYM, 45_000.0)
        assert portfolio.total_equity < INITIAL_CASH
