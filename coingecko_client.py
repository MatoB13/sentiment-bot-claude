"""
Trhove data z CoinGecko - pouziva sa LEN pre HYPE (Hyperliquid), ktore nema
ziaden overeny zdroj inde: nie je na Binance (HYPEUSDT ani HYPEUSDC
neexistuju - overene naozivo 2026-08-07) a yfinance "HYPE-USD" nevracia
ziadne data ("possibly delisted"). Bez tohto zdroja by HYPE mal po pridani
~9-dnovy uplny vypadok OHLC dat (kym vlastny 1-min Strike poller nenazbiera
210 hodinovych barov - viz market_data.MIN_OWN_BARS), pretoze aj yfinance
fallback/backfill (price_poller.backfill_if_empty, market_data.get_price_history)
by ticho vratil prazdny DataFrame.

Rovnaky graceful-degradation vzor ako binance_client.py - zlyhanie tu nesmie
zhodit TA fetch, volajuci (market_data.py) sa postara o fallback na prazdny
DataFrame. POZOR: /coins/{id}/ohlc endpoint NEPOSKYTUJE volume (na rozdiel od
Binance klines) - HYPE preto ma include_volume=False v assets.py.

POZOR (2026-08-08, live produkcny incident): anonymny pristup (bez API kluca)
z Railway zdielanej cloud IP adresy narazil na SPOLAHLIVE 429 Too Many
Requests na KAZDOM volani (rovnaky call z lokalneho pocitaca uspel bez
problemu) - CoinGecko anonymny endpoint zdiela rate-limit kvotu naprieč
VSETKYMI volajucimi z danej IP, nie len touto appkou, a cloud/datacenter IP
adresy (Railway, Render, Heroku a pod.) su bezne uz nasytene inymi
aplikaciami. Volny "Demo" API kluc (bez platobnej karty, registracia na
https://www.coingecko.com/en/developers/dashboard) davava DEDIKOVANU kvotu
(~30 req/min) nezavislu od zdielanej anonymnej kvoty - nastav
COINGECKO_API_KEY, ak je dostupny (config.py). Bez neho modul stale funguje
(anonymny pristup + kratky retry na 429), len menej spolahlivo."""
import time

import requests

import config

_BASE_URL = "https://api.coingecko.com/api/v3"
_TIMEOUT_SECONDS = 15
_RETRY_DELAY_SECONDS = 15
_MAX_ATTEMPTS = 2


def get_ohlc(coin_id: str, days: int = 30) -> list[dict]:
    """coin_id: CoinGecko id (napr. "hyperliquid", NIE ticker symbol). Vrati
    OHLC sviecky zoradene od najstarsej po najnovsiu. Granularita je
    CoinGecko-riadena podla 'days' (3-30 dni = 4h sviecky, nie presne hodinova
    ako Strike vlastny poller) - akceptovatelne pre jednorazovy backfill/
    kratkodoby fallback, nie ako bezny provozny zdroj.

    POZOR: 'days' free-tier endpoint akceptuje LEN presne tieto hodnoty (1/7/
    14/30/90/180/365) - overene naozivo 2026-08-07 (days=5 vratilo 400). Preto
    volajuci (market_data.fetch_ohlcv_coingecko) NIKDY nepouziva ine cislo."""
    headers = {"x-cg-demo-api-key": config.COINGECKO_API_KEY} if config.COINGECKO_API_KEY else {}

    resp = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = requests.get(
            f"{_BASE_URL}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
        if resp.status_code != 429 or attempt == _MAX_ATTEMPTS - 1:
            break
        print(f"[coingecko_client] 429 Too Many Requests pre {coin_id}, "
              f"skusam znova o {_RETRY_DELAY_SECONDS}s ({attempt + 1}/{_MAX_ATTEMPTS - 1})...")
        time.sleep(_RETRY_DELAY_SECONDS)

    resp.raise_for_status()
    rows = resp.json()
    return [
        {"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(get_ohlc("hyperliquid", days=3), indent=2))
