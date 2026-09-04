"""Spocitane fakty o doterajsej vykonnosti bota - do KAZDEHO promptu namiesto
rolling retrospektivy (2026-09-04, bod 4 auditu, schvalene pouzivatelom).

Preco: rolling retrospektiva bol Claudov volny text o sebe samom z n=3-5
obchodov - trhovy narativ, ktory starne (Hormuz, unlocky), zavery typu "VSETKY
straty boli chase" (base-rate chyba - 84 % vsetkych vstupov je chase), chvala
vlastnych nastrojov a vety o prahu, ktory uz nepozna. Audit ukazal, ze
diagnostikuje spravne a spravanie nemeni. Hodnotu ma len PRESNA sebaznalost -
a tu vie dat SQL lepsie nez narativ.

Co sa uklada: nic. Pocita sa z trades + cycle_logs (adx14 pri vstupe,
trigger_source) + price_bars (4h pohyb pred vstupom). Vysledok sa cachuje
CACHE_MINUTES v pamati procesu - jeden vypocet obsluzi vsetky tickery v tiku.

Pravidla proti novemu zdroju sumu (dohodnute s pouzivatelom):
- okno WINDOW_DAYS, riadok len pri n >= MIN_ROW (portfolio), ticker len suhrn
  pri n >= MIN_TICKER (bez rozdelenia na smer - z 9 a 10 obchodov by to bol sum)
- kalibracna tabulka az od MIN_CALIBRATION obchodov na NOVEJ skale confidence
  (od NEW_SCALE_FROM, ked sa prah prestal uvadzat a skala sa roztiahla) - stara
  skala 65-70 sa s novou nedá miesat
- ziadne odporucania, jedna veta ramca: opis, nie pravidlo
"""
import threading
from datetime import datetime, timedelta, timezone

from db import CycleLog, PriceBar, Trade

WINDOW_DAYS = 30
MIN_ROW = 20
MIN_TICKER = 10
MIN_RECENT = 5
RECENT_HOURS = 48
MIN_CALIBRATION = 20
MIN_CALIBRATION_BUCKET = 5
NEW_SCALE_FROM = datetime(2026, 9, 4, 12, 0)   # naive UTC, ako Trade.opened_at v DB
CACHE_MINUTES = 15

_cache: dict = {"at": None, "rows": None}
_lock = threading.Lock()


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _load_rows(session, now: datetime) -> list[dict]:
    """Jeden riadok na uzavrety obchod v okne: symbol, smer, R, vyhra, ADX pri
    vstupe, zdroj triggeru, 4h momentum, confidence, casy."""
    now_n = _naive_utc(now)
    cutoff = now_n - timedelta(days=WINDOW_DAYS)
    trades = (
        session.query(Trade)
        .filter(Trade.dry_run.is_(False), Trade.status != "open",
                Trade.pnl_usd.isnot(None), Trade.closed_at >= cutoff)
        .all()
    )
    if not trades:
        return []
    ids = [t.id for t in trades]
    logs = {
        cl.trade_id: cl
        for cl in session.query(CycleLog)
        .filter(CycleLog.trade_id.in_(ids), CycleLog.outcome == "opened")
        .all()
    }

    rows = []
    for t in trades:
        ep = t.entry_fill_price or t.entry_price
        if not ep or t.stop_loss_price is None or not t.notional_usd or not t.opened_at:
            continue
        risk = t.notional_usd * abs(ep - t.stop_loss_price) / ep
        if risk <= 0:
            continue
        cl = logs.get(t.id)
        adx = None
        src = None
        if cl is not None:
            ta = cl.ta if isinstance(cl.ta, dict) else {}
            adx = ta.get("adx14")
            src = cl.trigger_source or ("watch" if cl.triggered_by_watch else None) or "scheduled"
        h0 = _naive_utc(t.opened_at).replace(minute=0, second=0, microsecond=0)
        rows.append({
            "symbol": t.symbol,
            "dir": (t.direction or "").lower(),
            "R": t.pnl_usd / risk,
            "pnl": t.pnl_usd,
            "win": t.pnl_usd > 0,
            "adx": float(adx) if adx is not None else None,
            "src": src,
            "conf": t.confidence,
            "opened_at": _naive_utc(t.opened_at),
            "closed_at": _naive_utc(t.closed_at) if t.closed_at else None,
            "h0": h0,
            "mom": None,
        })

    # 4h pohyb pred vstupom - len potrebne bary, jednym dotazom
    symbols = {r["symbol"] for r in rows}
    hours = set()
    for r in rows:
        hours.add(r["h0"])
        hours.add(r["h0"] - timedelta(hours=4))
    closes = {
        (b.symbol, b.hour_start): b.close
        for b in session.query(PriceBar.symbol, PriceBar.hour_start, PriceBar.close)
        .filter(PriceBar.symbol.in_(symbols), PriceBar.hour_start.in_(hours))
        .all()
    }
    for r in rows:
        c0 = closes.get((r["symbol"], r["h0"]))
        c4 = closes.get((r["symbol"], r["h0"] - timedelta(hours=4)))
        if c0 is not None and c4 is not None and c4 > 0 and r["dir"] in ("long", "short"):
            r["mom"] = ((c0 - c4) > 0) == (r["dir"] == "long")
    return rows


def _rows(session, now: datetime) -> list[dict]:
    with _lock:
        at = _cache["at"]
        if at is not None and (now - at) < timedelta(minutes=CACHE_MINUTES) and _cache["rows"] is not None:
            return _cache["rows"]
    rows = _load_rows(session, now)
    with _lock:
        _cache["at"] = now
        _cache["rows"] = rows
    return rows


def clear_cache() -> None:
    with _lock:
        _cache["at"] = None
        _cache["rows"] = None


def _stat(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n,
        "win_pct": (100.0 * sum(1 for r in rows if r["win"]) / n) if n else None,
        "avg_R": (sum(r["R"] for r in rows) / n) if n else None,
        "pnl": sum(r["pnl"] for r in rows),
    }


def _regime(adx: float | None) -> str | None:
    if adx is None:
        return None
    if adx < 20:
        return "weak_no_trend"
    if adx < 25:
        return "developing"
    return "trending"


def compute(session, symbol: str, now: datetime | None = None) -> dict | None:
    """Fakty pre prompt tickera `symbol`. None, ak v okne nie je ziadny obchod."""
    now = now or datetime.now(timezone.utc)
    rows = _rows(session, now)
    if not rows:
        return None
    now_n = _naive_utc(now)

    def sub(pred):
        return _stat([r for r in rows if pred(r)])

    out = {
        "window_days": WINDOW_DAYS,
        "portfolio": _stat(rows),
        "regime": {k: sub(lambda r, k=k: _regime(r["adx"]) == k)
                   for k in ("trending", "developing", "weak_no_trend")},
        "direction": {k: sub(lambda r, k=k: r["dir"] == k) for k in ("long", "short")},
        "momentum": {"with": sub(lambda r: r["mom"] is True), "against": sub(lambda r: r["mom"] is False)},
        "source": {k: sub(lambda r, k=k: r["src"] == k) for k in ("watch", "scheduled")},
        "recent": _stat([r for r in rows if r["closed_at"] and r["closed_at"] >= now_n - timedelta(hours=RECENT_HOURS)]),
        "ticker": _stat([r for r in rows if r["symbol"] == symbol]),
    }
    new = [r for r in rows if r["opened_at"] >= NEW_SCALE_FROM and r["conf"] is not None]
    buckets = {}
    for lo, hi, lab in ((0, 60, "pod 60"), (60, 70, "60-69"), (70, 80, "70-79"), (80, 101, "80+")):
        buckets[lab] = _stat([r for r in new if lo <= r["conf"] < hi])
    out["calibration"] = {"n": len(new), "buckets": buckets}
    return out


def _line(label: str, s: dict, min_n: int) -> str | None:
    if s["n"] < min_n:
        return None
    return f"{label}: {s['n']} obchodov, win {s['win_pct']:.0f} %, priemer {s['avg_R']:+.2f} R"


def format_text(facts: dict | None, asset_name: str) -> str | None:
    """Blok do user promptu. None = nic (ziadne obchody v okne)."""
    if not facts or facts["portfolio"]["n"] == 0:
        return None
    lines = [f"## Tvoja doterajšia výkonnosť - spočítané fakty za {facts['window_days']} dní (opis, nie pravidlo)"]
    p = facts["portfolio"]
    lines.append(f"Celé portfólio (všetky tickery): {p['n']} obchodov, win {p['win_pct']:.0f} %, priemer {p['avg_R']:+.2f} R")
    for k, lab in (("trending", "sila trendu pri vstupe = trending (ADX≥25)"),
                   ("developing", "sila trendu pri vstupe = developing (20-25)"),
                   ("weak_no_trend", "sila trendu pri vstupe = weak_no_trend (<20)")):
        ln = _line(lab, facts["regime"][k], MIN_ROW)
        if ln:
            lines.append("  " + ln)
    for k, lab in (("long", "smer long"), ("short", "smer short")):
        ln = _line(lab, facts["direction"][k], MIN_ROW)
        if ln:
            lines.append("  " + ln)
    for k, lab in (("with", "vstup v smere 4h pohybu"), ("against", "vstup proti 4h pohybu")):
        ln = _line(lab, facts["momentum"][k], MIN_ROW)
        if ln:
            lines.append("  " + ln)
    for k, lab in (("watch", "cyklus vyvolaný watch úrovňou"), ("scheduled", "plánovaný cyklus")):
        ln = _line(lab, facts["source"][k], MIN_ROW)
        if ln:
            lines.append("  " + ln)
    rc = facts["recent"]
    if rc["n"] >= MIN_RECENT:
        lines.append(f"  posledných {RECENT_HOURS} h: {rc['n']} obchodov, win {rc['win_pct']:.0f} %, PnL {rc['pnl']:+.0f} $")
    tk = facts["ticker"]
    if tk["n"] >= MIN_TICKER:
        lines.append(f"{asset_name} (tento ticker): {tk['n']} obchodov, win {tk['win_pct']:.0f} %, priemer {tk['avg_R']:+.2f} R")
    else:
        lines.append(f"{asset_name} (tento ticker): {tk['n']} obchodov v okne - málo na záver")
    cal = facts["calibration"]
    if cal["n"] >= MIN_CALIBRATION:
        parts = [f"conf {lab}: {s['n']} / win {s['win_pct']:.0f} %"
                 for lab, s in cal["buckets"].items() if s["n"] >= MIN_CALIBRATION_BUCKET]
        lines.append(f"Kalibrácia tvojej confidence (od {NEW_SCALE_FROM:%d.%m.}, n={cal['n']}): " + "; ".join(parts))
    else:
        lines.append(f"Kalibrácia tvojej confidence: zatiaľ {cal['n']} obchodov na aktuálnej škále - tabuľka od {MIN_CALIBRATION}")
    return "\n".join(lines) + "\n"


def get_text(session, symbol: str, asset_name: str, now: datetime | None = None) -> str | None:
    return format_text(compute(session, symbol, now), asset_name)
