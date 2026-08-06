"""
Verejne (bez API kluca) trhove data z Binance - pouziva sa LEN ako doplnkovy
zdroj OBJEMU pre ADA/NIGHT (viz market_data._merge_volume_from_binance), kde
yfinance pokrytie je prilis riedke/nespolahlive (~41% barov pre ADA, NIGHT
pravdepodobne mimo yfinance pokrytia celkom). Kryptomeny SU na Binance
skutocne obchodovane s realnym objemom (na rozdiel od NAS100/NVDA/WTI, ktore
tam vobec nie su - preto tie ostavaju na yfinance).

POZOR - geograficke obmedzenie: Binance regulatorne blokuje pristup z
niektorych regionov (najma US). Pouziva sa dedikovany "market data-only"
mirror (data-api.binance.vision) namiesto hlavneho api.binance.com - podla
Binance dokumentacie ma mensie restrikcie nez hlavne obchodne/autentifikovane
API, ale negarantuje pristup zo VSETKYCH regionov (Railway worker beží v
sfo/US - viz diskusia o presune regionu). Zlyhanie tu NESMIE zhodit TA fetch -
volajuci (market_data.py) pouziva rovnaky graceful-degradation vzor ako pri
yfinance volume enrichmente (chybajuci udaj -> NaN, nie 0.0)."""
import requests

_BASE_URL = "https://data-api.binance.vision/api/v3/klines"
_TIMEOUT_SECONDS = 15


def get_hourly_klines(symbol: str, limit: int = 500) -> list[dict]:
    """symbol: Binance formát bez pomlčky/lomky (napr. "ADAUSDT", "NIGHTUSDT").
    Vrati zoznam {"open_time" (ms epoch UTC), "close", "volume"} zoradenych od
    najstarsieho po najnovsi. limit max 1000 (Binance API strop)."""
    resp = requests.get(
        _BASE_URL,
        params={"symbol": symbol, "interval": "1h", "limit": limit},
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    rows = resp.json()
    return [
        {"open_time": r[0], "close": float(r[4]), "volume": float(r[5])}
        for r in rows
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(get_hourly_klines("ADAUSDT", limit=3), indent=2))
