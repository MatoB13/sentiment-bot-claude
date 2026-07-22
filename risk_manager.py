"""Kontroly rozhodnutia od Claude pred realnou exekuciou obchodu."""
import math

import config

# Claude navrhuje konkretnu SL/TP cenu, ale musi zostat v tejto tolerancii
# okolo config.DEFAULT_SL_PCT/DEFAULT_TP_PCT (v nasobkoch default vzdialenosti).
SL_TOLERANCE = (0.5, 2.0)
TP_TOLERANCE = (0.5, 2.0)


class RejectedTrade(Exception):
    pass


def validate_and_size(decision: dict, ta: dict, has_open_position: bool,
                       live_price: float, market_meta: dict) -> dict:
    """Vrati dict pripraveny na strike_client.open_bracket_position, alebo vyhodi RejectedTrade.

    Position sizing je fixny: kazdy obchod pouzije config.MARGIN_USD marzy a
    config.LEVERAGE paku (teda vzdy rovnaky notional = MARGIN_USD * LEVERAGE).
    SL/TP navrhuje Claude ako absolutnu cenu, ale musi zostat v tolerancii okolo
    config.DEFAULT_SL_PCT/DEFAULT_TP_PCT (% od live ceny) - viz SL_TOLERANCE/TP_TOLERANCE.

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

    sl = decision["stop_loss_price"]
    tp = decision["take_profit_price"]

    if not live_price:
        raise RejectedTrade("Chybajuca live cena - nemozem overit SL/TP.")

    tick = float(market_meta["order_tick_price"])
    sl = _round_to_tick(sl, tick)
    tp = _round_to_tick(tp, tick)

    sl_distance = abs(live_price - sl)
    tp_distance = abs(tp - live_price)

    default_sl_distance = live_price * (config.DEFAULT_SL_PCT / 100)
    default_tp_distance = live_price * (config.DEFAULT_TP_PCT / 100)

    sl_lo, sl_hi = SL_TOLERANCE[0] * default_sl_distance, SL_TOLERANCE[1] * default_sl_distance
    if not (sl_lo <= sl_distance <= sl_hi):
        raise RejectedTrade(
            f"Stop-loss vzdialenost {sl_distance:.1f} mimo tolerancie okolo defaultu "
            f"{config.DEFAULT_SL_PCT}% ({sl_lo:.1f}-{sl_hi:.1f})."
        )

    tp_lo, tp_hi = TP_TOLERANCE[0] * default_tp_distance, TP_TOLERANCE[1] * default_tp_distance
    if not (tp_lo <= tp_distance <= tp_hi):
        raise RejectedTrade(
            f"Take-profit vzdialenost {tp_distance:.1f} mimo tolerancie okolo defaultu "
            f"{config.DEFAULT_TP_PCT}% ({tp_lo:.1f}-{tp_hi:.1f})."
        )

    # smer SL/TP musi davat zmysel voci direction
    if decision["direction"] == "long" and not (sl < live_price < tp):
        raise RejectedTrade("Pre LONG musi platit stop_loss < live_price < take_profit.")
    if decision["direction"] == "short" and not (tp < live_price < sl):
        raise RejectedTrade("Pre SHORT musi platit take_profit < live_price < stop_loss.")

    risk_reward = tp_distance / sl_distance if sl_distance else 0
    if risk_reward < 1.0:
        raise RejectedTrade(f"Risk:reward {risk_reward:.2f} je horsi ako 1:1 - neobchodujem.")

    leverage = config.LEVERAGE
    notional_usd = config.MARGIN_USD * leverage
    size = notional_usd / live_price

    step = float(market_meta["order_market_step_size"])
    min_size = float(market_meta["order_market_min_size"])
    max_size = float(market_meta["order_market_max_size"])
    min_notional = float(market_meta["order_min_notional"])

    size = math.floor(size / step) * step
    size = min(size, max_size)
    notional_usd = size * live_price
    margin_usd = notional_usd / leverage

    if size < min_size or notional_usd < min_notional:
        raise RejectedTrade(
            f"Vypocitana velkost pozicie {size} ({notional_usd:.2f} USD) je pod minimom "
            f"burzy (min_size={min_size}, min_notional={min_notional})."
        )

    return {
        "direction": "Long" if decision["direction"] == "long" else "Short",
        "leverage": leverage,
        "size": round(size, 8),
        "notional_usd": round(notional_usd, 2),
        "margin_usd": round(margin_usd, 2),
        "stop_loss_price": sl,
        "take_profit_price": tp,
        "entry_price": live_price,
        "confidence": decision["confidence"],
        "reasoning": decision["reasoning"],
        "risk_reward": round(risk_reward, 2),
    }


def _round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 8)
