"""Kontroly rozhodnutia od Claude pred realnou exekuciou obchodu."""
import math

import config


class RejectedTrade(Exception):
    pass


def validate_and_size(decision: dict, ta: dict, has_open_position: bool,
                       live_price: float, market_meta: dict) -> dict:
    """Vrati dict pripraveny na strike_client.open_bracket_position, alebo vyhodi RejectedTrade.

    live_price: aktualna mark/last cena z strike_client.get_market() (referencna cena burzy,
    presnejsia ako yfinance proxy v `ta`). market_meta: dict z strike_client.get_market()
    s tick/step/min-notional limitmi daneho trhu.
    """

    if has_open_position:
        raise RejectedTrade("Uz existuje otvorena NAS100 pozicia - preskakujem cyklus.")

    if decision["direction"] == "none":
        raise RejectedTrade(f"Model odporucil 'none' (confidence={decision['confidence']}).")

    if decision["confidence"] < config.MIN_CONFIDENCE:
        raise RejectedTrade(
            f"Confidence {decision['confidence']} < MIN_CONFIDENCE {config.MIN_CONFIDENCE}."
        )

    atr = ta.get("atr14")
    sl = decision["stop_loss_price"]
    tp = decision["take_profit_price"]

    if not live_price or not atr:
        raise RejectedTrade("Chybajuca live cena alebo ATR - nemozem overit SL/TP.")

    tick = float(market_meta["order_tick_price"])
    sl = _round_to_tick(sl, tick)
    tp = _round_to_tick(tp, tick)

    sl_distance = abs(live_price - sl)
    tp_distance = abs(tp - live_price)

    # sanity: SL nesmie byt sialene tesny (moznost hned vyhodit poziciu sumom)
    # ani sialene siroky (nezmyselne riziko)
    if sl_distance < 0.4 * atr:
        raise RejectedTrade(f"Stop-loss prilis tesny ({sl_distance:.1f} < 0.4*ATR={0.4*atr:.1f}).")
    if sl_distance > 4 * atr:
        raise RejectedTrade(f"Stop-loss prilis siroky ({sl_distance:.1f} > 4*ATR={4*atr:.1f}).")

    # smer SL/TP musi davat zmysel voci direction
    if decision["direction"] == "long" and not (sl < live_price < tp):
        raise RejectedTrade("Pre LONG musi platit stop_loss < live_price < take_profit.")
    if decision["direction"] == "short" and not (tp < live_price < sl):
        raise RejectedTrade("Pre SHORT musi platit take_profit < live_price < stop_loss.")

    risk_reward = tp_distance / sl_distance if sl_distance else 0
    if risk_reward < 1.0:
        raise RejectedTrade(f"Risk:reward {risk_reward:.2f} je horsi ako 1:1 - neobchodujem.")

    leverage = min(config.MAX_LEVERAGE, _confidence_to_leverage(decision["confidence"]))
    risk_amount_usd = config.ACCOUNT_BALANCE_USD * (config.RISK_PCT / 100)

    # velkost pozicie (v base-asset jednotkach NAS100) tak, aby zasah SL stratil ~risk_amount_usd
    size = risk_amount_usd / sl_distance

    step = float(market_meta["order_market_step_size"])
    min_size = float(market_meta["order_market_min_size"])
    max_size = float(market_meta["order_market_max_size"])
    min_notional = float(market_meta["order_min_notional"])

    size = math.floor(size / step) * step
    size = min(size, max_size)

    notional_usd = size * live_price
    required_margin_usd = notional_usd / leverage

    if size < min_size or notional_usd < min_notional:
        raise RejectedTrade(
            f"Vypocitana velkost pozicie {size} ({notional_usd:.2f} USD) je pod minimom "
            f"burzy (min_size={min_size}, min_notional={min_notional})."
        )
    if required_margin_usd > config.ACCOUNT_BALANCE_USD:
        raise RejectedTrade(
            f"Potrebna marza ${required_margin_usd:.2f} presahuje ACCOUNT_BALANCE_USD "
            f"${config.ACCOUNT_BALANCE_USD}."
        )

    return {
        "direction": "Long" if decision["direction"] == "long" else "Short",
        "leverage": leverage,
        "size": round(size, 8),
        "notional_usd": round(notional_usd, 2),
        "margin_usd": round(required_margin_usd, 2),
        "stop_loss_price": sl,
        "take_profit_price": tp,
        "entry_price": live_price,
        "confidence": decision["confidence"],
        "reasoning": decision["reasoning"],
        "risk_reward": round(risk_reward, 2),
    }


def _round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 8)


def _confidence_to_leverage(confidence: int) -> int:
    """Vyssia istota = trosku vyssia paka, ale vzdy v ramci MAX_LEVERAGE."""
    if confidence >= 85:
        return config.MAX_LEVERAGE
    if confidence >= 75:
        return max(1, config.MAX_LEVERAGE - 1)
    return max(1, config.MAX_LEVERAGE // 2)
