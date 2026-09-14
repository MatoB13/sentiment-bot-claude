"""TIENOVE meranie "rychlej vrstvy" sprav (2026-09-14, na ziadost pouzivatela).

OTAZKA: dnes bot cita spravy len v cykle - plny cyklus spusta rozvrh (6-9 h pri
slabsich tickeroch), watch/alarm (CENA) alebo zatvorenie obchodu. Sprava, ktora
pride PRED pohybom ceny, caka na najblizsi sken. "Rychla vrstva" by cyklus
spustala samotnou spravou. Oplati sa to? Za 7 dni pred 14.9. bola medzi dvoma
pohladmi na spravy medzera median 3.1 h, p90 7.4 h (60 % plnych cyklov je watch).
Jediny doterajsi udaj (11.9.): cerstvost spravy z web_searchu vysledok nezmenila.

AKO (v1, bez jedineho volania Clauda - zadarmo): kazdych NEWS_WATCH_INTERVAL_MINUTES
sa z UZ EXISTUJUCICH zasobnikov (Benzinga - alpaca_news_client, Google News -
google_news_client; nic sa nestahuje navyse, kese su zdielane s cyklami) zapise
kazda NOVA sprava o tickeri do news_events: kedy vysla, kedy ju bot uvidel, cena
4 h pred tym, cena v tej chvili, ATR. Neskor sa dopina cena o 1/4/12 h a max/min
za 12 h (z price_minutes - tie sa drzia len 3 dni, preto priebezne).
Vyhodnotenie (planovana uloha) z toho a z cycle_logs zisti: pohla sa cena az PO
sprave? o kolko ATR? kedy sa k nej bot realne dostal (watch/sken/plny cyklus)?

DO ROZHODOVANIA NEVSTUPUJE NIC. NEWS_WATCH_ENABLED=false job vypne.
NIKDY nevyhodi vynimku (vlastny job v scheduleri, chyba jedneho tickera nezastavi ostatne).
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

import alpaca_news_client
import assets
import config
import google_news_client
from db import CycleLog, NewsEvent, PriceMinute, get_session

# (ticker, zdroj), pre ktore uz v TOMTO procese prisiel neprazdny vysledok. Prvy
# neprazdny vysledok po starte/restarte je "baseline": tie spravy uz boli v
# zasobniku, ich seen_at nie je cas prichodu a do vyhodnotenia nejdu.
_started: set = set()
_OUTCOME_WINDOW_DAYS = 2


def _norm(title: str) -> str:
    return re.sub(r"\W+", " ", (title or "").lower()).strip()[:300]


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _price_at(session, symbol: str, target: datetime, tolerance_min: float = 5) -> float | None:
    """Minutova cena najblizsie pred `target` (naive UTC), najviac tolerance_min stara."""
    row = (session.query(PriceMinute.price)
           .filter(PriceMinute.symbol == symbol, PriceMinute.ts <= target,
                   PriceMinute.ts >= target - timedelta(minutes=tolerance_min))
           .order_by(PriceMinute.ts.desc()).first())
    return float(row[0]) if row else None


def _atr(session, symbol: str) -> float | None:
    rows = (session.query(CycleLog.ta).filter(CycleLog.symbol == symbol)
            .order_by(CycleLog.created_at.desc()).limit(5).all())
    for (ta,) in rows:
        try:
            v = float((ta or {}).get("atr14") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if v > 0:
            return v
    return None


def _candidates(asset: dict) -> list[tuple]:
    """(zdroj, titulok, medium, vydane, rutinne) zo zdielanych zasobnikov."""
    out = []
    try:
        for i in alpaca_news_client.items_for_asset(asset):
            out.append(("benzinga", i["title"], None, i["created"], i["routine"]))
    except Exception as e:
        print(f"[news_watch] [{asset['name']}] Benzinga zlyhala (pokracujem): {e}")
    try:
        for i in google_news_client.items_for_asset(asset):
            out.append(("google", i["title"], i.get("source"), i["created"], None))
    except Exception as e:
        print(f"[news_watch] [{asset['name']}] Google News zlyhal (pokracujem): {e}")
    return out


def _collect(asset: dict, session, now: datetime) -> int:
    symbol = asset["strike_symbol"]
    items = _candidates(asset)
    if not items:
        return 0
    norms = {_norm(t) for _, t, *_ in items}
    existing = {r[0] for r in session.query(NewsEvent.title_norm)
                .filter(NewsEvent.symbol == symbol, NewsEvent.title_norm.in_(list(norms))).all()}
    first = {src: (asset["name"], src) not in _started for src in ("benzinga", "google")}
    snap = None
    added = 0
    for src, title, media, created, routine in items:
        n = _norm(title)
        if not n or n in existing:
            continue
        existing.add(n)
        if snap is None:
            snap = (_price_at(session, symbol, now), _price_at(session, symbol, now - timedelta(hours=4), 10),
                    _atr(session, symbol))
        session.add(NewsEvent(symbol=symbol, source=src, title=title[:500], title_norm=n, media=media,
                              published_at=_naive(created), seen_at=now, routine=routine,
                              baseline=first[src], price_at_seen=snap[0], price_pre_4h=snap[1],
                              atr=snap[2]))
        added += 1
    session.commit()
    for src in {s for s, *_ in items}:
        _started.add((asset["name"], src))
    return added


def _fill_outcomes(session, now: datetime) -> int:
    """Doplni ceny po sprave, ked uz uplynul cas (1/4/12 h)."""
    pending = (session.query(NewsEvent)
               .filter(NewsEvent.baseline.is_(False), NewsEvent.price_12h.is_(None),
                       NewsEvent.seen_at <= now - timedelta(hours=1),
                       NewsEvent.seen_at >= now - timedelta(days=_OUTCOME_WINDOW_DAYS))
               .limit(500).all())
    filled = 0
    for ev in pending:
        if ev.price_1h is None:
            ev.price_1h = _price_at(session, ev.symbol, ev.seen_at + timedelta(hours=1))
        if ev.price_4h is None and ev.seen_at <= now - timedelta(hours=4):
            ev.price_4h = _price_at(session, ev.symbol, ev.seen_at + timedelta(hours=4))
        if ev.seen_at <= now - timedelta(hours=12):
            ev.price_12h = _price_at(session, ev.symbol, ev.seen_at + timedelta(hours=12))
            hi, lo = (session.query(func.max(PriceMinute.price), func.min(PriceMinute.price))
                      .filter(PriceMinute.symbol == ev.symbol, PriceMinute.ts > ev.seen_at,
                              PriceMinute.ts <= ev.seen_at + timedelta(hours=12)).one())
            ev.high_12h, ev.low_12h = hi, lo
        filled += 1
    session.commit()
    return filled


def poll_news() -> None:
    if not config.NEWS_WATCH_ENABLED:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = get_session()
    try:
        added = 0
        for asset in assets.enabled_assets():
            try:
                added += _collect(asset, session, now)
            except Exception as e:
                session.rollback()
                print(f"[news_watch] [{asset['name']}] zapis zlyhal (pokracujem): {e}")
        try:
            filled = _fill_outcomes(session, now)
        except Exception as e:
            session.rollback()
            filled = 0
            print(f"[news_watch] doplnanie cien zlyhalo (pokracujem): {e}")
        if added or filled:
            print(f"[news_watch] novych sprav {added}, doplnene ceny pri {filled}")
    except Exception as e:
        print(f"[news_watch] beh zlyhal: {e}")
    finally:
        session.close()
