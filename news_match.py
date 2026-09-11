"""Priradenie titulku k tickeru podla NAZVU (2026-09-11, na ziadost pouzivatela).

Zdielane zdrojmi, ktore stahuju cely feed a ticker si z neho vybera svoje
titulky - Benzinga (alpaca_news_client) a krypto RSS (market_news_client).
Regexy su v assets.py "news_keywords", bez ohladu na velkost pismen. Hole
bezne slova (midnight, hype, near, pump) tam zamerne nie su - viz komentare
pri tickeroch.
"""
import re

_regex_cache: dict = {}


def keyword_regexes(asset: dict) -> list:
    pats = tuple(asset.get("news_keywords") or ())
    if pats not in _regex_cache:
        _regex_cache[pats] = [re.compile(p, re.IGNORECASE) for p in pats]
    return _regex_cache[pats]


def mentions(asset: dict, title: str) -> bool:
    """Spomina titulok ticker podla niektoreho z jeho news_keywords?"""
    return any(r.search(title or "") for r in keyword_regexes(asset))
