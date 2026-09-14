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

AJ DO PLNEHO CYKLU, S TEXTOM (2026-09-14, na ziadost pouzivatela, po NVDA):
vyzvu Altmana/Amodeia na spomalenie AI mala Benzinga v piatok 17:05 UTC, sken
ju dvakrat odbil ako "nesuvisiacu s tezou" a plne cykly Benzingu vobec nevideli
- NVDA sa to dozvedelo az v pondelok. Preto:
  - kazda polozka nesie `new` = vysla PO poslednom plnom pohlade na ticker
    (`since`); nove sa zobrazuju vsetky (do ALPACA_NEWS_MAX_NEW_ITEMS), stare
    len dopĺňaju do ALPACA_NEWS_MAX_ITEMS,
  - zasobnik drzi ALPACA_NEWS_POOL_HOURS (36 h), nie len 12 h - posledny plny
    pohlad moze byt starsi nez 12 h a sprava medzi tym by inak prepadla,
  - stahuje sa aj plny text (include_content); do promptu plneho cyklu ide len
    pri NOVYCH clankoch (viz full_text_ids). Summary od Alpaca je jedna veta,
    plny text 2-10 tis. znakov, preto sa kazdy skracuje.

NIKDY nevyhodi vynimku - pri zlyhani vrati prazdny zoznam a zapise stav, aby sa
vypadok dal ukazat na dashboarde (rovnaky vzor ako market_news_client).
"""
import html
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import news_match

_URL = "https://data.alpaca.markets/v1beta1/news"
_TIMEOUT_SECONDS = 15
_PAGE_LIMIT = 50
# Poistka proti nekonecnemu strankovaniu - 36 h feedu su ~9 stran.
_MAX_PAGES = 20
# Rutinne titulky (ratingy, opcie, prehlady) - plny text dostanu az ked na
# dolezitejsie nove clanky nezostane miesto. Titulok vidi Claude vzdy.
_ROUTINE_RE = re.compile(
    r"price target|options activity|unusual options|whales?\b|short interest|"
    r"top (gainers|losers)|\bmovers\b|what'?s going on with|p/e ratio|"
    r"analyst ratings?|(upgrades|downgrades|maintains|reiterates)\b", re.I)
# Chvost clankov Benzinga (odkazy na dalsie clanky, obrazky) - nic neprinasa.
_TAIL_RE = re.compile(r"\b(Read Next|Read More|Photo(?: courtesy)?)\s*:", re.I)
# Pri doplnani sa znova stiahne aj poslednych 30 min - Benzinga titulky casto
# upravi par minut po zverejneni (13 zo 136 za 12 h) a novsia verzia vyhra.
_REFRESH_OVERLAP_MINUTES = 30

# {news_id: {"id", "title", "created", "symbols", "text"}} - zdielane VSETKYMI tickermi.
_pool: dict = {}
_pool_fetched_at: float = 0.0
_pool_status: dict = {"ok": None, "error": None, "size": 0}
# {asset_name: stav posledneho vyberu} - ide do triage.alpaca_news pri cykle.
_last_status: dict = {}
# 2026-09-14 - zasobnik citaju cykly (paralelne vlakna) aj news_watch; bez zamku
# by iteracia pocas doplnania mohla spadnut na "dictionary changed size".
_lock = threading.Lock()


def _pool_snapshot() -> list[dict]:
    with _lock:
        _refresh_pool()
        return list(_pool.values())


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


def _plain_text(content: str | None, summary: str | None) -> str:
    """HTML clanku -> cisty text skrateny na ALPACA_NEWS_FULL_TEXT_CHARS (na
    hranici vety/slova). Bez obsahu padne na jednovetove summary."""
    text = re.sub(r"<[^>]+>", " ", content or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    # Hlada sa az v druhej casti - "Photo:" na zaciatku je popis obrazka, nie chvost.
    tail = _TAIL_RE.search(text, int(len(text) * 0.3))
    if tail:
        text = text[:tail.start()].rstrip()
    if not text:
        text = re.sub(r"\s+", " ", html.unescape(summary or "")).strip()
    limit = max(0, int(config.ALPACA_NEWS_FULL_TEXT_CHARS))
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        cut = cut[:end + 1] if end >= limit * 0.6 else cut.rsplit(" ", 1)[0]
        text = cut.rstrip() + " […]"
    return text


def _fetch(start: datetime) -> list[dict]:
    """Vsetky titulky od `start` (vratane), cez strankovanie. Vynimku necha prejst."""
    out, token = [], None
    for _ in range(_MAX_PAGES):
        params = {"start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "sort": "desc",
                  "limit": _PAGE_LIMIT, "include_content": "true"}
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
    horizon = now - timedelta(hours=_pool_hours())
    newest = max((i["created"] for i in _pool.values()), default=None)
    start = horizon if newest is None else max(
        horizon, newest - timedelta(minutes=_REFRESH_OVERLAP_MINUTES))
    error = None
    try:
        for n in _fetch(start):
            created = _parse_ts(n.get("created_at"))
            title = html.unescape(n.get("headline") or "").strip()
            if created is None or not title or n.get("id") is None:
                continue
            _pool[n["id"]] = {"id": n["id"], "title": title, "created": created,
                              "symbols": list(n.get("symbols") or []),
                              "text": _plain_text(n.get("content"), n.get("summary"))}
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


def _match(asset: dict, item: dict) -> str | None:
    """'symbol' / 'nazov' podla toho, co titulok priradilo tickeru, inak None."""
    if set(asset.get("alpaca_news_symbols") or ()) & set(item["symbols"]):
        return "symbol"
    if news_match.mentions(asset, item["title"]):
        return "nazov"
    return None


def _pool_hours() -> float:
    return max(config.ALPACA_NEWS_MAX_AGE_HOURS, config.ALPACA_NEWS_POOL_HOURS)


def is_routine(title: str) -> bool:
    return bool(_ROUTINE_RE.search(title or ""))


def get_headlines_for_asset(asset: dict, since: datetime | None = None) -> list[dict]:
    """Titulky tykajuce sa tickera (podla symbolu alebo nazvu), od najnovsieho.
    [] ked je zdroj vypnuty, ticker nema cim hladat alebo zdroj zlyhal.

    since = cas posledneho PLNEHO pohladu na ticker (trade_cycle._last_full_look_at).
    Clanky vydane po nom maju new=True a ukazu sa VSETKY (do ALPACA_NEWS_MAX_NEW_ITEMS),
    aj ked su starsie nez ALPACA_NEWS_MAX_AGE_HOURS - tie clanky analytik este
    nevidel. Stare len doplnia do ALPACA_NEWS_MAX_ITEMS. since=None (plny pohlad
    este nebol) = nove je vsetko do ALPACA_NEWS_MAX_AGE_HOURS.

    Polozka: title, age_hours, by, new, full_text (ci ide do plneho cyklu aj
    text) a text. `text` sa do stavu (a teda do DB) nezapisuje."""
    if not enabled() or not covers(asset):
        return []
    pool = _pool_snapshot()
    now = datetime.now(timezone.utc)
    if since is None:
        since = now - timedelta(hours=config.ALPACA_NEWS_MAX_AGE_HOURS)
    elif since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    matched = []
    for item in pool:
        by = _match(asset, item)
        if not by:
            continue
        age = max(0.0, (now - item["created"]).total_seconds() / 3600)
        new = item["created"] > since
        if not new and age > config.ALPACA_NEWS_MAX_AGE_HOURS:
            continue
        matched.append({"title": item["title"], "age_hours": round(age, 2), "by": by,
                        "new": new, "full_text": False, "text": item.get("text") or ""})
    # Dedup podla titulku (ta ista sprava moze prist pod viacerymi id).
    seen, unique = set(), []
    for i in sorted(matched, key=lambda x: x["age_hours"]):
        k = i["title"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(i)
    new_items = [i for i in unique if i["new"]][:max(0, config.ALPACA_NEWS_MAX_NEW_ITEMS)]
    old_items = [i for i in unique if not i["new"]]
    result = new_items + old_items[:max(0, config.ALPACA_NEWS_MAX_ITEMS - len(new_items))]
    # Plny text pre najviac ALPACA_NEWS_FULL_TEXT_ITEMS novych clankov: najprv
    # tie, co ticker spominaju v titulku, potom ostatne nerutinne, rutinne az
    # ked zostane miesto (v kazdej skupine od najnovsieho). Benzinga taguje aj
    # suhrnne clanky (ZEC dostaval politicke memecoin spravy s tagom ZECUSD).
    with_text = [i for i in new_items if i["text"]]

    def _rank(i):
        if news_match.mentions(asset, i["title"]):
            return 0
        return 2 if is_routine(i["title"]) else 1
    ranked = sorted(with_text, key=lambda i: (_rank(i), i["age_hours"]))
    for i in ranked[:max(0, config.ALPACA_NEWS_FULL_TEXT_ITEMS)]:
        i["full_text"] = True
    _last_status[asset["name"]] = {
        "ok": _pool_status["ok"], "error": _pool_status["error"],
        "pool_size": _pool_status["size"], "count": len(result),
        "new_count": len(new_items),
        "since": since.isoformat(),
        "newest_age_hours": result[0]["age_hours"] if result else None,
        # Ktore titulky sli do promptu a PRECO (symbol/nazov) - dashboard ich
        # ukazuje pri cykle, falosne zhody podla nazvu tak budu vidno. Bez textu.
        "items": [{k: v for k, v in i.items() if k != "text"} for i in result],
    }
    return result


def items_for_asset(asset: dict) -> list[dict]:
    """VSETKY titulky zo zasobnika priradene tickeru, bez stropov a BEZ zapisu stavu
    (last_status patri cyklom) - pre tienove meranie news_watch (2026-09-14).
    [{title, created, routine}] od najnovsieho, dedup podla titulku."""
    if not enabled() or not covers(asset):
        return []
    seen, out = set(), []
    for item in sorted(_pool_snapshot(), key=lambda x: x["created"], reverse=True):
        if not _match(asset, item):
            continue
        k = item["title"].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({"title": item["title"], "created": item["created"],
                    "routine": is_routine(item["title"])})
    return out
