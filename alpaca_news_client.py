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
GOOGL 49, TSLA 48, QQQ 36, ZECUSD 29, USO 26, GLD 6, CRCL 4, AAOI 2; ADA 0.
Preto per-ticker mapovanie v assets.py ("alpaca_news_symbols") - tickery bez
pokrytia ho nemaju.

Pouziva sa REST (nie WebSocket): sken bezi v case cyklu, nie priebezne, a
Alpaca povoluje len JEDNO WebSocket spojenie na ucet.

NIKDY nevyhodi vynimku - pri zlyhani vrati prazdny zoznam a zapise stav, aby sa
vypadok dal ukazat na dashboarde (rovnaky vzor ako market_news_client).
"""
import time
from datetime import datetime, timedelta, timezone

import requests

import config

_URL = "https://data.alpaca.markets/v1beta1/news"
_TIMEOUT_SECONDS = 10

# {tuple(symbols): (fetched_at, items)} - tie iste symboly (napr. pri opakovanom
# cykle toho isteho tickera) sa v ramci ALPACA_NEWS_CACHE_MINUTES nestahuju znova.
_cache: dict = {}
_last_status: dict = {}


def enabled() -> bool:
    return bool(config.ALPACA_NEWS_ENABLED and config.ALPACA_API_KEY_ID
                and config.ALPACA_API_SECRET_KEY)


def last_status(symbols) -> dict | None:
    return _last_status.get(tuple(symbols))


def _age_hours(created_at: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return max(0.0, (now - dt).total_seconds() / 3600)


def get_headlines(symbols) -> list[dict]:
    """Najnovsie titulky pre dane Alpaca symboly (napr. ["QQQ"], ["BTCUSD"]),
    od najnovsieho, len do ALPACA_NEWS_MAX_AGE_HOURS. [] ked je zdroj vypnuty
    alebo zlyhal."""
    symbols = tuple(symbols or ())
    if not symbols or not enabled():
        return []
    now_ts = time.time()
    hit = _cache.get(symbols)
    if hit and now_ts - hit[0] < config.ALPACA_NEWS_CACHE_MINUTES * 60:
        return hit[1]

    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=config.ALPACA_NEWS_MAX_AGE_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict] = []
    error = None
    try:
        resp = requests.get(
            _URL, timeout=_TIMEOUT_SECONDS,
            headers={"APCA-API-KEY-ID": config.ALPACA_API_KEY_ID,
                     "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET_KEY},
            params={"symbols": ",".join(symbols), "start": start, "sort": "desc",
                    "limit": 50, "include_content": "false"})
        resp.raise_for_status()
        for n in resp.json().get("news", []):
            age = _age_hours(n.get("created_at"), now)
            title = (n.get("headline") or "").strip()
            if age is None or not title or age > config.ALPACA_NEWS_MAX_AGE_HOURS:
                continue
            items.append({"title": title, "age_hours": round(age, 2),
                          "symbols": n.get("symbols") or [], "source": n.get("source") or "benzinga"})
    except Exception as e:
        # Kluce sa do chyby nikdy nedostanu - requests ich neuvadza v texte vynimky.
        error = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"[alpaca_news] {','.join(symbols)} zlyhal (pokracujem bez titulkov): {error}")

    # Dedup podla titulku (ta ista sprava moze byt otagovana viac symbolmi).
    seen, unique = set(), []
    for i in sorted(items, key=lambda x: x["age_hours"]):
        k = i["title"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(i)
    result = unique[:config.ALPACA_NEWS_MAX_ITEMS]
    _cache[symbols] = (now_ts, result)
    _last_status[symbols] = {
        "ok": error is None, "count": len(result), "error": error,
        "newest_age_hours": result[0]["age_hours"] if result else None,
        # Ktore titulky sli do promptu - dashboard ich ukazuje pri cykle.
        "items": [{"title": i["title"], "age_hours": i["age_hours"]} for i in result],
    }
    return result
