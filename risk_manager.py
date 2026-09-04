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
# 2026-08-30 (UNITREE #140 incident) - povodnych 0.1 (10% cieloveho sl_pct)
# dovolilo Claude-om navrhnutu SL vzdialenost orezat az na absurdne tesnych
# 0.206% (UNITREE cielilo 2.0%) - TP z toho odvodeny pomerom bol 0.309%, teda
# tesnejsie nez samotny bezny bid/ask spread (~0.08% na UNITREE v tom case).
# Take tesne nohy neustale "zasahuje" bezny trhovy sum aj znama Strike burzova
# anomalia (SL/TP sa niekedy sami "expiruju" bez vyplnenia - viz
# position_monitor._check_and_reheal_bracket_legs), co spustalo stovky
# zbytocnych reheal cyklov za den. Historicka kontrola nasla rovnaky vzor (SL
# vzdialenost < 35% ciela) aj pri HYPE (4x) a ADA (1x) - nie izolovany
# UNITREE problem. Zdvihnute na 0.5 (50% ciela) - LEN rozsiruje uz existujuci
# priliz tesny SL, nikdy nezuzi sirsi, takze ziadny dopad na bezne dobre
# kalibrovane obchody.
SAFETY_FLOOR_MULTIPLE = 0.5
# 2026-09-04 (na ziadost pouzivatela) - TP je odteraz CLAUDOV (do tohto dna sa
# jeho take_profit_price ignoroval a TP sa dopocital ako SL vzdialenost x pomer
# tp_pct/sl_pct tickera - viz komentar v resolve_sl_tp_distances). Podlaha na
# pomer TP/SL: bez nej by najhorsi pripad (SL na 5x kalibracie, TP na 0.5x) dal
# pomer 0.15 - presne julovy problem, kvoli ktoremu sa TP vtedy odpojil.
MIN_REWARD_RISK_RATIO = 1.0


class RejectedTrade(Exception):
    pass


def resolve_sl_tp_distances(live_price: float, sl_price: float | None, tp_price: float | None,
                             sl_pct: float, tp_pct: float) -> tuple[float, float, float]:
    """Vrati (sl_distance, tp_distance, mechanical_tp_distance) - VZDIALENOSTI
    od live ceny, uz orezane. Zdielane medzi validate_and_size (realny obchod)
    a retrospective._hypothetical_sl_tp (co by sa bolo stalo), aby obe pocitali
    to iste.

    SL: Claudova vzdialenost, orezana do SAFETY_FLOOR_MULTIPLE..SAFETY_CAP_MULTIPLE
    nasobku kalibrovaneho sl_pct. Za 185 obchodov (audit 4.9.) sa Claude drzal
    kalibracie (median 1.03x, max 3.25x) - strop 5x ani raz nezasiahol, podlaha
    0.5x tiez nie. Sirsie SL mu pomahali (1.2-2x: +0.21 R avg, >2x: +3.4 R avg),
    preto sa strop NEZUZOVAL.

    TP (2026-09-04): Claudova vzdialenost, rovnake pasmo voci kalibrovanemu
    tp_pct. Od 24.7. do tohto dna sa Claudov TP IGNOROVAL a TP bol vzdy
    sl_distance * (tp_pct / sl_pct) - vtedy navrhoval pre NAS100 shorty SL
    316-348 bodov a TP 29-59 (pomer 0.1). Dnes je jeho pomer median 1.48,
    p10 1.12, takze dostava TP spat - s dvoma poistkami: podlaha 0.5x
    kalibracie (inak by sa zopakoval UNITREE #140 - nohy tesnejsie nez spread,
    stovky oprav denne) a MIN_REWARD_RISK_RATIO na pomer TP/SL.

    mechanical_tp_distance = stary vzorec, uklada sa k obchodu, aby sa po
    ~30 obchodoch dalo zmerat, ci Claudov TP porazil pomer tickera. Chybajuci/
    neplatny tp_price = pouzije sa mechanicky (fail-safe, nie zamietnutie)."""
    sl_distance = abs(live_price - float(sl_price))
    default_sl_distance = live_price * (sl_pct / 100)
    sl_distance = min(max(sl_distance, SAFETY_FLOOR_MULTIPLE * default_sl_distance),
                       SAFETY_CAP_MULTIPLE * default_sl_distance)

    mechanical_tp_distance = sl_distance * (tp_pct / sl_pct)

    try:
        tp_distance = abs(live_price - float(tp_price))
    except (TypeError, ValueError):
        tp_distance = None
    if not tp_distance or tp_distance <= 0:
        tp_distance = mechanical_tp_distance
    default_tp_distance = live_price * (tp_pct / 100)
    tp_distance = min(max(tp_distance, SAFETY_FLOOR_MULTIPLE * default_tp_distance),
                       SAFETY_CAP_MULTIPLE * default_tp_distance)
    if tp_distance < MIN_REWARD_RISK_RATIO * sl_distance:
        tp_distance = MIN_REWARD_RISK_RATIO * sl_distance

    return sl_distance, tp_distance, mechanical_tp_distance


def _leverage_cap_and_mmr(margin_usd: float, margin_tiers: list[dict]) -> tuple[int, float]:
    """Najvyssia paka SKUTOCNE dosiahnutelna na Strike pri danej (fixnej)
    marzi, spolu s maintenance margin rate platnou pri tejto page - viz
    _leverage_from_cushion nizsie.

    margin_tiers (zo strike_client.get_market()) su zoradene podla notional
    stropu s KLESAJUCOU max_leverage (vyssi notional = nizsia povolena paka).
    Kedze nasa marza je fixna (per-asset config), notional pri danej page je
    margin_usd * leverage - hladame NAJNIZSIU (teda prvu vyhovujucu) tier,
    ktorej vlastna max_leverage este drzi vysledny notional v jej vlastnom
    strope (inak by sa realny strop posunul na dalsiu, prisnejsiu tier). Pre
    nase typicke marze ($50-100) voci tier-1 stropom ($25-100k notional) je
    vysledok prakticky vzdy tier-1 max_leverage."""
    tiers = sorted(margin_tiers, key=lambda t: float(t["max_notional"]))
    for tier in tiers:
        max_lev = int(tier["max_leverage"])
        max_notional = float(tier["max_notional"])
        mmr = float(tier["maintenance_margin_rate"])
        if margin_usd * max_lev <= max_notional:
            return max_lev, mmr
    last = tiers[-1]
    return int(last["max_leverage"]), float(last["maintenance_margin_rate"])


def _leverage_from_cushion(sl_distance: float, live_price: float, margin_usd: float,
                            margin_tiers: list[dict], cushion_multiple: float) -> int:
    """Paka dopocitana tak, aby vzdialenost do teoretickej likvidacnej ceny
    bola PRESNE cushion_multiple-nasobkom (uz orezanej) SL vzdialenosti - napr.
    1.5 = likvidacia je o 50% dalej od vstupu nez SL. Cielom (2026-08-08,
    explicitne pouzivatelom) je MAXIMALIZOVAT expoziciu pri zachovani
    bezpecneho odstupu od likvidacie - {TICKER}_LEVERAGE uz nema na skutocny
    sizing ziaden vplyv (viz config.py), jediny strop je realny Strike-om
    povoleny max pre danu marzu (viz _leverage_cap_and_mmr), NIKDY povodna
    fixna konfigurovana hodnota.

    Vzorec (izolovana marza, bez poplatkov): teoreticka likvidacna vzdialenost
    (ako podiel z entry ceny) = 1/leverage - maintenance_margin_rate. Pri
    ciely cushion_multiple * sl_fraction = 1/leverage - mmr, teda
    leverage = 1 / (cushion_multiple * sl_fraction + mmr)."""
    max_leverage, mmr = _leverage_cap_and_mmr(margin_usd, margin_tiers)
    sl_fraction = sl_distance / live_price
    raw_leverage = 1 / (cushion_multiple * sl_fraction + mmr)
    leverage = min(math.floor(raw_leverage), max_leverage)
    return max(leverage, 1)


def validate_and_size(decision: dict, has_open_position: bool,
                       live_price: float, market_meta: dict,
                       min_confidence: int, sl_pct: float, tp_pct: float,
                       cushion_multiple: float, margin_usd: float) -> dict:
    """Vrati dict pripraveny na strike_client.open_bracket_position, alebo vyhodi RejectedTrade.

    Position sizing: `margin_usd` je per-asset config SKALOVANY confidence
    (2026-09-04: marza = margin_usd * confidence/100 - viz komentar nizsie),
    a `leverage` sa od 2026-08-08 DOPOCITAVA z (uz orezanej) SL vzdialenosti a
    `cushion_multiple` - viz _leverage_from_cushion. Notional = margin_usd *
    (takto dopocitana) leverage, teda uz NIE JE fixny naprieč obchodmi ako
    predtym - siri SL prirodzene znamena nizsiu paku (a teda mensi notional
    pri rovnakej marzi), tesnejsi SL vyssiu paku, vzdy orezane na skutocny
    burzou povoleny strop.

    SL aj TP: Claude navrhuje absolutne ceny, z ktorych pouzijeme len VZDIALENOSTI
    od live ceny, orezane do SAFETY_FLOOR_MULTIPLE..SAFETY_CAP_MULTIPLE nasobku
    kalibrovaneho sl_pct/tp_pct + podlaha MIN_REWARD_RISK_RATIO na pomer TP/SL
    (nikdy zamietnutie, len orezanie) - viz resolve_sl_tp_distances. Do
    2026-09-04 sa Claudov TP ignoroval (historia v jej docstringu).

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

    sl_distance, tp_distance, mechanical_tp_distance = resolve_sl_tp_distances(
        live_price, decision["stop_loss_price"], decision.get("take_profit_price"), sl_pct, tp_pct,
    )

    if decision["direction"] == "long":
        sl = live_price - sl_distance
        tp = live_price + tp_distance
        tp_mech = live_price + mechanical_tp_distance
    else:  # short
        sl = live_price + sl_distance
        tp = live_price - tp_distance
        tp_mech = live_price - mechanical_tp_distance

    tick = float(market_meta["order_tick_price"])
    sl = _round_to_tick(sl, tick)
    tp = _round_to_tick(tp, tick)
    tp_mech = _round_to_tick(tp_mech, tick)

    # Ak by zaokruhlenie na tick_size (napr. pri velmi malej, floor-om vynutenej
    # vzdialenosti) skolabovalo SL/TP naspat presne na live_price, posunieme o
    # jeden tick spravnym smerom - inak by pozicia mala nulovu ochranu.
    if sl == live_price:
        sl = sl - tick if decision["direction"] == "long" else sl + tick
    if tp == live_price:
        tp = tp + tick if decision["direction"] == "long" else tp - tick

    risk_reward = tp_distance / sl_distance if sl_distance else 0

    # 2026-09-04 (navrh pouzivatela) - MARZA SA SKALUJE CONFIDENCE.
    #
    # Doteraz bola marza fixna a confidence fungovala ako binarny prah: 64 =
    # nic, 66 = plna pozicia. Namerane na 859 cykloch: 82 % rozhodnuti nad
    # prahom lezalo do 2 bodov nad nim, cize cislo nenieslo ziadnu rozlisovaciu
    # informaciu - Claude sa rozhodol obchodovat a napisal cislo, ktore to
    # umoznilo. Meranie s ukrytym prahom (30 cyklov) dalo rozpatie 18 bodov
    # namiesto 3, s medianom 52 - a realizovany win rate 42 % na 178 obchodoch
    # sedi na 52 podstatne lepsie nez na vtedajsich 66.
    #
    # Teraz sa cely rozsah pouzije: marza = margin_usd * confidence/100.
    # Ziadny utes, ku ktoremu by sa dalo ukotvit - kazdy bod confidence sa
    # premietne do penazi.
    #
    # POZOR: Claude o tomto vzorci NEVIE a vediet nema (viz claude_analyst) -
    # inak by sa cislo znova stalo pakou na velkost pozicie namiesto odhadu
    # pravdepodobnosti a stratili by sme moznost overit jeho kalibraciu.
    #
    # Skaluje sa PRED _leverage_from_cushion, lebo paka zavisi od marze cez
    # margin_tiers - inak by sa notional pocital z inej marze, nez sa realne
    # posle na burzu.
    confidence_scale = max(0.0, min(1.0, float(decision["confidence"]) / 100.0))
    margin_usd = margin_usd * confidence_scale

    # Paka az TERAZ, z uz finalnej (po tick-zaokruhleni) SL vzdialenosti -
    # viz _leverage_from_cushion.
    final_sl_distance = abs(live_price - sl)
    leverage = _leverage_from_cushion(
        final_sl_distance, live_price, margin_usd, market_meta["margin_tiers"], cushion_multiple,
    )

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
        # Stary vzorec (SL x pomer tickera) - len na porovnanie, viz Trade.take_profit_price_mechanical.
        "take_profit_price_mechanical": tp_mech,
        "entry_price": live_price,
        "confidence": decision["confidence"],
        "reasoning": decision["reasoning"],
        "risk_reward": round(risk_reward, 2),
    }


def _round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 8)
