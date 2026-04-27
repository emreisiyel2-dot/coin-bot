"""
EventLogger — JSONL tabanlı olaysal loglama.

Her bot çalışması için ayrı run_id üretir.
Dört ayrı dosyaya yazar:
  logs/runs.jsonl   → çalışma başlangıç/bitiş olayları
  logs/scans.jsonl  → sembol tarama kararları
  logs/trades.jsonl → açılan/kapanan pozisyonlar
  logs/errors.jsonl → yakalanan istisnalar

Kullanım:
    evt = EventLogger("breakout_v2")
    evt.log_run("started")
    evt.log_scan("BTC/USDT", "no_signal", bars_loaded=259)
    evt.log_run("completed", symbols_scanned=4, duration_sec=evt.elapsed())
"""

import json
import time
import traceback as tb
from datetime import datetime, timezone
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"


class EventLogger:
    """
    Append-only JSONL event logger.

    Her örnek bir run_id'ye sahiptir; tüm log satırlarına otomatik eklenir.
    thread-safe değildir — her bot çalışması kendi instance'ını oluşturmalı.
    """

    def __init__(self, bot_name: str, run_id: str | None = None,
                 logs_dir: Path | None = None) -> None:
        self._bot       = bot_name
        self._run_id    = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._logs_dir  = logs_dir or _LOGS_DIR
        self._t0        = time.monotonic()
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def log_run(self, status: str, **kwargs) -> None:
        """
        Çalışma başlangıç / bitiş eventi.

        status: "started" | "completed" | "error"
        Ek alanlar (completed'da): symbols_scanned=N, duration_sec=...
        """
        self._write("runs.jsonl", {"event": "run", "status": status, **kwargs})

    def log_scan(self, symbol: str, signal_state: str, **kwargs) -> None:
        """
        Tek sembol için tarama kararı.

        signal_state:
          "scanned"             → veri yüklendi
          "no_signal"           → strateji HOLD döndürdü
          "candidate_rejected"  → ön koşul başarısız (reject_reason + checks ile birlikte)
          "signal_accepted"     → strateji BUY döndürdü, pozisyon açılmaya çalışılıyor
        """
        self._write("scans.jsonl", {
            "event": "scan", "symbol": symbol,
            "signal_state": signal_state, **kwargs,
        })

    def log_trade(self, symbol: str, action: str, **kwargs) -> None:
        """
        Açılan veya kapanan pozisyon.

        action: "OPEN" | "CLOSE"
        Ek alanlar: price, quantity, size_usdt, pnl, reason, strategy
        """
        self._write("trades.jsonl", {
            "event": "trade", "symbol": symbol, "action": action, **kwargs,
        })

    def log_error(self, error: str, **kwargs) -> None:
        """
        Yakalanan istisna.

        error: str(exc)
        Ek alanlar: context, traceback
        """
        self._write("errors.jsonl", {"event": "error", "error": error, **kwargs})

    def elapsed(self) -> float:
        """Başlangıçtan bu yana geçen süre (saniye, 3 ondalık)."""
        return round(time.monotonic() - self._t0, 3)

    @property
    def run_id(self) -> str:
        return self._run_id

    # ── Dahili ────────────────────────────────────────────────────────────────

    def _write(self, filename: str, record: dict) -> None:
        entry = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "bot":    self._bot,
            "run_id": self._run_id,
            **record,
        }
        path = self._logs_dir / filename
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass   # log yazma hatası asla bot'u durdurmamalı


def capture_exception(evt: EventLogger, context: str = "") -> None:
    """
    except bloğu içinden çağrılır — mevcut istisnayı loglar.

    Kullanım:
        except Exception as exc:
            capture_exception(evt, context="fetch_candles:BTC/USDT")
            raise  # veya logger.warning(...)
    """
    evt.log_error(
        error=tb.format_exc().strip().splitlines()[-1],
        context=context,
        traceback=tb.format_exc().strip(),
    )


# ── Dashboard summary ────────────────────────────────────────────────────────

def write_dashboard_summary(
    logs_dir: Path | None = None,
    state_files: dict[str, Path] | None = None,
    extra_fields: dict | None = None,
) -> None:
    """
    logs/dashboard_summary.json oluşturur / günceller.

    Her bot çalışmasının sonunda çağrılabilir.
    Mevcut JSONL logları ve state dosyalarını okur (sadece okuma).
    """
    logs_dir = logs_dir or _LOGS_DIR
    state_files = state_files or {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read_jsonl(filename: str) -> list[dict]:
        path = logs_dir / filename
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def _last_run_for_bot(runs: list[dict], bot: str) -> dict:
        for r in reversed(runs):
            if r.get("bot") == bot and r.get("status") == "completed":
                return {"ts": r.get("ts"), "duration_sec": r.get("duration_sec")}
        return {}

    def _last_scan_for_bot(scans: list[dict], bot: str) -> dict:
        for s in reversed(scans):
            if s.get("bot") == bot:
                return {"ts": s.get("ts"), "symbol": s.get("symbol"),
                        "signal_state": s.get("signal_state")}
        return {}

    def _count_positions(state_path: Path) -> int:
        if not state_path.exists():
            return 0
        try:
            st = json.loads(state_path.read_text())
            return len(st.get("positions", {}))
        except Exception:
            return 0

    def _count_closed_trades(state_path: Path) -> int:
        if not state_path.exists():
            return 0
        try:
            st = json.loads(state_path.read_text())
            return len([t for t in st.get("trade_log", []) if t.get("action") == "CLOSE"])
        except Exception:
            return 0

    runs  = _read_jsonl("runs.jsonl")
    scans = _read_jsonl("scans.jsonl")

    today_scans = [s for s in scans if s.get("ts", "").startswith(today)]

    # Per-bot stats
    bots = ["breakout_v2", "rsi_reversion", "short_momentum_v1", "momentum_pullback_v1"]
    bot_summaries = {}
    for bot in bots:
        bot_scans = [s for s in today_scans if s.get("bot") == bot]
        no_signal = len([s for s in bot_scans if s.get("signal_state") == "no_signal"])
        rejected  = len([s for s in bot_scans if s.get("signal_state") == "candidate_rejected"])
        accepted  = len([s for s in bot_scans if s.get("signal_state") == "signal_accepted"])

        reject_reasons = {}
        for s in bot_scans:
            if s.get("signal_state") == "candidate_rejected":
                reason = s.get("reject_reason", "unknown")
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        bot_summaries[bot] = {
            "last_run":   _last_run_for_bot(runs, bot),
            "last_scan":  _last_scan_for_bot(scans, bot),
            "scans_today": len(bot_scans),
            "no_signal":  no_signal,
            "rejected":   rejected,
            "accepted":   accepted,
            "top_reject_reasons": dict(sorted(reject_reasons.items(), key=lambda x: -x[1])[:5]),
        }

        # Scoring stats from score_decision in scan logs
        score_buy_count = 0
        score_skip_count = 0
        score_reasons: dict[str, int] = {}
        symbol_scores: dict[str, list[int]] = {}
        for s in bot_scans:
            sd = s.get("score_decision")
            if not sd:
                continue
            sym = s.get("symbol", "")
            sc = sd.get("score", 0)
            symbol_scores.setdefault(sym, []).append(sc)
            if sd.get("action") == "BUY":
                score_buy_count += 1
            else:
                score_skip_count += 1
            for r in sd.get("reject_reasons", []):
                score_reasons[r] = score_reasons.get(r, 0) + 1

        latest_scores = {}
        avg_scores = {}
        for sym, scs in symbol_scores.items():
            latest_scores[sym] = scs[-1]
            avg_scores[sym] = round(sum(scs) / len(scs), 1)

        bot_summaries[bot]["scoring"] = {
            "buy_candidates":   score_buy_count,
            "skip_count":       score_skip_count,
            "latest_score_per_symbol": latest_scores,
            "avg_score_per_symbol":    avg_scores,
            "top_score_reject_reasons": dict(sorted(score_reasons.items(), key=lambda x: -x[1])[:5]),
        }

    # Position / trade counts from state files
    live_dir = logs_dir.parent / "live"
    bk_state = state_files.get("breakout_v2", live_dir / "paper_state.json")
    rr_state = state_files.get("rsi_reversion", live_dir / "intraday_state.json")

    summary = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "total_scans_today": len(today_scans),
        "open_positions": {
            "breakout_v2":  _count_positions(bk_state),
            "rsi_reversion": _count_positions(rr_state),
        },
        "completed_trades": {
            "breakout_v2":  _count_closed_trades(bk_state),
            "rsi_reversion": _count_closed_trades(rr_state),
        },
        "bots": bot_summaries,
    }

    # Add short-side breakdown from intraday state (shared for all intraday strategies)
    rr_st_data = {}
    try:
        if rr_state.exists():
            rr_st_data = json.loads(rr_state.read_text())
    except Exception:
        pass

    if rr_st_data:
        positions = rr_st_data.get("positions", {})
        trade_log = rr_st_data.get("trade_log", [])
        long_pos = {s: p for s, p in positions.items() if p.get("side") != "short"}
        short_pos = {s: p for s, p in positions.items() if p.get("side") == "short"}
        long_closed = [t for t in trade_log if t.get("action") == "CLOSE" and t.get("side") != "short"]
        short_closed = [t for t in trade_log if t.get("action") == "CLOSE" and t.get("side") == "short"]
        short_pnl = sum(t.get("pnl", 0) for t in short_closed)
        long_pnl = sum(t.get("pnl", 0) for t in long_closed)
        summary["side_breakdown"] = {
            "long_positions": len(long_pos),
            "short_positions": len(short_pos),
            "long_closed_trades": len(long_closed),
            "short_closed_trades": len(short_closed),
            "pnl_by_side": {
                "long": round(long_pnl, 4),
                "short": round(short_pnl, 4),
            },
        }

        # Short reject reasons from scan logs
        short_scans = [s for s in today_scans if s.get("bot") == "short_momentum_v1"]
        short_reject_reasons = {}
        for s in short_scans:
            if s.get("signal_state") == "candidate_rejected":
                reason = s.get("reject_reason", "unknown")
                short_reject_reasons[reason] = short_reject_reasons.get(reason, 0) + 1
        summary["short_momentum_v1_summary"] = {
            "accepted": len([s for s in short_scans if s.get("signal_state") == "signal_accepted"]),
            "rejected": len([s for s in short_scans if s.get("signal_state") == "candidate_rejected"]),
            "no_signal": len([s for s in short_scans if s.get("signal_state") == "no_signal"]),
            "top_reject_reasons": dict(sorted(short_reject_reasons.items(), key=lambda x: -x[1])[:5]),
        }

    if extra_fields:
        summary.update(extra_fields)

    out_path = logs_dir / "dashboard_summary.json"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
    except OSError:
        pass
