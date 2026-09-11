"""CoinMarketCal (https://coinmarketcal.com) klient - kalendar krypto-projektovych
udalosti (burzove listingy, hlasovania, protokolove upgrady, token unlocky),
per-asset (viz assets.py 'coinmarketcal_slug') - 2026-08-19, na ziadost
pouzivatela. Doplna existujuci Event Risk Gate mechanizmus (viz claude_analyst.py),
ktory doteraz spolieha VYHRADNE na Claude-ov vlastny web_search bez
strukturovaneho zdroja - teraz dostane aj overene, strukturovane udaje priamo
v prompte.

API detaily (overene naozivo 2026-08-19 s realnym kluucom pouzivatela):
- auth: header "x-api-key"
- endpoint: /v2/events?coins=<slug>&limit=N (POZOR: nie /v1 - ten vracia
  ploche {"message":"Forbidden"} bez ohladu na cestu/auth format, ziadny
  rozumny error text)
- Free plan ma KREDITOVY kvoten, nie klasicky rate-limit (resetuje sa ~13 dni,
  viz screenshot pouzivatela "0% used - 13d to reset") - preto sa API vola
  LEN raz denne z poll_events() nizsie, NIKDY zivo pocas obchodneho cyklu
  (na rozdiel od marketaux_client.py, ktory ma in-memory cache, ale stale
  vola zivo pri cache-mise - tu je to plna DB-kes architektura, rovnaky vzor
  ako price_poller.py/sl_calibration.py).
- Free plan pokryva LEN top 100 trackovanych coinov celkovo (/v2/coins,
  overene ziadny dalsi cursor za stranku 2). Z nasich tickerov potvrdene
  pokryte: bitcoin, cardano (ADA), zcash (ZEC), hyperliquid (HYPE),
  midnight-3 (NIGHT, symbol "night") - MINIMAX nie je pokryty vobec (mimo
  top 100), ziadny nas ini ticker (NAS100/NVDA/GOLD/WTI/AAOI/GOOGL/SKHYNIX)
  nie je krypto-specificky, teda sa sem netyka."""
from datetime import datetime, timedelta, timezone

import requests

import config
from db import CoinMarketCalEvent, CoinMarketCalSlugStatus, get_session

_BASE_URL = "https://api.coinmarketcal.com/v2/events"
_COINS_URL = "https://api.coinmarketcal.com/v2/coins"
# Kontrola pokrytia slugov (2026-09-11) - raz za tyzden, viz check_slugs().
_SLUG_CHECK_DAYS = 7
_COINS_MAX_PAGES = 5  # free plan = 100 coinov = 2 strany po 50


def _fetch_covered_coins() -> dict:
    """{slug: rank} vsetkych coinov, ktore bezplatny plan pokryva. Vynimku necha prejst."""
    out, cursor = {}, None
    for _ in range(_COINS_MAX_PAGES):
        resp = requests.get(
            _COINS_URL,
            headers={"x-api-key": config.COINMARKETCAL_API_KEY, "Accept": "application/json"},
            params={"cursor": cursor} if cursor else {},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        for c in body.get("data") or []:
            if c.get("slug"):
                out[c["slug"]] = c.get("rank")
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor:
            break
    return out


def check_slugs(session, force: bool = False) -> None:
    """Overi, ci je slug kazdeho tickera stale v bezplatnom plane (2026-09-11,
    na ziadost pouzivatela). PRECO: /v2/events pre neexistujuci alebo nepokryty
    slug vracia HTTP 200 a 0 udalosti - presne to iste ako "ziadne udalosti" -
    takze keby coin vypadol z top-100 (Midnight je 91.), ticker by ticho prestal
    dostavat kalendar a nikto by si to nevsimol.

    Vola sa z poll_events (ten bezi pri kazdom nasadeni aj denne), ale realne
    klope na API len raz za _SLUG_CHECK_DAYS, alebo ked sa slug zmenil /
    pribudol / posledna kontrola zlyhala - cas je v DB, takze redeploy ho
    neresetuje (poucenie z ATR kalibracie, ktora pri redeployoch nebezala nikdy)."""
    import assets  # lokalny import - rovnaky dovod ako v poll_events

    configured = {a["strike_symbol"]: a["coinmarketcal_slug"]
                  for a in assets.ALL_ASSETS if a.get("coinmarketcal_slug")}
    rows = {r.symbol: r for r in session.query(CoinMarketCalSlugStatus).all()}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_SLUG_CHECK_DAYS)

    def _fresh(sym, slug):
        r = rows.get(sym)
        if r is None or r.slug != slug or r.error or r.covered is None:
            return False
        checked = r.checked_at if r.checked_at.tzinfo else r.checked_at.replace(tzinfo=timezone.utc)
        return checked > cutoff

    for sym in set(rows) - set(configured):
        session.delete(rows.pop(sym))  # ticker uz slug nema
    if not force and all(_fresh(s, g) for s, g in configured.items()):
        session.commit()
        return

    try:
        covered, error = _fetch_covered_coins(), None
    except Exception as e:
        covered, error = None, f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[coinmarketcal_client] kontrola slugov zlyhala (skusim pri dalsom polle): {error}")
    for sym, slug in configured.items():
        row = rows.get(sym) or CoinMarketCalSlugStatus(symbol=sym)
        if row.slug != slug:
            row.covered, row.rank = None, None
        row.slug, row.checked_at, row.error = slug, now, error
        if covered is not None:
            row.covered, row.rank = slug in covered, covered.get(slug)
            if not row.covered:
                print(f"[coinmarketcal_client] POZOR: {sym} slug '{slug}' NIE JE v bezplatnom "
                      f"plane - ticker nedostava kalendar udalosti.")
        session.merge(row)
    session.commit()
    if covered is not None:
        print(f"[coinmarketcal_client] kontrola slugov: {sum(1 for g in configured.values() if g in covered)}"
              f"/{len(configured)} pokrytych (plan ma {len(covered)} coinov).")


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _fetch_events(coin_slug: str, limit: int = 10) -> list[dict]:
    resp = requests.get(
        _BASE_URL,
        headers={"x-api-key": config.COINMARKETCAL_API_KEY, "Accept": "application/json"},
        params={"coins": coin_slug, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def poll_events() -> None:
    """Vstupny bod scheduleru (main.py, denne) - pre KAZDY asset s nastavenym
    coinmarketcal_slug (viz assets.py, aj neaktivny - rovnaky vzor ako
    sl_calibration.py) prepocita nadchadzajuce udalosti z CoinMarketCal.
    Kazdy asset izolovane (jeden zlyhany fetch neblokuje ostatne)."""
    if not config.COINMARKETCAL_API_KEY:
        print("[coinmarketcal_client] COINMARKETCAL_API_KEY nie je nastaveny, preskakujem.")
        return

    import assets  # lokalny import - predide cirkularnemu importu (assets nepotrebuje tento modul)

    print(f"\n=== [coinmarketcal_client] poll_events {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        try:
            check_slugs(session)
        except Exception as e:
            session.rollback()
            print(f"[coinmarketcal_client] kontrola slugov spadla (neblokujuce): {e}")
        for asset in assets.ALL_ASSETS:
            slug = asset.get("coinmarketcal_slug")
            if not slug:
                continue
            name = asset["name"]
            symbol = asset["strike_symbol"]
            try:
                events = _fetch_events(slug)
            except Exception as e:
                print(f"[coinmarketcal_client] [{name}] fetch zlyhal (neblokujuce): {e}")
                continue

            session.query(CoinMarketCalEvent).filter(CoinMarketCalEvent.symbol == symbol).delete()
            written = 0
            for e in events:
                date_start = _parse_dt(e.get("date"))
                if date_start is None:
                    continue
                session.add(CoinMarketCalEvent(
                    symbol=symbol,
                    cmc_event_id=str(e.get("id")),
                    title=e.get("title") or "(bez nazvu)",
                    date_start=date_start,
                    date_end=_parse_dt(e.get("dateEnd")),
                    is_estimated=bool(e.get("isEstimated")),
                ))
                written += 1
            print(f"[coinmarketcal_client] [{name}] ({slug}): {written} udalosti zapisanych.")
        session.commit()
    finally:
        session.close()


def get_cached_events(symbol: str, session, limit: int = 5) -> list[dict]:
    """Cita z DB kese (viz poll_events vyssie) - ZIADNE zive API volanie.
    Vrati LEN buduce/prebiehajuce udalosti (efektivny koniec - date_end ak
    existuje, inak date_start - musi byt v buducnosti), zoradene podla
    date_start, najviac `limit`."""
    now = datetime.now(timezone.utc)
    rows = (
        session.query(CoinMarketCalEvent)
        .filter(CoinMarketCalEvent.symbol == symbol)
        .order_by(CoinMarketCalEvent.date_start)
        .all()
    )
    out = []
    for r in rows:
        effective_end = r.date_end or r.date_start
        if effective_end.tzinfo is None:
            effective_end = effective_end.replace(tzinfo=timezone.utc)
        if effective_end < now:
            continue
        out.append({
            "title": r.title, "date_start": r.date_start, "date_end": r.date_end,
            "is_estimated": r.is_estimated,
        })
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    poll_events()
