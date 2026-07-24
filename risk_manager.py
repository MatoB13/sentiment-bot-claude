"""Kontroly rozhodnutia od Claude pred realnou exekuciou obchodu.

Jediny GATE na otvorenie obchodu je confidence (min_confidence) - okrem toho uz
len veci mimo nasej kontroly: uz otvorena pozicia, direction="none", alebo
skutocne limity burzy (min. velkost/notional objednavky - to Strike API proste
neprijme, nie je to nas risk-preference). SL/TP navrhnute Claudom sa POUZIJE
(prip. upravi), ale nikdy nezablokuje otvorenie pozicie.
"""
import math

# Bezpecnostny strop na SL/TP vzdialenost (nasobok cieloveho sl_pct/tp_pct) -
# NIKDY nezablokuje obchod, len oreze extremnu (typicky chybnu) hodnotu na
# rozumnu hranicu namiesto doslovneho pouzitia. Dolny strop (SAFETY_FLOOR_MULTIPLE)
# chrani pred degenerovanou (napr. nulovou) vzdialenostou.
SAFETY_CAP_MULTIPLE = 5.0
SAFETY_FLOOR_MULTIPLE = 0.1


class RejectedTrade(Exception):
    pass


def validate_and_size(decision: dict, has_open_position: bool,
                       live_price: float, market_meta: dict,
                       min_confidence: int, sl_pct: float, tp_pct: float,
                       leverage: int, margin_usd: float) -> dict:
    """Vrati dict pripraveny na strike_client.open_bracket_position, alebo vyhodi RejectedTrade.

    Position sizing je fixny: kazdy obchod pouzije `margin_usd` marzy a `leverage`
    paku (teda vzdy rovnaky notional = margin_usd * leverage). Vsetky risk
    parametre su per-asset (viz assets.py).

    SL: Claude navrhuje absolutnu cenu, z ktorej pouzijeme len VZDIALENOST
    (abs(live_price - stop_loss_price)), orezanu do SAFETY_FLOOR_MULTIPLE..
    SAFETY_CAP_MULTIPLE nasobku cieloveho sl_pct (nikdy zamietnutie, len orezanie).

    TP: NEPOUZIVA Claude-ov navrhnuty take_profit_price priamo - namiesto toho sa
    DOPOCITA z (uz orezanej) SL vzdialenosti a cieloveho pomeru tp_pct/sl_pct, takze
    risk:reward pomer je VZDY presne zachovany. Dovod (overene backtestom
    2026-07-24 na historickych cycle_logs): Claude systematicky navrhoval SL
    vzdialenost oveľa sirsiu nez TP (napr. SL 316-348 bodov vs TP len 29-59 bodov
    pre NAS100 shorty - risk:reward 0.09-0.17 namiesto cieloveho 1.5), co pri
    realnom cenovom vyvoji viedlo k systematickym stratam aj pri dobrom win-rate
    (male vyhry, obrovske prehry). SL od Claude sa NEIGNORUJE (jeho odhad kam
    siaha invalidacia setupu je uzitocny), len sa TP prestane spoliehat na
    Claude-ovo (nespolahlive skalibrovane) absolutne cislo.

    Smerova konzistencia: ak by SL/TP vyszli oproti smeru obratene (napr. Claude
    dal stop_loss_price nad live_price pri LONG), prepocet z ORIENTOVANEJ
    vzdialenosti + znovu-umiestnenie na spravnu stranu podla smeru to automaticky
    opravi bez zamietnutia obchodu.

    live_price: aktualna mark/last cena z strike_client.get_market() (referencna cena burzy,
    presnejsia ako yfinance proxy v `ta`). market_meta: dict z strike_client.get_market()
    s tick/step/min-notional limitmi daneho trhu.
    """

    if has_open_position:
        raise RejectedTrade("Uz existuje otvorena pozicia pre tento asset - preskakujem cyklus.")

    if decision["direction"] == "none":
        raise RejectedTrade(f"Model odporucil 'none' (confidence={decision['confidence']}).")

    if decision["confidence"] < min_confidence:
        raise RejectedTrade(
            f"Confidence {decision['confidence']} < MIN_CONFIDENCE {min_confidence}."
        )

    if not live_price:
        raise RejectedTrade("Chybajuca live cena - nemozem vypocitat SL/TP.")

    sl_distance = abs(live_price - decision["stop_loss_price"])
    default_sl_distance = live_price * (sl_pct / 100)
    sl_distance = min(max(sl_distance, SAFETY_FLOOR_MULTIPLE * default_sl_distance),
                       SAFETY_CAP_MULTIPLE * default_sl_distance)

    # TP dopocitane z (orezanej) SL vzdialenosti a cieloveho pomeru - nie z
    # Claude-ovho navrhnuteho take_profit_price (viz vysvetlenie vyssie).
    tp_distance = sl_distance * (tp_pct / sl_pct)

    if decision["direction"] == "long":
        sl = live_price - sl_distance
        tp = live_price + tp_distance
    else:  # short
        sl = live_price + sl_distance
        tp = live_price - tp_distance

    tick = float(market_meta["order_tick_price"])
    sl = _round_to_tick(sl, tick)
    tp = _round_to_tick(tp, tick)

    # Ak by zaokruhlenie na tick_size (napr. pri velmi malej, floor-om vynutenej
    # vzdialenosti) skolabovalo SL/TP naspat presne na live_price, posunieme o
    # jeden tick spravnym smerom - inak by pozicia mala nulovu ochranu.
    if sl == live_price:
        sl = sl - tick if decision["direction"] == "long" else sl + tick
    if tp == live_price:
        tp = tp + tick if decision["direction"] == "long" else tp - tick

    risk_reward = tp_distance / sl_distance if sl_distance else 0

    notional_usd = margin_usd * leverage
    size = notional_usd / live_price

    step = float(market_meta["order_market_step_size"])
    min_size = float(market_meta["order_market_min_size"])
    max_size = float(market_meta["order_market_max_size"])
    min_notional = float(market_meta["order_min_notional"])

    size = math.floor(size / step) * step
    size = min(size, max_size)
    notional_usd = size * live_price
    margin_usd = notional_usd / leverage

    # Toto NIE JE nas risk-preference - je to skutocny limit burzy (Strike API
    # by objednavku pod touto velkostou proste odmietol), preto tu ostava jedine
    # tvrde zamietnutie okrem confidence/otvorenej pozicie/direction="none".
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
