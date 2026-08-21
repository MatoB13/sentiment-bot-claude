"""
Strike Finance API klient (v2, Ed25519 API wallet auth).

Schema podpisu podla https://docs.strikefinance.org/api/getting-started :
message = f"{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{BODY_HASH}"
podpisany Ed25519 privatnym klucom API wallet-u. (Overene voci realnemu API.)

SL/TP sa nastavuju cez bracket "strategy" objednavku (POST /v2/order/strategy),
nie top-level poliami na /v2/order - viz https://docs.strikefinance.org/api/trade/orders.
Leverage sa nastavuje samostatne pred otvorenim pozicie (POST /v2/leverage) -
viz https://docs.strikefinance.org/api/trade/trading. `size` je v base-asset
jednotkach (napr. kolko NAS100 kontraktov), nie notional USD hodnota.
"""
import hashlib
import json
import time
import uuid

import requests

import config


def _sign(method: str, path: str, body_str: str = "") -> dict:
    private_key_bytes = bytes.fromhex(config.STRIKE_API_PRIVATE_KEY)
    if len(private_key_bytes) == 64:
        private_key_bytes = private_key_bytes[:32]

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)

    timestamp = int(time.time())
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body_str.encode()).hexdigest()

    message = f"{method.upper()}:{path}:{timestamp}:{nonce}:{body_hash}"
    signature = private_key.sign(message.encode())

    return {
        "X-API-Wallet-Public-Key": config.STRIKE_API_PUBLIC_KEY,
        "X-API-Wallet-Signature": signature.hex(),
        "X-API-Wallet-Timestamp": str(timestamp),
        "X-API-Wallet-Nonce": nonce,
    }


# Prechodne infra chyby (burza/proxy docasne nedostupna) - bezpecne opakovat
# LEN pre GET (citanie, ziadny vedlajsi efekt). POST/DELETE (otvorenie/zatvorenie
# pozicie a pod.) sa NIKDY neopakuju automaticky - ak by sa odpoved stratila po
# tom, co sa objednavka na burze uz realne vykonala, retry by mohol omylom
# spustit rovnaku akciu druhykrat (napr. otvorit poziciu 2x).
# 429 (rate limit) pridane 2026-08-19 (crash-scenario audit) - pri hromadnom
# zatvoreni viacerych pozicii naraz robi position_monitor viacero GET volani
# (order/fill history) za sebou; predtym by 429 rovno zhodil ten lookup bez
# pokusu o retry (self-healing _backfill_missing_exact_data by to skusila
# znova az pri buducom tiku).
_RETRYABLE_STATUS = {429, 502, 503, 504}
_MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 2


def _request(method: str, path: str, body: dict | None = None) -> dict:
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    url = f"{config.STRIKE_BASE_URL}{path}"
    attempts = _MAX_RETRIES + 1 if method.upper() == "GET" else 1

    for attempt in range(attempts):
        # Kazdy pokus potrebuje CERSTVY podpis (timestamp/nonce) - opatovne
        # pouzitie povodnych headers pri oneskorenom retry by Strike API mohlo
        # odmietnut ako expirovany/replay podpis.
        headers = _sign(method, path, body_str)
        if body is not None:
            headers["Content-Type"] = "application/json"

        resp = requests.request(method.upper(), url, headers=headers,
                                 data=body_str if body is not None else None, timeout=20)
        if resp.status_code >= 300:
            if resp.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                print(f"[strike_client] {method} {path} -> {resp.status_code} "
                      f"(prechodna chyba), skusam znova o {_RETRY_DELAY_SECONDS}s "
                      f"({attempt + 1}/{attempts - 1})...")
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            raise RuntimeError(f"Strike API {method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json()


def get_account() -> dict:
    return _request("GET", "/v2/account")


def get_positions(symbol: str | None = None) -> list[dict]:
    path = "/v2/positions"
    if symbol:
        path += f"?symbol={symbol}"
    result = _request("GET", path)
    return result if isinstance(result, list) else result.get("positions", [])


def get_closed_positions(symbol: str | None = None, limit: int = 20) -> list[dict]:
    params = [f"limit={limit}"]
    if symbol:
        params.append(f"symbol={symbol}")
    result = _request("GET", f"/v2/closedPositions?{'&'.join(params)}")
    return result if isinstance(result, list) else result.get("positions", [])


def get_order_history(symbol: str, start_ms: int | None = None, end_ms: int | None = None,
                       limit: int = 100) -> list[dict]:
    """/v2/history/order - VSETKY objednavky (aj expirovane/zrusene) za obdobie,
    vratane strategy_id linku spat na nasu Trade.strategy_id. Na rozdiel od
    /v2/closedPositions ma toto skutocne pouzitelne polia (Status, Type,
    CloseReason, AutoCloseType) - viz docs.strikefinance.org/api/trade/history."""
    params = [f"symbol={symbol}", f"limit={limit}"]
    if start_ms is not None:
        params.append(f"startTime={start_ms}")
    if end_ms is not None:
        params.append(f"endTime={end_ms}")
    result = _request("GET", f"/v2/history/order?{'&'.join(params)}")
    return result if isinstance(result, list) else result.get("orders", [])


def get_fill_history(symbol: str, start_ms: int | None = None, end_ms: int | None = None,
                      limit: int = 500) -> list[dict]:
    """/v2/history/fill - skutocne jednotlive fills (moze byt viac na jednu
    objednavku, ak sa vykonala postupne) so skutocnou cenou, poplatkom a
    realized_pnl priamo z burzy - jediny zdroj presneho (nie odhadovaneho) PnL."""
    params = [f"symbol={symbol}", f"limit={limit}"]
    if start_ms is not None:
        params.append(f"startTime={start_ms}")
    if end_ms is not None:
        params.append(f"endTime={end_ms}")
    result = _request("GET", f"/v2/history/fill?{'&'.join(params)}")
    return result if isinstance(result, list) else result.get("fills", [])


def get_funding_history(symbol: str, start_ms: int | None = None, end_ms: int | None = None,
                         limit: int = 1000) -> list[dict]:
    """/v2/history/funding - periodicke funding platby za drzanie perpetual
    pozicie (kladne amount = prijate, zaporne = zaplatene), NEZAVISLE od
    /v2/history/fill (fills obsahuju len obchodne PnL/poplatky, ziadny funding -
    overene naprieč zaznamami 2026-08-15). Zdokumentovane pod api/user/rest-api/
    history (nie api/trade/history, kde su len order/fill) - preto lahko
    prehliadnutelne."""
    params = [f"symbol={symbol}", f"limit={limit}"]
    if start_ms is not None:
        params.append(f"startTime={start_ms}")
    if end_ms is not None:
        params.append(f"endTime={end_ms}")
    result = _request("GET", f"/v2/history/funding?{'&'.join(params)}")
    if isinstance(result, list):
        return result
    return (result.get("funding") if isinstance(result, dict) else None) or []


def get_markets() -> list[dict]:
    """Vrati zoznam vsetkych marketov (obsahuje presny symbol, tick/step size, mark_price...)."""
    result = _request("GET", "/v2/markets")
    markets = result if isinstance(result, list) else result.get("markets", {})
    if isinstance(markets, dict):
        return list(markets.values())
    return markets


def get_market(symbol: str) -> dict:
    for m in get_markets():
        if m.get("symbol") == symbol:
            return m
    raise RuntimeError(f"Market {symbol} sa nenasiel v /v2/markets.")


def set_leverage(symbol: str, leverage: int) -> dict:
    return _request("POST", "/v2/leverage", {"symbol": symbol, "leverage": leverage})


def set_margin_mode(symbol: str, mode: str) -> dict:
    """POST /v2/marginMode - "cross" alebo "isolated". Burza to odmietne (400),
    ak je pre dany symbol prave otvorena pozicia - preto sa vola LEN tesne pred
    otvorenim novej pozicie (rovnako ako set_leverage), nikdy inokedy."""
    return _request("POST", "/v2/marginMode", {"symbol": symbol, "marginMode": mode})


def open_bracket_position(direction: str, size: float, leverage: int,
                           stop_loss_price: float, take_profit_price: float,
                           symbol: str = None) -> dict:
    """
    direction: 'Long' alebo 'Short'. size: pozicna velkost v base-asset jednotkach.
    Otvori market poziciu + zaroven pripravi TP/SL ako bracket ("strategy") objednavku:
    ak jedna strana (TP/SL) trigerne, druha sa automaticky zrusi.

    TP noha je "take_profit_limit" (nie "take_profit" market-style) - price aj
    stop_price su rovnake ako povodna TP cena. Ked sa hladina dosiahne postupne
    (bezny pripad pri viachodinovom swingu), objednavka si po spusteni len lahne
    do knihy ako pasivna a neskor sa vykona ako MAKER (Strike ma na maker fee
    rebate, nie poplatok). Ak by cena cez uroven preskocila naraz (gap/nahly
    naraz likvidity), vykona sa okamzite ako taker - rovnako ako doteraz, ziadne
    zhorsenie. Zamerne BEZ post_only: to by pri gap-scenari cely TP prikaz
    odmietlo (pozicia by ostala docasne bez TP nohy, chranena len SL-kom).
    SL zostava market-style ("stop") - potrebuje garantovane vykonanie.

    Poziciu otvarame v ISOLATED margin mode (nie cross) - kedze bot moze mat
    sucasne otvorene pozicie na viacerych assetoch (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX), pri
    cross marginy by extremny pohyb (napr. sklz cez SL pri gape) na jednom
    assete cerpal zo ZDIELANEJ marze a mohol tak zvysit riziko likvidacie aj na
    ostatnych, inak nesuvisiacich, otvorenych poziciach. Isolated obmedzi
    najhorsi pripad kazdej pozicie len na jej vlastnu alokovanu marzu, bez
    zmeny bezneho sizingu (ten je aj tak fixny cez margin_usd). Nastavenie
    margin mode NIKDY neblokuje otvorenie pozicie (viz set_margin_mode) - ak
    zlyha, poziciu otvorime v akomkolvek mode je prave aktivny (status quo).
    """
    symbol = symbol or config.STRIKE_NAS100_SYMBOL
    side = "buy" if direction == "Long" else "sell"
    size_str = str(size)

    try:
        set_margin_mode(symbol, "isolated")
    except Exception as e:
        print(f"[strike_client] Nepodarilo sa nastavit isolated margin pre {symbol} "
              f"(pokracujem v aktualnom mode): {e}")

    set_leverage(symbol, leverage)

    body = {
        "strategy_id": str(uuid.uuid4()),
        "symbol": symbol,
        "side": side,
        "type": "market",
        "size": size_str,
        "tp_order": {
            "type": "take_profit_limit", "size": size_str,
            "stop_price": str(take_profit_price), "price": str(take_profit_price),
        },
        "sl_order": {"type": "stop", "size": size_str, "stop_price": str(stop_loss_price)},
    }
    return _request("POST", "/v2/order/strategy", body)


def get_open_orders(symbol: str | None = None) -> list[dict]:
    """/v2/openOrders - PRAVE TERAZ zive visiace objednavky (na rozdiel od
    get_order_history, ktore vracia HISTORICKY log vratane uz expirovanych/
    zrusenych zaznamov - tu je dolezite vediet presne, co je REALNE na burzi
    tento okamih). Pouziva position_monitor._check_and_reheal_bracket_legs
    (2026-08-21, po ADA incidente - SL noha bracket objednavky sa sama
    "expirovala" na burzi, close_reason "order_strategy_secondary_oco", BEZ
    vyplnenia a bez akehokolvek zasahu z nasej strany, pozicia ostala
    docasne nechranena)."""
    path = f"/v2/openOrders?symbol={symbol}" if symbol else "/v2/openOrders"
    result = _request("GET", path)
    return result if isinstance(result, list) else result.get("orders", [])


def place_stop_order(symbol: str, side: str, size: float, stop_price: float) -> dict:
    """Samostatna reduce-only SL (stop) objednavka - DOPLNENIE chybajucej SL
    nohy na uz existujucej pozicii (viz _check_and_reheal_bracket_legs), nie
    otvorenie novej pozicie (tam open_bracket_position, oba nohy naraz cez
    /v2/order/strategy). Rovnaky tvar poli ako vnoreny sl_order vyssie."""
    body = {
        "symbol": symbol, "side": side, "type": "stop",
        "size": str(size), "stop_price": str(stop_price),
        "reduce_only": True,
    }
    return _request("POST", "/v2/order", body)


def place_take_profit_order(symbol: str, side: str, size: float, price: float) -> dict:
    """Samostatna reduce-only TP (take_profit_limit) objednavka - DOPLNENIE
    chybajucej TP nohy, analogicke place_stop_order vyssie."""
    body = {
        "symbol": symbol, "side": side, "type": "take_profit_limit",
        "size": str(size), "stop_price": str(price), "price": str(price),
        "reduce_only": True,
    }
    return _request("POST", "/v2/order", body)


def cancel_all_orders(symbol: str = None) -> dict:
    symbol = symbol or config.STRIKE_NAS100_SYMBOL
    return _request("DELETE", "/v2/order/cancel-all", {"symbol": symbol})


def close_position_market(direction: str, size: float, symbol: str = None) -> dict:
    """Force-close: market objednavka na opacnu stranu, reduce_only + close_position."""
    symbol = symbol or config.STRIKE_NAS100_SYMBOL
    close_side = "sell" if direction == "Long" else "buy"
    body = {
        "symbol": symbol,
        "side": close_side,
        "type": "market",
        "size": str(size),
        "reduce_only": True,
        "close_position": True,
    }
    return _request("POST", "/v2/order", body)


if __name__ == "__main__":
    print(json.dumps(get_market(config.STRIKE_NAS100_SYMBOL), indent=2))
