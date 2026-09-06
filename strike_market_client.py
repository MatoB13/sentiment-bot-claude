"""Verejna (NEAUTENTIFIKOVANA) market-data cast Strike API.

ZAMERNE SAMOSTATNY MODUL, nie rozsirenie strike_client.py:

1. Ina base cesta. Obchodna cast bezi na `https://api.strikefinance.org`,
   tato na `https://api.strikefinance.org/price`.
2. Ziadny podpis. strike_client podpisuje kazdu poziadavku Ed25519 klucom
   z .env; tu netreba nic - su to verejne data.
3. Ziadne mutacie. Tento modul NIKDY nic nemeni, len cita. Preto sa ho netyka
   ani testovacia zamka v strike_client._request (tá blokuje ne-GET volania),
   a naopak - keby sem niekedy pribudol zapis, patri do strike_client, nie sem.

Objavene 2026-09-06 pri otazke, ci sa da forwardovat Strike Discord kanal
#strike-feed. Ukazalo sa, ze ten kanal je len renderovanie dat, ktore Strike
vydava strukturovane a verejne - s ID sekvenciou a milisekundovou presnostou.
"""
import requests

_BASE_URL = "https://api.strikefinance.org/price"
_TIMEOUT_SECONDS = 20

# Strop endpointu. Namerane, kolko historie jedno volanie pokryje:
# ZEC 10.8 h (najrusnejsi), NEAR 17.4 h, BTC 20.7 h, ADA 45 h, WTI/XAU ~145 h,
# NAS100 1058 h. Aj pri najrusnejsom tickeri teda hodinovy zber s velkou
# rezervou staci na to, aby v rade nevznikla diera.
MAX_TRADES_LIMIT = 1000


def get_recent_trades(symbol: str, limit: int = MAX_TRADES_LIMIT) -> list[dict]:
    """Verejna tape symbolu - obchody VSETKYCH uzivatelov, od najnovsieho.

    Vrati [{"id", "ts_ms", "price", "qty", "quote_qty", "is_buyer_maker"}].

    is_buyer_maker (semantika ako Binance): True = kupujuci bol maker, teda
    agresor bol PREDAVAJUCI; False = agresivny nakup.

    Vynimky NEODCHYTAVA - volajuci (strike_tape_poller) ich musi zapisat do
    StrikeTapePollStatus, aby sa vypadok dal ukazat na dashboarde.
    """
    resp = requests.get(f"{_BASE_URL}/v2/trades",
                        params={"symbol": symbol, "limit": min(limit, MAX_TRADES_LIMIT)},
                        timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return [
        {
            "id": int(r["id"]),
            "ts_ms": int(r["time"]),
            "price": float(r["price"]),
            "qty": float(r["qty"]),
            "quote_qty": float(r["quoteQty"]),
            "is_buyer_maker": bool(r["isBuyerMaker"]),
        }
        for r in resp.json()
    ]


def get_open_interest() -> list[dict]:
    """Open interest pre VSETKY symboly naraz (jedno volanie, namerane 31
    symbolov) - v base-asset jednotkach, nie v USD.

    Vrati [{"symbol", "open_interest", "ts_ms"}]. Symbol sa nefiltruje: aj
    tickery, ktore neobchodujeme, su zadarmo v tej istej odpovedi a mozu sa
    neskor hodit (napr. pri vybere noveho tickera)."""
    resp = requests.get(f"{_BASE_URL}/v2/openInterest", timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    out = []
    for r in resp.json():
        try:
            out.append({
                "symbol": r["symbol"],
                "open_interest": float(r["openInterest"]),
                "ts_ms": int(r["time"]),
            })
        except (KeyError, TypeError, ValueError):
            continue  # jeden pokazeny symbol nesmie zahodit ostatne
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(get_recent_trades("ADA-USD", 3), indent=2))
    print(json.dumps(get_open_interest()[:3], indent=2))
