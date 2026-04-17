"""
Live trading readiness checklist.

Kullanım:
    python -m live.readiness
"""

from pathlib import Path

STATE_FILE = Path(__file__).parent / "paper_state.json"


def print_checklist() -> None:
    state_exists = STATE_FILE.exists()

    items = [
        # (kategori, açıklama, durum, not)
        # ── HAZIR ────────────────────────────────────────────────────────────
        ("HAZIR", "Strateji (breakout_v2)",             True,  "4h, onaylı parametreler"),
        ("HAZIR", "Backtest doğrulaması",                True,  "365 gün, PF=1.63, WR=47%"),
        ("HAZIR", "Risk tabanlı pozisyon boyutlandırma", True,  "%1 equity/trade, %50 max exposure"),
        ("HAZIR", "Günlük kayıp koruması",               True,  "Realized+unrealized %2 limiti"),
        ("HAZIR", "Max eşzamanlı trade limiti",           True,  "3 trade / 1 trade/sembol"),
        ("HAZIR", "Auto-disable sistemi",                True,  "WR<%30 veya net PnL<0 ise durdur"),
        ("HAZIR", "State kalıcılığı",                    True,  "paper_state.json, yeniden başlatma safe"),
        ("HAZIR", "Paper trading runner",                True,  "python -m live.paper_trader"),
        ("HAZIR", "Durum raporu",                        True,  "python -m live.paper_trader --status"),
        ("HAZIR", "Exchange bağlantısı (read-only)",     True,  "ccxt.binance — sadece veri çekme"),
        ("HAZIR", "Paper state dosyası",                 state_exists, "paper_state.json"),

        # ── EKSİK ─────────────────────────────────────────────────────────────
        ("EKSİK", "Gerçek emir yerleştirme (order placement)", False,
         "ccxt buy_market / create_order henüz yok — paper_trader sadece sanal"),
        ("EKSİK", "API anahtarı yönetimi",               False,
         ".env'de BINANCE_API_KEY / SECRET gerekli — şu an yok"),
        ("EKSİK", "Order fill doğrulama",                False,
         "Gerçek fill fiyatı vs beklenen fiyat kontrolü yok"),
        ("EKSİK", "Exchange tarafında TP/SL emirleri",   False,
         "Şu an fiyat takibi yazılım tarafında; exchange OCO/stop emirleri yok"),
        ("EKSİK", "Bağlantı kesinti kurtarma",           False,
         "WebSocket veya periyodik yeniden deneme mantığı yok"),
        ("EKSİK", "Kısmi dolum (partial fill) yönetimi", False,
         "Büyük emir kısmi dolarsa pozisyon takibi tutarsız olur"),
        ("EKSİK", "Minimum lot/tick size doğrulama",     False,
         "Her sembol için exchange minQty/stepSize uyumu kontrol edilmeli"),
        ("EKSİK", "Gerçek zamanlı alarm / bildirim",     False,
         "Telegram/e-posta bildirimi yok — hatalar yalnızca log'a düşüyor"),
        ("EKSİK", "Cron / zamanlayıcı entegrasyonu",     False,
         "Her 4h otomatik çalıştırma için cron/scheduler kurulmadı"),
        ("EKSİK", "Multi-instance kilidi",               False,
         "Aynı anda iki process çalışırsa state bozulabilir — dosya kilidi yok"),
    ]

    ready   = [i for i in items if i[2]]
    missing = [i for i in items if not i[2]]

    print("\n" + "═" * 66)
    print("  LIVE TRADING HAZIRLIK RAPORU — breakout_v2 / 4h")
    print("═" * 66)
    print(f"  Hazır  : {len(ready)}/{len(items)}")
    print(f"  Eksik  : {len(missing)}/{len(items)}")
    print()

    print("  ✅  HAZIR")
    print("  " + "─" * 62)
    for _, desc, _, note in ready:
        print(f"  ✅  {desc:<46} {note}")

    print()
    print("  ❌  EKSİK — canlı işlem için gerekli")
    print("  " + "─" * 62)
    for _, desc, _, note in missing:
        print(f"  ❌  {desc:<46}")
        print(f"       → {note}")

    print()
    print("  ── Öneri ──────────────────────────────────────────────────")
    print("  Paper trading:  HAZIR — hemen başlatılabilir")
    print("  Gerçek para  :  HENÜZ DEĞİL — en az şunlar tamamlanmalı:")
    print("    1. API anahtarı + emir yerleştirme (ccxt create_order)")
    print("    2. Exchange tarafı TP/SL emirleri (OCO) veya arka plan izleyici")
    print("    3. Bağlantı kesinti ve kısmi dolum yönetimi")
    print("    4. Cron ile otomatik zamanlama")
    print("    5. En az 4 hafta paper trading ile canlı performans doğrulaması")
    print("═" * 66 + "\n")


if __name__ == "__main__":
    print_checklist()
