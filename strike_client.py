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


def _request(method: str, path: str, body: dict | None = None) -> dict:
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    headers = _sign(method, path, body_str)
    if body is not None:
        headers["Content-Type"] = "application/json"

    url = f"{config.STRIKE_BASE_URL}{path}"
    resp = requests.request(method.upper(), url, headers=headers,
                             data=body_str if body is not None else None, timeout=20)
    if resp.status_code >= 300:
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


def open_bracket_position(direction: str, size: float, leverage: int,
                           stop_loss_price: float, take_profit_price: float,
                           symbol: str = None) -> dict:
    """
    direction: 'Long' alebo 'Short'. size: pozicna velkost v base-asset jednotkach.
    Otvori market poziciu + zaroven pripravi TP/SL ako bracket ("strategy") objednavku:
    ak jedna strana (TP/SL) trigerne, druha sa automaticky zrusi.
    """
    symbol = symbol or config.STRIKE_NAS100_SYMBOL
    side = "buy" if direction == "Long" else "sell"
    size_str = str(size)

    set_leverage(symbol, leverage)

    body = {
        "strategy_id": str(uuid.uuid4()),
        "symbol": symbol,
        "side": side,
        "type": "market",
        "size": size_str,
        "tp_order": {"type": "take_profit", "size": size_str, "stop_price": str(take_profit_price)},
        "sl_order": {"type": "stop", "size": size_str, "stop_price": str(stop_loss_price)},
    }
    return _request("POST", "/v2/order/strategy", body)


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
