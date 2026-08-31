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
# 2026-08-31 (na ziadost pouzivatela) - long/short account ratio pre squeeze-risk
# kontext (viz market_data.get_long_short_snapshot). INY DOMAIN nez klines
# vyssie (fapi.binance.com = futures data, nie spot data-api.binance.vision) -
# je to verejny, neautentifikovany endpoint (rovnaky "market data" charakter
# ako klines), ale NEOVERENE, ci ma rovnaku vynimku z US regionalneho blokovania
# ako oficialny data-api.binance.vision mirror. Zlyhanie MUSI byt rovnako
# neblokujuce ako vyssie - volajuci (market_data.py) jednoducho vynecha polia.
_FUTURES_DATA_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"


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


def get_long_short_ratio(symbol: str) -> dict | None:
    """Najnovsi "global long/short account ratio" (podiel VSETKYCH Binance
    futures uctov s otvorenou dlhou vs kratkou poziciou na danom symbole,
    NIE vazene velkostou pozicie) - verejny endpoint, ziadny API kluc.
    Vrati {"long_pct", "short_pct", "long_short_ratio"} alebo None pri
    zlyhani (symbol nema futures market, region blok, timeout a pod.)."""
    resp = requests.get(
        _FUTURES_DATA_URL,
        params={"symbol": symbol, "period": "1h", "limit": 1},
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    r = rows[-1]
    return {
        "long_pct": round(float(r["longAccount"]) * 100, 1),
        "short_pct": round(float(r["shortAccount"]) * 100, 1),
        "long_short_ratio": round(float(r["longShortRatio"]), 3),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_hourly_klines("ADAUSDT", limit=3), indent=2))
