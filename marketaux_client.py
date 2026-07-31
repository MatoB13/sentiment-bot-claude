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
"""
import requests

import config

BASE_URL = "https://api.marketaux.com/v1/news/all"


def get_news_sentiment(query: dict, limit: int = 5) -> list[dict] | None:
    """query: extra parametre pre Marketaux (napr. {"symbols": "QQQ"} alebo
    {"search": "Midnight", "entity_types": "cryptocurrency"} - viz assets.py).
    Vrati zoznam najnovsich clankov (title/source/published_at/sentiment_score/
    url) alebo None ak kluc chyba/API zlyha (nikdy nevyhadzuje)."""
    if not config.MARKETAUX_API_KEY or not query:
        return None

    params = {
        "api_token": config.MARKETAUX_API_KEY,
        "language": "en",
        "limit": limit,
        "sort": "published_desc",
    }
    params.update(query)

    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        articles = resp.json().get("data", [])
    except Exception as e:
        print(f"[marketaux_client] Nepodarilo sa nacitat news (query={query}): {e}")
        return None

    out = []
    for a in articles:
        # entities[0] existuje len pri symbol-based dopyte (nie pri holom 'search'
        # bez entity match) - sentiment_score potom chyba, co je OK, zobrazi sa bez neho.
        entities = a.get("entities") or []
        sentiment = entities[0].get("sentiment_score") if entities else None
        out.append({
            "title": a.get("title"),
            "source": a.get("source"),
            "published_at": a.get("published_at"),
            "sentiment_score": sentiment,
            "url": a.get("url"),
        })
    return out


if __name__ == "__main__":
    print(get_news_sentiment({"symbols": "QQQ"}))
