"""
Marketaux (https://www.marketaux.com) klient - free tier (~100 requestov/den),
news + per-entity sentiment skore. Per-asset (viz assets.py 'marketaux_query') -
kazdy asset ma vlastny presny dopyt, nie vseobecne kluc. slovo.

POZOR (2026-07-31, live overene): "symbols=NIGHT" ani "search=NIGHT" sa
NESMIE pouzit - "night" je bezne anglicke slovo a vratil 87k+ irelevantnych
vysledkov (Wendy's, "Night Owl Capital"...). Pre NIGHT (Midnight/Cardano) sa
pouziva "search=Midnight" + "entity_types=cryptocurrency" (overene: cistych
~284 relevantnych vysledkov). Podobne krypto potrebuje "ADAUSD" tvar symbolu
(holé "ADA" alebo "BTC" davaju falosne zhody s nesuvisiacimi tickermi/ETF).

POZOR (2026-08-08, live produkcny incident): vsetkych 9 tickerov ma teraz
marketaux_query a vola sa 1x za KAZDY dokonceny cyklus (viz trade_cycle.py) -
pri realnych produkcnych intervaloch to je ~147 volani/den, teda nad volnym
~100/den limitom (potvrdene emailom "Request Limit Reached"). Spravy sa
realne nemenia kazdu hodinu, preto jednoduchy in-memory cache podla presneho
dopytu (config.MARKETAUX_CACHE_HOURS, default 3h) - VRATANE zlyhanych/None
vysledkov (opakovanie rate-limitovaneho volania skorej by len spotrebovalo
dalsiu kvotu bez uzitku). Cache je per-proces (nie perzistentna cez restart) -
to je v poriadku, worker bezi ako dlhozijuci proces (main.py loop).

POZOR (2026-08-19, na ziadost pouzivatela, po upgrade na Basic plan): live
analyza realnych dopytov ukazala, ze pre viacero tickerov (ADA/ZEC/NIGHT) je
aj NAJNOVSI dostupny clanok pod danym dopytom 65-70+ dni stary (ZEC/NIGHT
dokonca az 3+ roky pri starsich vysledkoch) - obycajny pocet-limit (5 vs 10
vs 20 clankov) by to nevyriesil, len by menil KOLKO starych clankov sa
posle. Preto teraz FRESHNESS FILTER (config.MARKETAUX_MAX_ARTICLE_AGE_HOURS,
25h - tesne nad POSITION_MAX_HOURS a najdlhsim beznym cyklom, viz config.py
komentar) namiesto/popri pocte - clanky nad tento vek sa do promptu vobec
nedostanu (radsej 0 clankov nez zavadzajuco stare).

POZOR (2026-09-01, po externom audite): povodne sa cerstvost riesila LEN
lokalne, s predpokladom "API vracia zoradene podla datumu, staci pri prvom
starom clanku prestat". Ten predpoklad plati pre `symbols` dopyty, ale pri
`search` dopytoch Marketaux poradie NEDODRZIAVA (overene naozivo - veky prisli
ako 35101, 41024, 23752, 44380 h a ziadna hodnota parametra `sort` to nezmenila).
Tickery so `search` dopytom preto systematicky vracali prazdno, hoci cerstve
clanky existovali. Teraz sa posiela `published_after` a filtruje az API;
lokalny filter ostava len ako poistka a uz nerobi break."""
import time
from datetime import datetime, timedelta, timezone

import requests

import config

BASE_URL = "https://api.marketaux.com/v1/news/all"

# Kolko clankov sa ziada OD MARKETAUX (surovy strop pred freshness filtrom) -
# Basic plan dovoluje az 20/request, netreba sa obmedzovat na strane requestu,
# ked filtrovanie aj tak orezava vysledok podla veku nizsie.
_RAW_FETCH_LIMIT = 20
# Strop na POCET clankov v prompte PO freshness filtri - aj velmi "novinova"
# tickery (napr. GOOGL) nemaju prepchat prompt desiatkami redundantnych
# titulkov o tej istej udalosti.
_MAX_RESULT_ARTICLES = 8

# {cache_key: (fetched_at_epoch_seconds, result)}
_cache: dict[str, tuple[float, list[dict] | None]] = {}


def _normalize(text: str) -> str:
    """Len pismena a cislice, male - aby "SK Hynix" naslo aj "SKHynix" a
    "Circle Internet" aj "Circle Internet Group (CRCL)"."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _is_relevant(article: dict, query: dict) -> bool:
    """Vyskytuje sa hladany vyraz naozaj v titulku alebo snippete?

    2026-09-01 (po externom audite): Marketaux pri `search` dopytoch vracia aj
    clanky, kde sa hladane slovo NIKDE v texte nenachadza - matchuje cez vlastne
    tagy/entity. Overene naozivo:
      - search "Cardano" vratil "Virtune AB completed the rebalancing" a
        "Bitcoin holds near $79K" - slovo Cardano ani v jednom titulku/snippete
      - search "Midnight" (bez entity_types) vratil indicke danove priznania,
        iPhone 18, ceny CNG v Mumbai a thajsky rezort
    Taky "kontext" je pre rozhodovanie horsi nez ziadny - Claude by staval tezu
    na clankoch o inom aktive.

    Tyka sa LEN `search` dopytov. Pri `symbols` dopytoch je entita priradena
    burzovym symbolom, takze clanok o nej je relevantny, aj ked ju v titulku
    nemenuje (napr. "Tech Stocks Retreat" pre QQQ) - tam sa nefiltruje.

    Viacslovna fraza sa overuje PO SLOVACH: staci, ze sa v texte vyskytnu
    vsetky (v lubovolnom poradi), inak by "SK Hynix" nenaslo "SKHynix and
    Nvidia Collaborate"."""
    search = (query or {}).get("search")
    if not search:
        return True  # symbols-based dopyt - nefiltrujeme

    haystack = _normalize((article.get("title") or "") + " " + (article.get("snippet") or ""))
    # operatory Marketaux syntaxe ("+", "|", uvodzovky) nie su sucastou hladaneho textu
    words = [w for w in search.replace('"', " ").replace("+", " ").replace("|", " ").split() if w]
    if not words:
        return True
    return all(_normalize(w) in haystack for w in words)


def _cache_key(query: dict, limit: int) -> str:
    return str(sorted(query.items())) + f"|limit={limit}"


def get_news_sentiment(query: dict, limit: int = _RAW_FETCH_LIMIT) -> list[dict] | None:
    """query: extra parametre pre Marketaux (napr. {"symbols": "QQQ"} alebo
    {"search": "Midnight", "entity_types": "cryptocurrency"} - viz assets.py).
    Vrati zoznam najnovsich, DOSTATOCNE CERSTVYCH clankov (title/source/
    published_at/age_hours/sentiment_score/snippet/url) alebo None ak kluc
    chyba/API zlyha (nikdy nevyhadzuje) - prazdny zoznam [] je legitimny
    vysledok (ziadny dostatocne cerstvy clanok, viz freshness filter v
    docstringu modulu). Vysledok pre presne tento dopyt sa cachuje na
    config.MARKETAUX_CACHE_HOURS."""
    if not config.MARKETAUX_API_KEY or not query:
        return None

    key = _cache_key(query, limit)
    cached = _cache.get(key)
    if cached is not None:
        fetched_at, result = cached
        age_hours = (time.time() - fetched_at) / 3600
        if age_hours < config.MARKETAUX_CACHE_HOURS:
            return result

    # 2026-09-01 - CERSTVOST SA VYNUCUJE NA STRANE API cez published_after.
    # Predtym sa spoliehalo na "sort=published_desc" + break pri prvom starom
    # clanku (viz nizsie). Live test ukazal DVE veci:
    #   1. "sort" Marketaux pri `search` dopytoch IGNORUJE - vysledky prisli
    #      v poradi 35101, 41024, 23752, 44380 hodin a ziadny variant hodnoty
    #      (published_desc / published_on / sort_order=desc / bez sortu) na tom
    #      nic nezmenil. Pri `symbols` dopytoch zoradene su.
    #   2. break-pri-prvom-starom preto pri `search` zahadzoval CERSTVE clanky,
    #      ktore lezali dalej v zozname - tickery s `search` dopytom (HYPE,
    #      NIGHT, MINIMAX, UNITREE, SKHYNIX) tak systematicky vracali prazdno.
    # published_after je overene spolahlivy: "search=Cardano" bez neho vratil
    # 0 cerstvych, s nim 2 (stare 3h a 10h).
    published_after = (datetime.now(timezone.utc)
                       - timedelta(hours=config.MARKETAUX_MAX_ARTICLE_AGE_HOURS)
                       ).strftime("%Y-%m-%dT%H:%M")
    params = {
        "api_token": config.MARKETAUX_API_KEY,
        "language": "en",
        "limit": limit,
        "published_after": published_after,
    }
    params.update(query)

    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        articles = resp.json().get("data", [])
    except Exception as e:
        print(f"[marketaux_client] Nepodarilo sa nacitat news (query={query}): {e}")
        _cache[key] = (time.time(), None)
        return None

    now = datetime.now(timezone.utc)
    out = []
    for a in articles:
        published_at = a.get("published_at")
        try:
            pub_dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue  # bez pouzitelneho datumu nevieme overit cerstvost - radsej preskocit
        age_hours = (now - pub_dt).total_seconds() / 3600
        if age_hours > config.MARKETAUX_MAX_ARTICLE_AGE_HOURS:
            # `continue`, NIE `break` - poradie sa uz nepredpoklada (viz komentar
            # pri published_after vyssie). Filter tu ostava ako druha poistka pre
            # pripad, ze by API published_after niekedy ignorovalo; vyhodit ho by
            # znamenalo tichu zavislost na spravani cudzieho API.
            continue

        if not _is_relevant(a, query):
            continue

        # entities[0] existuje len pri symbol-based dopyte (nie pri holom 'search'
        # bez entity match) - sentiment_score potom chyba, co je OK, zobrazi sa bez neho.
        entities = a.get("entities") or []
        sentiment = entities[0].get("sentiment_score") if entities else None
        out.append({
            "title": a.get("title"),
            "source": a.get("source"),
            "published_at": published_at,
            "age_hours": age_hours,
            "sentiment_score": sentiment,
            "url": a.get("url"),
            # 2026-08-19 (na ziadost pouzivatela) - API toto uz aj tak posiela v
            # tej istej odpovedi (ZIADNY extra request/naklad), ale doteraz sa
            # to nikde nezachytavalo ani neposielalo do promptu - Claude tak
            # videl LEN holy titulok, nikdy skutocny obsah clanku. snippet je
            # kratky vytah (par viet), nie plny text.
            "snippet": a.get("snippet"),
        })
        if len(out) >= _MAX_RESULT_ARTICLES:
            break

    _cache[key] = (time.time(), out)
    return out


if __name__ == "__main__":
    print(get_news_sentiment({"symbols": "QQQ"}))
