"""Titulky Benzinga cez Alpaca News API - vstup pre LACNY SKEN (2026-09-11, na
ziadost pouzivatela).

PRECO: sken nema web_search, jeho jediny zdroj sprav su titulky. Marketaux pre
cast tickerov nevracia nic (za 24 h: CRCL 0 %, ZEC 33 %) a je oneskoreny.
Benzinga je profesionalna agentura; cez Alpaca sa titulok doruci 0.1-0.2 s po
zverejneni (namerane 11.9. na WebSocket streame). Pozor: samotna Benzinga pri
makro udalostiach nie je rychla - prvy titulok k CPI 11.9. prisiel 18 min po
zverejneni (12:30 -> 12:48 UTC). Nie je to teda zdroj na obchodovanie makra,
ale na firemne a krypto spravy.

POKRYTIE (namerane 11.9. za 7 dni): BTCUSD 88, NVDA 87, AAPL 81, SPY 80,
GOOGL 49, TSLA 48, QQQ 36, ZECUSD 29, USO 26, GLD 6, CRCL 4, AAOI 2; ADA,
NIGHT, PUMP, MINIMAX, UNITREE, ZHIPU 0; SK Hynix 6 titulkov, ale BEZ vlastneho
symbolu (Benzinga ich otagovala na AAPL/AMD/EWY/DRAM...).

ZDIELANY ZASOBNIK (2026-09-11, druha verzia, na namietku pouzivatela: "to, ze
sa o NIGHT nepise posledny tyzden, neznamena, ze sa coskoro nezacne"):
prva verzia sa pytala Alpaca per symbol a tickery bez pokrytia vobec nemala -
keby o nich Benzinga zacala pisat, bot by to nevidel. Teraz sa stahuje CELY
feed (za 12 h ~136 titulkov = 3 strany, potom kazdych ALPACA_NEWS_CACHE_MINUTES
jedna strana len s novymi) a KAZDY ticker si z neho vyberie svoje titulky:
  - podla symbolu (assets.py "alpaca_news_symbols") - Benzinga otagovala, alebo
  - podla nazvu v titulku (assets.py "news_keywords", regexy) - pre firmy bez
    US symbolu (SK Hynix, MiniMax, Unitree, Zhipu) a krypto, ktore Benzinga
    netaguje spolahlivo.
Prazdny vysledok nic nestoji - sken bezi tak ci tak a titulky su len jeho vstup.

Pouziva sa REST (nie WebSocket): sken bezi v case cyklu, nie priebezne, a
Alpaca povoluje len JEDNO WebSocket spojenie na ucet.

NIKDY nevyhodi vynimku - pri zlyhani vrati prazdny zoznam a zapise stav, aby sa
vypadok dal ukazat na dashboarde (rovnaky vzor ako market_news_client).
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import config

_URL = "https://data.alpaca.markets/v1beta1/news"
_TIMEOUT_SECONDS = 10
_PAGE_LIMIT = 50
# Poistka proti nekonecnemu strankovaniu - 12 h feedu su ~3 strany.
_MAX_PAGES = 10
# Pri doplnani sa znova stiahne aj poslednych 30 min - Benzinga titulky casto
# upravi par minut po zverejneni (13 zo 136 za 12 h) a novsia verzia vyhra.
_REFRESH_OVERLAP_MINUTES = 30

# {news_id: {"id", "title", "created", "symbols"}} - zdielane VSETKYMI tickermi.
_pool: dict = {}
_pool_fetched_at: float = 0.0
_pool_status: dict = {"ok": None, "error": None, "size": 0}
# {asset_name: stav posledneho vyberu} - ide do triage.alpaca_news pri cykle.
_last_status: dict = {}
_regex_cache: dict = {}


def enabled() -> bool:
    return bool(config.ALPACA_NEWS_ENABLED and config.ALPACA_API_KEY_ID
                and config.ALPACA_API_SECRET_KEY)


def covers(asset: dict) -> bool:
    """Ma ticker cim hladat? (symbol alebo nazov) - kazdy by mal, test to strazi."""
    return bool(asset.get("alpaca_news_symbols") or asset.get("news_keywords"))


def last_status(asset_name: str) -> dict | None:
    return _last_status.get(asset_name)


def _parse_ts(created_at) -> datetime | None:
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _fetch(start: datetime) -> list[dict]:
    """Vsetky titulky od `start` (vratane), cez strankovanie. Vynimku necha prejst."""
    out, token = [], None
    for _ in range(_MAX_PAGES):
        params = {"start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "sort": "desc",
                  "limit": _PAGE_LIMIT, "include_content": "false"}
        if token:
            params["page_token"] = token
        resp = requests.get(
            _URL, timeout=_TIMEOUT_SECONDS,
            headers={"APCA-API-KEY-ID": config.ALPACA_API_KEY_ID,
                     "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET_KEY},
            params=params)
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("news") or [])
        token = body.get("next_page_token")
        if not token:
            break
    return out


def _refresh_pool() -> None:
    """Doplni zasobnik o nove titulky a vyhodi starsie ako ALPACA_NEWS_MAX_AGE_HOURS.
    Najviac raz za ALPACA_NEWS_CACHE_MINUTES - vsetky tickery v jednom tiku
    zdielaju to iste stiahnutie."""
    global _pool_fetched_at, _pool_status
    now_ts = time.time()
    if _pool_fetched_at and now_ts - _pool_fetched_at < config.ALPACA_NEWS_CACHE_MINUTES * 60:
        return
    now = datetime.now(timezone.utc)
    horizon = now - timedelta(hours=config.ALPACA_NEWS_MAX_AGE_HOURS)
    newest = max((i["created"] for i in _pool.values()), default=None)
    start = horizon if newest is None else max(
        horizon, newest - timedelta(minutes=_REFRESH_OVERLAP_MINUTES))
    error = None
    try:
        for n in _fetch(start):
            created = _parse_ts(n.get("created_at"))
            title = (n.get("headline") or "").strip()
            if created is None or not title or n.get("id") is None:
                continue
            _pool[n["id"]] = {"id": n["id"], "title": title, "created": created,
                              "symbols": list(n.get("symbols") or [])}
    except Exception as e:
        # Kluce sa do chyby nikdy nedostanu - requests ich neuvadza v texte vynimky.
        error = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"[alpaca_news] doplnenie zasobnika zlyhalo (pokracujem so starym): {error}")
    for k in [k for k, v in _pool.items() if v["created"] < horizon]:
        del _pool[k]
    # Aj pri chybe posun hodiny - inak by kazdy ticker v tiku znova klopal na
    # padnuty zdroj (rovnaky vzor ako market_news_client: kesuje sa aj neuspech).
    _pool_fetched_at = now_ts
    _pool_status = {"ok": error is None, "error": error, "size": len(_pool)}


def _keyword_regexes(asset: dict) -> list:
    pats = tuple(asset.get("news_keywords") or ())
    if pats not in _regex_cache:
        _regex_cache[pats] = [re.compile(p, re.IGNORECASE) for p in pats]
    return _regex_cache[pats]


def _match(asset: dict, item: dict) -> str | None:
    """'symbol' / 'nazov' podla toho, co titulok priradilo tickeru, inak None."""
    if set(asset.get("alpaca_news_symbols") or ()) & set(item["symbols"]):
        return "symbol"
    if any(r.search(item["title"]) for r in _keyword_regexes(asset)):
        return "nazov"
    return None


def get_headlines_for_asset(asset: dict) -> list[dict]:
    """Najnovsie titulky tykajuce sa tickera (podla symbolu alebo nazvu), od
    najnovsieho, len do ALPACA_NEWS_MAX_AGE_HOURS, najviac ALPACA_NEWS_MAX_ITEMS.
    [] ked je zdroj vypnuty, ticker nema cim hladat alebo zdroj zlyhal."""
    if not enabled() or not covers(asset):
        return []
    _refresh_pool()
    now = datetime.now(timezone.utc)
    matched = []
    for item in _pool.values():
        by = _match(asset, item)
        if by:
            age = max(0.0, (now - item["created"]).total_seconds() / 3600)
            matched.append({"title": item["title"], "age_hours": round(age, 2), "by": by})
    # Dedup podla titulku (ta ista sprava moze prist pod viacerymi id).
    seen, unique = set(), []
    for i in sorted(matched, key=lambda x: x["age_hours"]):
        k = i["title"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(i)
    result = unique[:config.ALPACA_NEWS_MAX_ITEMS]
    _last_status[asset["name"]] = {
        "ok": _pool_status["ok"], "error": _pool_status["error"],
        "pool_size": _pool_status["size"], "count": len(result),
        "newest_age_hours": result[0]["age_hours"] if result else None,
        # Ktore titulky sli do promptu a PRECO (symbol/nazov) - dashboard ich
        # ukazuje pri cykle, falosne zhody podla nazvu tak budu vidno.
        "items": result,
    }
    return result
