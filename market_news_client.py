"""Zdielane TRHOVE krypto titulky z verejnych RSS - vstup pre LACNY SKEN.

PRECO VZNIKOL (2026-09-05, meranie na ziadost pouzivatela):
Marketaux nevracia pre mensie krypto tickery NIC. Za 7 dni: ADA 0 %, NEAR 0 %,
PUMP 0 %, NIGHT 0 %, MINIMAX 0 %, ZEC 4 % (oproti GOOGL 98 %, BTC 89 %).
Sken pritom NEMA web_search - jeho jediny zdroj sprav su prave tie titulky -
takze pre tie tickery rozhodoval vylucne z TA.

PRECO NEPOMOHOL INY DODAVATEL: overene na dvoch nezavislych RSS zdrojoch
(Cointelegraph, CryptoSlate) - najnovsi clanok pre ADA mal 70 h, pre ZEC 205 h
a pre NEAR 2137 h (89 dni). Nie je to chyba zdroja, tie spravy v danom case
neexistuju. CryptoPanic zrusil volny plan, Alpha Vantage ma 25 volani/den
(robime ~136), Finnhub nema krypto per-symbol.

CO NAOPAK EXISTUJE: trhove feedy su cerstve (10-15 poloziek do 25 h) a
relevantne spravy v nich su - len nie otagovane po tickeroch. The Block mal v
ten isty den "Zcash tops $1,000" aj "Trump wants Hyperliquid in the US".
Pre mensie altcoiny je aj tak driverom trh, nie vlastna sprava.

NAVRH JE ZAMERNE UZKY: jedno stiahnutie zdielane VSETKYMI tickermi (nie per
ticker), len do skenu, a zapnute len tam, kde ma Marketaux nulu. Sken je filter -
posudit, ci sa titulok tyka jeho tickera, je presne jeho uloha.

RIZIKO: vseobecne titulky mozu vyvolat falosne "ANO", teda drahy plny cyklus
kvoli sprave, ktora s tickerom nesuvisi. Preto sa zapina po jednom tickeri a
meria sa dopad na skip rate (viz karta "Trhove titulky v skene" v dashboarde).
"""
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import config

# Dva zdroje kvoli redundancii - ked jeden vypadne alebo rate-limituje, druhy
# feed drzi sluzbu. Namerane 5.9.: CryptoSlate 10/10 poloziek do 25 h (najnovsi
# 0 h), The Block 10 do 25 h. Cointelegraph/Decrypt maju viac poloziek, ale
# starsich - tieto dva staci.
_FEEDS = (
    "https://cryptoslate.com/feed/",
    "https://www.theblock.co/rss.xml",
)
_TIMEOUT_SECONDS = 12
_USER_AGENT = "Mozilla/5.0 (compatible; nas100-sentiment-bot/1.0)"

# {(): (fetched_at_epoch, result)} - kluc je prazdny, feed je zdielany pre vsetkych
_cache: tuple[float, list[dict]] | None = None


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    """RSS -> [{"title", "age_hours", "source"}], len polozky s datumom."""
    out = []
    root = ET.fromstring(xml_bytes)
    now = datetime.now(timezone.utc)
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        raw_date = item.findtext("pubDate")
        if not title or not raw_date:
            continue
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = (now - published).total_seconds() / 3600
        if age < 0:
            age = 0.0
        out.append({"title": title, "age_hours": round(age, 1)})
    return out


def get_market_headlines() -> list[dict]:
    """Cerstve trhove krypto titulky, zoradene od najnovsieho.

    NIKDY nevyhodi vynimku - pri zlyhani vrati prazdny zoznam. Sken bez titulkov
    funguje presne ako doteraz, takze vypadok zdroja nesmie zhodit cyklus.
    Vysledok sa kesuje (aj prazdny), aby jeden vypadok neznamenal opakovane
    volanie pri kazdom tickeri v tom istom tiku."""
    global _cache
    if not config.MARKET_NEWS_ENABLED:
        return []
    now = time.time()
    if _cache is not None and now - _cache[0] < config.MARKET_NEWS_CACHE_MINUTES * 60:
        return _cache[1]

    items: list[dict] = []
    for url in _FEEDS:
        try:
            resp = requests.get(url, timeout=_TIMEOUT_SECONDS,
                                 headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            items.extend(_parse_feed(resp.content))
        except Exception as e:
            print(f"[market_news] {url} zlyhal (pokracujem): {e}")

    fresh = [i for i in items if i["age_hours"] <= config.MARKET_NEWS_MAX_AGE_HOURS]
    # Dedup podla titulku - tie iste agenturne spravy chodia cez viac feedov.
    seen, unique = set(), []
    for i in sorted(fresh, key=lambda x: x["age_hours"]):
        key = i["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(i)
    result = unique[:config.MARKET_NEWS_MAX_ITEMS]
    _cache = (now, result)
    print(f"[market_news] {len(result)} cerstvych titulkov "
          f"(z {len(items)} stiahnutych, limit {config.MARKET_NEWS_MAX_AGE_HOURS} h)")
    return result


if __name__ == "__main__":
    for h in get_market_headlines():
        print(f"  [pred {h['age_hours']:.0f}h] {h['title']}")
