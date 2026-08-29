"""Analyticky/obchodny cyklus pre vsetky aktivne assety (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX).

Jeden scheduler tick (viz main.py) = jeden vstup do run_all_cycles(): zdielany
makro fetch (cross-market/session, pripadne BTC proxy) sa spravi PRESNE RAZ a
potom sa pouzije pre kazdy aktivny asset z assets.py nezavisle - kazdy ma
vlastnu poziciu, vlastny risk (SL/TP%, leverage, margin, min_confidence) a
vlastne Claude rozhodnutie. Zlyhanie jedneho assetu nesmie zhodit ostatne."""
import math
import threading
from datetime import datetime, timedelta, timezone

import assets
import claude_analyst
import coinmarketcal_client
import config
import discord_client
import eia_client
import fred_client
import market_data
import marketaux_client
import retrospective
import risk_manager
import risk_overrides
import social_sentiment
import strike_client
from db import (CycleLog, DailyRetrospective, FlaggedMacroEvent, PriceBar,
                 RollingRetrospective, Trade, get_session)


# Tolerancia na scheduler jitter/spracovanie predchadzajucich assetov v tom
# istom cykle - bez nej by drobne oneskorenie (o par sekund) niekedy tesne
# netrafilo pozadovany interval a preskocilo by sa o cely dalsi tick navyse.
# POZOR: "last_log.created_at" sa zapise AZ PO dokonceni Claude odpovede (vratane
# web_search kol/pause_turn pokracovani), takze sa moze bezne posunut o 3-5 minut
# oproti nominalnemu casu ticku - 0.05h (3 min) na to nestacilo a sposobovalo to
# obcasne zbytocne preskocenie ticku tesne pod hranicou (viz produkcny incident
# 2026-07-27: NAS100 preskocilo cyklus po 56min41s, kedze pozadovanych 57min
# (1h - 3min tolerancia) tesne nedosiahlo).
_TIME_GATE_TOLERANCE_HOURS = 0.1


def _required_interval_hours(asset: dict, now: datetime) -> float:
    """Kolko hodin ma uplynut od posledneho cyklu TOHTO assetu, nez je zase 'na
    rade'. KAZDY asset (2026-07-31 zjednotene) ma vsetky tri prahy definovane
    v assets.py (trade/off_hours/weekend interval hours) - pre 24/7 krypto
    (ADA/NIGHT) su defaultne vsetky tri rovnake (ziadne skutocne "off hours"
    preň neexistuju), ale su nezavisle nastavitelne rovnako ako pre ostatne.
    Pre akcie/futures (NAS100/NVDA/GOLD/WTI) mimo trading hours a cez vikend
    podkladovy trh realne stoji alebo je velmi ticho (NVDA sa cez vikend
    vobec neobchoduje), takze hodinova analyza tych istych zastaralych dat je
    zbytocny naklad.

    trading_hours_start_utc/end_utc (2026-08-07) su teraz PER-ASSET (viz
    assets.py) namiesto priamo config.TRADING_HOURS_*, kedze SKHYNIX
    (Korea Exchange) ma skutocnu seansu v uplne inych UTC hodinach nez
    zdielany NYSE default vsetkych ostatnych - pouzitie zdielanej hodnoty by
    preň off_hours/trade_hours logiku obratilo naopak."""
    if now.weekday() >= 5:  # sobota=5, nedela=6
        return asset["weekend_interval_hours"]
    start = asset["trading_hours_start_utc"]
    end = asset["trading_hours_end_utc"]
    if start <= now.hour < end:
        return asset["trade_interval_hours"]
    return asset["off_hours_interval_hours"]


def _add_spread_to_ta(ta: dict, market_meta: dict, live_price: float) -> None:
    """2026-08-29 (na ziadost pouzivatela) - Strike get_market() uz vzdy vracia
    bid1_price/ask1_price/bid1_size/ask1_size (viz market_meta), ale doteraz sa
    to nikdy nedostalo do promptu pre Claude (videl len mark_price). Spread je
    priamy signál likvidity/execution rizika - obzvlast dolezity pri tenkych
    trhoch (MINIMAX/UNITREE/ZHIPU/SKHYNIX). Mutuje `ta` na mieste (rovnaky vzor
    ako ostatne doplnkove polia - jednoducho pridane kluce, ktore sa potom
    cele zoberu do json.dumps(ta) v prompte, ziadne dalsie prepojenie treba).
    Ticho vynecha, ak market_meta chyba bid/ask (napr. uplne prazdny orderbook
    na velmi tenkom syntetickom trackeri) - nie je to chyba cyklu."""
    try:
        bid = float(market_meta.get("bid1_price"))
        ask = float(market_meta.get("ask1_price"))
        if bid > 0 and ask > 0 and ask >= bid and live_price > 0:
            ta["spread_pct"] = round((ask - bid) / live_price * 100, 4)
    except (TypeError, ValueError):
        pass


def _check_ta_scale(ta: dict, live_price: float, name: str) -> None:
    """Preventivna poistka proti scale-mismatch dat (2026-08-09, po SKHYNIX
    incidente - yfinance fallback v inej skale nez Strike nafucal watch_price
    na hodnotu, ktora bola voci live cene triviálne vzdy pravdiva, takze
    watch_monitor spustal cyklus na kazdom ticku). Existujuci SL/TP safety cap
    v risk_manager.py uz chrani SKUTOCNE OBCHODY (klampovanie na 0.1x-5x
    cieloveho %), ale watch_price/watch_direction ziadnu takuto ochranu
    nemali - a tento problem sa moze zopakovat s AKYMKOLVEK buducim zdrojom
    (nielen tymi uz opravenymi), preto kontrola tu nie je viazana na
    konkretny zdroj/symbol, len na fakt "TA posledna cena vs. live cena by
    mali byt v rozumnom pomere". Vyhodi, ak sa lisia viac nez
    config.TA_LIVE_PRICE_MISMATCH_RATIO-nasobne - zachyti to HNED pri zbere
    dat, este PRED Claude volanim (usetri aj naklad)."""
    last_price = ta.get("last_price")
    if not last_price or not live_price:
        return
    ratio = last_price / live_price
    if ratio > config.TA_LIVE_PRICE_MISMATCH_RATIO or ratio < 1 / config.TA_LIVE_PRICE_MISMATCH_RATIO:
        raise ValueError(
            f"[{name}] TA last_price ({last_price}) a Strike live_price ({live_price}) sa lisia "
            f"{ratio:.1f}x (limit {config.TA_LIVE_PRICE_MISMATCH_RATIO}x) - mozny scale mismatch "
            "datoveho zdroja."
        )


def _is_due(asset: dict, session) -> bool:
    """True ak od posledneho zaznamu tohto assetu uplynul jeho pozadovany
    interval pre aktualny casovy usek (viz _required_interval_hours) - teraz
    plati pre VSETKY assety rovnako (predtym mali non-variable_interval assety
    ako ADA vynimku a beeli na kazdom scheduler ticku bez vlastneho gate)."""
    now = datetime.now(timezone.utc)
    required_hours = _required_interval_hours(asset, now)

    last_log = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == asset["strike_symbol"])
        .order_by(CycleLog.created_at.desc())
        .first()
    )
    if last_log is None:
        return True

    last_time = last_log.created_at
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    elapsed_hours = (now - last_time).total_seconds() / 3600
    return elapsed_hours >= required_hours - _TIME_GATE_TOLERANCE_HOURS


def _source_usage_fields(asset: dict, marketaux_news, social, coinmarketcal_events) -> dict:
    """Zdrojova telemetria pre CycleLog (2026-08-19, na ziadost pouzivatela) -
    "Zdroje pre rozhodovanie" tab v dashboarde z toho pocita % vyuzitia
    kazdeho zdroja za poslednych 24h. None pre marketaux/coinmarketcal =
    tento zdroj sa pre dany asset vobec nekonfiguruje (odlisuje sa od False =
    nakonfigurovany, ale zlyhal/prazdny). social sa vola VZDY (nezavisle od
    configu), preto tam None netreba - 0 uz sam o sebe znamena "skusene,
    nic nenajdene"."""
    return {
        "marketaux_used": bool(marketaux_news) if asset.get("marketaux_query") else None,
        "social_post_count": len(social) if social is not None else 0,
        "coinmarketcal_used": bool(coinmarketcal_events) if asset.get("coinmarketcal_slug") else None,
    }


def _config_snapshot(asset: dict) -> dict:
    """Aktualne aktivne trading/risk nastavenia pre dany asset - uklada sa s
    kazdym cyklom, aby dashboard vzdy zobrazoval presne to, s cim bot naozaj
    bezal (zmena v Railway env premennych sa prejavi uz na dalsom cykle)."""
    return {
        "symbol": asset["strike_symbol"],
        "asset_name": asset["name"],
        "enabled": asset["enabled"],
        "dry_run": config.DRY_RUN,
        "trade_interval_hours": asset["trade_interval_hours"],
        "off_hours_interval_hours": asset["off_hours_interval_hours"],
        "weekend_interval_hours": asset["weekend_interval_hours"],
        "trading_hours_start_utc": asset["trading_hours_start_utc"],
        "trading_hours_end_utc": asset["trading_hours_end_utc"],
        "monitor_interval_minutes": config.MONITOR_INTERVAL_MINUTES,
        "watch_interval_minutes": config.WATCH_INTERVAL_MINUTES,
        "position_max_hours": config.POSITION_MAX_HOURS,
        "macro_event_max_triggers_per_hour": config.MACRO_EVENT_MAX_TRIGGERS_PER_HOUR,
        "health_check_loss_trigger_fraction": config.HEALTH_CHECK_LOSS_TRIGGER_FRACTION,
        "min_confidence": asset["min_confidence"],
        "margin_usd": asset["margin_usd"],
        # POZOR (2026-08-08): "leverage" uz NIE JE skutocne pouzita paka -
        # tu je od tejto zmeny DOPOCITAVANA per-obchod z SL vzdialenosti +
        # liquidation_cushion_multiple (viz risk_manager._leverage_from_cushion),
        # tento fixny asset["leverage"] ostava len ako historicky/referencny
        # udaj (retrospective.py fallback pre stare zaznamy). Skutocna paka
        # kazdeho obchodu je vzdy vidiet v Obchody tabe (Trade.leverage).
        "leverage": asset["leverage"],
        "liquidation_cushion_multiple": asset["liquidation_cushion_multiple"],
        "default_sl_pct": asset["sl_pct"],
        "default_tp_pct": asset["tp_pct"],
        "claude_model": config.CLAUDE_MODEL,
        "effort": asset.get("effort") or None,
    }


def _upsert_rolling(session, symbol: str, summary: str | None, based_through_date: str) -> None:
    """Vytvori/aktualizuje JEDINY RollingRetrospective riadok pre tento symbol.
    Ak summary je None (nic nove na zapracovanie - napr. vcera nebehal ziaden
    cyklus), len posunie based_through_date bez zmeny existujuceho textu."""
    rolling = session.query(RollingRetrospective).filter(RollingRetrospective.symbol == symbol).first()
    if rolling is None:
        session.add(RollingRetrospective(symbol=symbol, summary=summary, based_through_date=based_through_date))
    else:
        if summary is not None:
            rolling.summary = summary
        rolling.based_through_date = based_through_date
        rolling.updated_at = datetime.now(timezone.utc)


def _save_pending_retrospective(name: str, symbol: str, pending_stats: dict, decision: dict, session) -> None:
    """Zdielana ulozit-ak-je-co logika pre pending_stats z _get_retrospective_context
    - vola sa z run_cycle_for_asset (bezny cyklus) AJ z _run_position_health_check
    (2026-08-17, ked je 'pending retrospektiva' sama osebe dovodom na eskalaciu
    na plny Claude cyklus aj pri otvorenej pozicii - viz volajuci)."""
    for_date = pending_stats["for_date"]
    # Dve NEZAVISLE izolovane transakcie - zlyhanie jednej nesmie zobrat so
    # sebou druhu ani nizsie cycle_log/trade zapisy. Duplicity osetrene
    # explicitne (existence check), lebo based_through_date (gate v
    # _get_retrospective_context) sa posunie az pri uspesnom summary_reflection
    # - ak ten chyba/zlyha, tento cyklus sa moze na dalsom tiku zopakovat a bez
    # tejto kontroly by vznikol duplicitny DailyRetrospective riadok za rovnaky den.
    if decision.get("daily_reflection"):
        try:
            already = session.query(DailyRetrospective.id).filter(
                DailyRetrospective.symbol == symbol, DailyRetrospective.for_date == for_date,
            ).first()
            if not already:
                session.add(DailyRetrospective(
                    symbol=symbol, for_date=for_date,
                    stats=pending_stats, reflection=decision["daily_reflection"],
                ))
                session.commit()
                print(f"[{name}] Ulozena denna retrospektiva za {for_date}.")
        except Exception as e:
            print(f"[{name}] Ulozenie dennej retrospektivy zlyhalo, pokracujem: {e}")
            session.rollback()

    if decision.get("summary_reflection"):
        try:
            _upsert_rolling(session, symbol, decision["summary_reflection"], for_date)
            session.commit()
            print(f"[{name}] Aktualizovane priebezne zhrnutie (based_through={for_date}).")
        except Exception as e:
            print(f"[{name}] Ulozenie priebezneho zhrnutia zlyhalo, pokracujem: {e}")
            session.rollback()


# Kolko WATCH-triggered cyklov za sebou (bez otvorenej pozicie medzi nimi) sa
# uz stalo pre tento symbol - 2026-08-19, na ziadost pouzivatela po HYPE
# zacykleni (watch_price/watch_direction sa spustal opakovane kazdych par
# minut, Claude vzdy zvolil 'none' + hned znova nastavil novu tesnu watch
# uroven, bez akehokolvek povedomia o vlastnom opakujucom sa vzore - na
# rozdiel od _get_confidence_streak nizsie, ktory pokryva LEN long/short pod
# prahom, nie direction='none'+watch). Nad tento pocet sa novo nastavena
# watch uroven MECHANICKY zmaze (viz run_cycle_for_asset) - Claude tak
# nemoze retazec predlzit donekonecna ani keby prompt kontext ignoroval,
# padne spat na bezny hodinovy interval.
_WATCH_RETRIGGER_HARD_LIMIT = 3


def _get_watch_retrigger_streak(symbol: str, session) -> dict | None:
    """Analogicke k _get_confidence_streak, ale pre watch-retrigger slucku
    namiesto opakovaneho near-threshold smeru. Retazec sa pocita od
    NAJNOVSIEHO zaznamu spat, kym neprejde cyklus, ktory NEBOL watch-triggered,
    alebo cyklus s outcome='opened' (poziciu uz otvoril, retazec sa prerusil)."""
    logs = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == symbol)
        .order_by(CycleLog.created_at.desc())
        .limit(20)
        .all()
    )
    streak = []
    for log in logs:
        if log.outcome == "opened" or not log.triggered_by_watch:
            break
        streak.append(log)
    if not streak:
        return None
    return {
        "count": len(streak),
        "entries": [
            {"watch_price": l.watch_price, "watch_direction": l.watch_direction,
             "confidence": l.confidence, "direction": l.direction, "live_price": l.live_price}
            for l in reversed(streak)  # najstarsi prvy
        ],
    }


def _get_watch_set_context(symbol: str, session) -> dict | None:
    """Najde najnovsi CycleLog, ktory nastavil watch_price/watch_direction pre
    tento symbol - POUZIVA SA LEN ked je AKTUALNY beh watch-triggered, aby
    Claude videl VLASTNE odovodnenie (watch_rationale) cakania spred spustenia.

    Na rozdiel od _get_watch_retrigger_streak vyssie NEVYZADUJE, aby predosly
    zaznam sam mal triggered_by_watch=True - cyklus, ktory watch NASTAVI
    (napr. post-close review po TP/SL), sam typicky watch-triggered NIE JE
    (bol vyvolany zatvorenim pozicie, nie watchom), takze by ho retrigger-streak
    funkcia hned na zaciatku preskocila a tento kontext by sa nikdy nezobrazil
    (viz diskusia s pouzivatelom o ZEC 09:33->09:34 rozpore)."""
    log = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == symbol, CycleLog.watch_price.isnot(None))
        .order_by(CycleLog.created_at.desc())
        .first()
    )
    if not log:
        return None
    return {
        "created_at": log.created_at, "live_price": log.live_price,
        "direction": log.direction, "confidence": log.confidence,
        "watch_price": log.watch_price, "watch_direction": log.watch_direction,
        "watch_rationale": log.watch_rationale,
    }


def _get_confidence_streak(symbol: str, min_confidence: int, session) -> dict | None:
    """Zisti, kolko POSLEDNYCH cyklov za sebou Claude navrhol ROVNAKY smer
    (long/short) s confidence POD prahom (teda kazdy z nich by bol zamietnuty)
    - bez toho, aby sa smer medzitym zmenil, otocil na 'none', alebo cyklus
    presiel prahom (co by znamenalo, ze uz sa obchod otvoril). Ucel: dat
    Claude-ovi KONKRETNY, spocitany fakt namiesto spoliehania sa na to, ze si
    sam vsimne vlastny opakujuci sa vzor naprieč viacerymi cyklami (dostane
    inak len key_assumptions z JEDNEHO predchadzajuceho cyklu, nie dlhsiu
    historiu) - viz diskusia 2026-08 o systematicky nadhodnotenej opatrnosti
    (napr. opakovane "RSI extrem, riziko odrazu" bez toho, aby sa odraz realne
    stal). Vracia None ak streak < 3 (prilis kratky na to, aby stal za zmienku)."""
    logs = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == symbol, CycleLog.direction.in_(["long", "short"]))
        .order_by(CycleLog.created_at.desc())
        .limit(20)
        .all()
    )
    if not logs or logs[0].confidence is None or logs[0].confidence >= min_confidence:
        return None  # posledny cyklus uz presiel prahom (alebo je prazdny) - ziadny streak na hlasenie

    direction = logs[0].direction
    streak = []
    for log in logs:
        if log.direction != direction or log.confidence is None or log.confidence >= min_confidence:
            break
        streak.append(log)
    if len(streak) < 3:
        return None

    start_log, current_log = streak[-1], streak[0]  # najstarsi / najnovsi v streaku
    if not start_log.live_price or not current_log.live_price:
        return None

    return {
        "direction": direction,
        "streak_len": len(streak),
        "avg_confidence": sum(l.confidence for l in streak) / len(streak),
        "price_change_pct": (current_log.live_price - start_log.live_price) / start_log.live_price * 100,
    }


# 2026-08-26 (na ziadost pouzivatela, po portfolio-wide audite chase-breakout
# strat naprieč tickermi) - NAMIESTO mechanickeho "streak" pocitadla (co by
# rovnako trestalo aj nesuvisiace straty za sebou, viz diskusia s pouzivatelom -
# "pocet samostatny nedava zmysel") davame Claude-ovi SUROVY material o
# poslednych uzavretych obchodoch tohto symbolu (vratane closed_trade_reflection/
# sl_tp_calibration_verdict z ich post-close review, ak uz existuju) a
# NECHAME HO SAMEHO posudit, ci ide o opakujuci sa vzor - rovnaky princip ako
# _VOLUME_NOTE v claude_analyst.py (fakty, nie hotovy verdikt).
_RECENT_TRADES_CONTEXT_LIMIT = 4


def _get_recent_closed_trades_context(symbol: str, session) -> list[dict] | None:
    trades = (
        session.query(Trade)
        .filter(Trade.symbol == symbol, Trade.status != "open")
        .order_by(Trade.closed_at.desc())
        .limit(_RECENT_TRADES_CONTEXT_LIMIT)
        .all()
    )
    if not trades:
        return None

    trade_ids = [t.id for t in trades]
    open_confidence = {
        log.trade_id: log.confidence
        for log in session.query(CycleLog)
        .filter(CycleLog.trade_id.in_(trade_ids), CycleLog.outcome == "opened")
        .all()
    }
    reviews = {
        log.reviewed_trade_id: log
        for log in session.query(CycleLog)
        .filter(CycleLog.reviewed_trade_id.in_(trade_ids))
        .all()
    }

    now = datetime.now(timezone.utc)
    out = []
    for t in reversed(trades):  # najstarsi prvy, najnovsi (najvyznamnejsi) posledny
        hours_ago = None
        if t.closed_at is not None:
            closed_at = t.closed_at
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            hours_ago = (now - closed_at).total_seconds() / 3600
        review = reviews.get(t.id)
        out.append({
            "direction": t.direction,
            "confidence": open_confidence.get(t.id),
            "close_reason": t.close_reason,
            "pnl_usd": t.pnl_usd,
            "hours_ago": hours_ago,
            "reflection": review.closed_trade_reflection if review else None,
            "sl_tp_verdict": review.sl_tp_calibration_verdict if review else None,
        })
    return out


# 2026-08-27 (prierez cez CELE portfolio, nie len jeden ticker) - portfolio malo
# 69% win rate pocas potvrdeneho silneho BTC rally (18.-21.8), ale len 26%
# (OBOMA smermi rovnako zle) pocas nasledujuceho plocheho/range-bound obdobia
# (22.-27.8) - _get_recent_closed_trades_context vyssie ukazuje KAZDEMU tickeru
# LEN jeho vlastnu malu vzorku, takze ziaden jednotlivy cyklus nevidel fakt, ze
# CELE portfolio naraz prehrava. 48h okno a min. 5 obchodov - kratsie/menej by
# bolo prilis nahodny signal na to, aby stal za zmienku (rovnaky duch ako
# "n je mala vzorka" upozornenia v retrospective.py).
_PORTFOLIO_PERFORMANCE_LOOKBACK_HOURS = 48
_PORTFOLIO_PERFORMANCE_MIN_TRADES = 5


def _get_portfolio_recent_performance(session) -> dict | None:
    """Cross-tickerova (NIE per-symbol) uspesnost za poslednych
    _PORTFOLIO_PERFORMANCE_LOOKBACK_HOURS hodin, naprieč VSETKYMI symbolmi.
    None ak je vzorka prilis mala na zmysluplny signal."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_PORTFOLIO_PERFORMANCE_LOOKBACK_HOURS)
    trades = (
        session.query(Trade)
        .filter(Trade.closed_at.isnot(None), Trade.closed_at >= cutoff, Trade.pnl_usd.isnot(None))
        .all()
    )
    if len(trades) < _PORTFOLIO_PERFORMANCE_MIN_TRADES:
        return None
    wins = sum(1 for t in trades if t.pnl_usd >= 0)
    return {
        "n": len(trades),
        "win_rate_pct": wins / len(trades) * 100,
        "net_pnl_usd": sum(t.pnl_usd for t in trades),
        "lookback_hours": _PORTFOLIO_PERFORMANCE_LOOKBACK_HOURS,
    }


# 2026-08-29 (na ziadost pouzivatela) - risk_manager.validate_and_size doteraz
# bral do uvahy LEN, ci je uz otvorena pozicia na TOMTO ISTOM symbole
# (has_open_position) - o ostatnych SUCASNE otvorenych poziciach (a ich
# korelacii s TYMTO tickerom) Claude nevedel VOBEC NIC. Mohol tak nevedomky
# pridavat silne korelovanu expoziciu (napr. dalsi krypto long popri uz
# otvorenom ADA aj NEAR longu - tie mali v testoch 0.6-0.7 korelaciu). Toto je
# INFORMACNY fakt (rovnaky "facts not verdicts" duch ako ostatne doplnky), nie
# tvrdy blok - konecne rozhodnutie (znizit confidence/ist mensie/ist inym
# smerom) necha na Claude, rovnako ako pri _VOLUME_NOTE.
_PORTFOLIO_EXPOSURE_CORR_MIN_OVERLAP = 15
_PORTFOLIO_EXPOSURE_LOOKBACK_DAYS = 30


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Rovnaka metodika ako dashboard (nas100-monitor-web/index.html
    pearsonCorrelation) - Pearson korelacia na dvoch rovnako dlhych zoznamoch,
    None pri nulovom rozptyle (konstantny rad)."""
    n = len(xs)
    if n == 0:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def _get_portfolio_exposure_context(symbol: str, session) -> list[dict] | None:
    """Pre KAZDU inu momentalne otvorenu poziciu (iny symbol nez `symbol`)
    spocita Pearson korelaciu hodinovych log-vynosov (posledych
    _PORTFOLIO_EXPOSURE_LOOKBACK_DAYS dni, min.
    _PORTFOLIO_EXPOSURE_CORR_MIN_OVERLAP prekryvajucich sa barov, inak None -
    rovnaky prah ako dashboard). None (cely vysledok), ak nie je ziadna ina
    otvorena pozicia - vtedy nie je co ukazat."""
    others = session.query(Trade).filter(Trade.symbol != symbol, Trade.status == "open").all()
    if not others:
        return None

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_PORTFOLIO_EXPOSURE_LOOKBACK_DAYS)

    def _closes_by_hour(sym: str) -> dict:
        return {
            b.hour_start: b.close
            for b in session.query(PriceBar.hour_start, PriceBar.close)
            .filter(PriceBar.symbol == sym, PriceBar.hour_start >= cutoff)
            .all()
        }

    own_bars = _closes_by_hour(symbol)

    out = []
    for trade in others:
        correlation = None
        other_bars = _closes_by_hour(trade.symbol)
        common_hours = sorted(set(own_bars) & set(other_bars))
        pairs = [(own_bars[h], other_bars[h]) for h in common_hours]
        returns_pairs = [
            (math.log(pairs[i][0] / pairs[i - 1][0]), math.log(pairs[i][1] / pairs[i - 1][1]))
            for i in range(1, len(pairs))
            if pairs[i - 1][0] > 0 and pairs[i][0] > 0 and pairs[i - 1][1] > 0 and pairs[i][1] > 0
        ]
        if len(returns_pairs) >= _PORTFOLIO_EXPOSURE_CORR_MIN_OVERLAP:
            own_returns = [p[0] for p in returns_pairs]
            other_returns = [p[1] for p in returns_pairs]
            corr = _pearson_correlation(own_returns, other_returns)
            correlation = round(corr, 2) if corr is not None else None
        out.append({
            "symbol": trade.symbol,
            "direction": trade.direction,
            "margin_usd": trade.margin_usd,
            "correlation": correlation,
        })
    return out


def _get_retrospective_context(asset: dict, session) -> tuple[str | None, str | None, dict | None]:
    """Vrati (retrospective_reflection, new_stats_text, pending_stats) pre tento asset.

    retrospective_reflection: aktualne RollingRetrospective.summary - priebezne
    aktualizovane zhrnutie CEZ VIAC DNI (nie len posledny den) - prenasa sa do
    KAZDEHO cyklu, kym ho Claude neaktualizuje pri dalsom prvom cykle dna.

    new_stats_text/pending_stats: ak vcerajsok (UTC) este NEBOL zapracovany do
    RollingRetrospective (based_through_date != vcera), cerstvo vypocitane
    (zdarma) statistiky - Claude ich v TOMTO cykle zreflektuje (daily_reflection
    + summary_reflection na submit_trade_decision) a run_cycle_for_asset ulozi
    vysledok - prve do DailyRetrospective (audit zaznam), druhe do
    RollingRetrospective (pending_stats = surove cisla na ulozenie do oboch).
    Ak vcera nebehal ziaden cyklus vobec, rovno oznaci vcerajsok ako spracovany
    bez Claude volania (niet co reflektovat)."""
    symbol = asset["strike_symbol"]
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    yesterday_str = yesterday.isoformat()

    rolling = session.query(RollingRetrospective).filter(RollingRetrospective.symbol == symbol).first()
    retrospective_reflection = rolling.summary if rolling else None

    if rolling is not None and rolling.based_through_date == yesterday_str:
        return retrospective_reflection, None, None

    stats = retrospective.compute_daily_stats(asset, yesterday, session)
    if stats["total_signals"] == 0 and stats.get("none_count", 0) == 0:
        # Ani jeden cyklus vcera (asset bol cely den mimo intervalu) - nie je co
        # reflektovat, len oznacime vcerajsok ako spracovany, aby sa to
        # nepokusalo prepocitavat kazdy dalsi cyklus dna.
        already = session.query(DailyRetrospective.id).filter(
            DailyRetrospective.symbol == symbol, DailyRetrospective.for_date == yesterday_str,
        ).first()
        if not already:
            session.add(DailyRetrospective(
                symbol=symbol, for_date=yesterday_str, stats=stats,
                reflection="(ziadne cykly, niet co hodnotit)",
            ))
        _upsert_rolling(session, symbol, None, yesterday_str)
        session.commit()
        return retrospective_reflection, None, None

    return retrospective_reflection, retrospective.format_stats_for_prompt(stats), stats


# 2026-08-19 produkcny nalez: rozne cykly casto pomenuju TU ISTU realnu
# udalost mierne inak ("FOMC minutes" vs "FOMC Minutes Release" vs "FOMC
# Minutes (July 28-29 meeting)" vs slovensky preklad...) - viz
# _save_flagged_macro_event nizsie, dedup teraz podla casovej blizkosti
# namiesto presneho mena.
_DUPLICATE_EVENT_WINDOW_HOURS = 3


def _save_flagged_macro_event(event: dict | None, symbol: str, session) -> None:
    """Ak Claude tento cyklus vratil upcoming_macro_event (viz claude_analyst
    DECISION_TOOL/POSITION_HEALTH_TOOL - vyznamna nadchadzajuca udalost, ktoru
    SAM zistil cez web_search), ulozi ju do FlaggedMacroEvent (ak uz tam nie
    je) - watch_monitor._check_macro_events ju neskor spusti. scope="this_asset"
    (default) = flagged_by_symbol=TENTO asset (spusti sa LEN preň).
    scope="all_assets" = flagged_by_symbol=None (spusti vsetky aktivne assety,
    rovnaky mechanizmus ako macro_calendar.py FOMC/CPI/NFP) - takto Claude
    priebezne SAM udrziava aj SIROKY makro kalendar, nie len assety-specificke
    udalosti (viz diskusia s pouzivatelom 2026-08: nechce rucne dopĺňat
    macro_calendar.py, chce aby to Claude robil sam prubezne). Nikdy nezhodi
    cely cyklus - chybne formatovany datum len zaloguje a ignoruje (Claude sa
    mohol pomylit vo formate, nie je to fatalne)."""
    if not event or not event.get("name") or not event.get("datetime_utc"):
        return
    try:
        dt = datetime.fromisoformat(str(event["datetime_utc"]).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        print(f"[trade_cycle] Neplatny upcoming_macro_event.datetime_utc "
              f"({event.get('datetime_utc')!r}): {e}, ignorujem.")
        return

    now = datetime.now(timezone.utc)
    # Zdravorozumova poistka proti zjavne chybnemu datumu (napr. zly rok) -
    # udalost by nemala byt v minulosti (dopredu tolerujeme malu rezervu pre
    # timezone chyby) ani prilis daleko v buducnosti.
    if dt < now - timedelta(hours=1) or dt > now + timedelta(days=180):
        print(f"[trade_cycle] upcoming_macro_event '{event['name']}' ma podozrivy "
              f"datum ({dt.isoformat()}), ignorujem.")
        return

    target_symbol = None if event.get("scope") == "all_assets" else symbol
    key = (f"{event['name']}_{dt.date().isoformat()}" if target_symbol is None
           else f"{event['name']}_{dt.date().isoformat()}_{target_symbol}")

    # 2026-08-19 produkcny nalez (FOMC minutes 19.8. zaznacene 8x pod roznymi
    # nazvami, vycerpalo hodinovy trigger limit takmer okamzite): povodny
    # dedup cez presny event_key retazec nechytil rozne pomenovania TEJ ISTEJ
    # udalosti. Teraz najprv skontrolujeme, ci uz existuje flagnuta udalost s
    # ROVNAKYM scope (target_symbol) a datetime_utc do _DUPLICATE_EVENT_WINDOW_HOURS
    # od tejto - ak ano, ide takmer isto o tu istu realnu udalost pod inym
    # menom, preskocime (prvy zaznamenany nazov/riadok "vyhrava").
    dt_naive = dt.replace(tzinfo=None)
    near_duplicate = (
        session.query(FlaggedMacroEvent.id)
        .filter(FlaggedMacroEvent.flagged_by_symbol == target_symbol)
        .filter(FlaggedMacroEvent.datetime_utc >= dt_naive - timedelta(hours=_DUPLICATE_EVENT_WINDOW_HOURS))
        .filter(FlaggedMacroEvent.datetime_utc <= dt_naive + timedelta(hours=_DUPLICATE_EVENT_WINDOW_HOURS))
        .first()
    )
    if near_duplicate:
        return

    exists = session.query(FlaggedMacroEvent.id).filter(FlaggedMacroEvent.event_key == key).first()
    if exists:
        return
    session.add(FlaggedMacroEvent(
        event_key=key, name=event["name"], datetime_utc=dt, flagged_by_symbol=target_symbol,
    ))
    scope_label = "vsetky assety" if target_symbol is None else target_symbol
    print(f"[trade_cycle] Nova makro udalost zaznacena Claudom: {key} ({dt.isoformat()}, scope={scope_label})")


# ZAMERNE NEOBSAHUJE "*_stalling" (viz market_data._trend_label, 2026-08-16) -
# ta kategoria znamena EMA struktura este v smere povodneho pohybu, ALE RSI
# uz je neutralny (momentum vyprchalo) - teda prave TEN pripad, ked by
# eskalacia na plny (plateny) health-check bola najcastejsie zbytocna
# (zastarany/lagujuci signal, nie skutocny cerstvy obrat proti pozicii).
# Skutocne potvrdeny obrat (strong_*/mild_* s momentum) tu ostava presne ako
# predtym.
_ADVERSE_TREND = {
    "long": {"strong_downtrend", "mild_downtrend"},
    "short": {"strong_uptrend", "mild_uptrend"},
}


def _mechanical_health_escalation(asset: dict, ta: dict, open_position: dict) -> str | None:
    """Bez Claude volania (zdarma) rozhodne, ci ma tento health check eskalovat
    na plny Claude cyklus (viz config.HEALTH_CHECK_LOSS_TRIGGER_FRACTION) - vrati
    dovod (str) ak ano, inak None. Pouziva UZ VYPOCITANE TA (market_data.
    get_market_snapshot - trend classification je jej sucastou zdarma) a
    open_position (uz vypocitany unrealized_pnl_pct) - ziadny extra fetch."""
    direction = (open_position["direction"] or "").lower()
    trend = ta.get("trend")
    if trend in _ADVERSE_TREND.get(direction, set()):
        return f"TA trend sa obratil proti pozicii (trend={trend})"

    loss_trigger_pct = -asset["sl_pct"] * config.HEALTH_CHECK_LOSS_TRIGGER_FRACTION
    pnl_pct = open_position["unrealized_pnl_pct"]
    if pnl_pct <= loss_trigger_pct:
        return (f"Nerealizovana strata {pnl_pct:.2f}% dosiahla "
                f"{config.HEALTH_CHECK_LOSS_TRIGGER_FRACTION * 100:.0f}% SL vzdialenosti "
                f"({asset['sl_pct']}%)")
    return None


def _run_position_health_check(asset: dict, open_trade: Trade, cross_market: dict, market_session: dict,
                                btc_proxy: dict | None, fred_macro: dict | None, session,
                                macro_event: str | None = None) -> None:
    """Ked uz je otvorena pozicia, namiesto predosleho ticheho 'skipped' zaznamu
    (2026-08 spatna vazba pouzivatela: chcel Claudeho priebezny nazor na
    otvorenu poziciu, nie len zahltenu historiu signalov bez obsahu) spustime
    'position health check' - Claude posudi, ci povodne kluc. predpoklady este
    platia. Bot SAM nikdy nemeni SL/TP na burze. Zatvorenie: PRI BEZNEJ
    (recommendation="hold" alebo consider_closing s nizsou istotou) je to len
    opinion pre cloveka (kill-switch tlacidlo v monitor-web). PRI VYSOKEJ istote
    (consider_closing + close_confidence >= config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD,
    2026-08-21 na ziadost pouzivatela po NAS100 SL incidente) bot poziciu zatvori
    SAM - viz _maybe_ai_early_close. Bezi na rovnakom _is_due() intervale ako
    bezny otvaraci cyklus (ziadny samostatny interval navyse)."""
    name = asset["name"]
    symbol = asset["strike_symbol"]
    print(f"[{name}] Otvorena pozicia (trade_id={open_trade.id}) - position health check namiesto skipu.")

    try:
        market_meta = strike_client.get_market(symbol)
        live_price = float(market_meta["mark_price"])
        ta = market_data.get_market_snapshot(asset, session)
        _check_ta_scale(ta, live_price, name)
        _add_spread_to_ta(ta, market_meta, live_price)
    except Exception as e:
        print(f"[{name}] Position health check: zber trhovych dat zlyhal, preskakujem: {e}")
        session.add(CycleLog(
            symbol=symbol, config_snapshot=_config_snapshot(asset),
            outcome="error", reject_reason=f"health_check_market_data_failed: {e}",
            trade_id=open_trade.id,
        ))
        session.commit()
        return

    opened_at = open_trade.opened_at
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    hours_held = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600

    is_long = (open_trade.direction or "").lower() == "long"
    pnl_pct = ((live_price - open_trade.entry_price) / open_trade.entry_price if is_long
               else (open_trade.entry_price - live_price) / open_trade.entry_price)

    # 2026-08-26 produkcny nalez (ZEC) - Claude pri position health checku
    # doteraz nevidel ZIADNY explicitny fakt o tom, ako blizko sa cena UZ
    # DOSTALA k TP a ako dlho odvtedy len stagnuje/vratila sa spat - musel by
    # si to sam vsimnut zo surovych sviecok, co pri stojacej teze (napr.
    # "sell-the-news pokles") lahko prehliadne v prospech potvrdenia povodnej
    # tezy. Najpriaznivejsia cena OD OTVORENIA (min low pre short, max high
    # pre long) + ako davno nastala je teraz explicitny, mechanicky vypocitany
    # fakt v prompte (viz claude_analyst.py position_block), nie nieco, co
    # musi sam odhalit.
    best_price_since_open = None
    best_price_hours_ago = None
    bars = (
        session.query(PriceBar)
        .filter(PriceBar.symbol == symbol, PriceBar.hour_start >= opened_at.replace(tzinfo=None))
        .order_by(PriceBar.hour_start)
        .all()
    )
    if bars:
        best_bar = max(bars, key=lambda b: b.high) if is_long else min(bars, key=lambda b: b.low)
        best_price_since_open = best_bar.high if is_long else best_bar.low
        best_bar_time = best_bar.hour_start
        if best_bar_time.tzinfo is None:
            best_bar_time = best_bar_time.replace(tzinfo=timezone.utc)
        best_price_hours_ago = (datetime.now(timezone.utc) - best_bar_time).total_seconds() / 3600

    open_position = {
        "direction": open_trade.direction,
        "entry_price": open_trade.entry_price,
        "live_price": live_price,
        "stop_loss_price": open_trade.stop_loss_price,
        "take_profit_price": open_trade.take_profit_price,
        "leverage": open_trade.leverage,
        "opened_at_str": opened_at.strftime('%A, %d. %B %Y, %H:%M UTC'),
        "hours_held": hours_held,
        "unrealized_pnl_usd": open_trade.margin_usd * open_trade.leverage * pnl_pct,
        "unrealized_pnl_pct": pnl_pct * 100,
        "best_price_since_open": best_price_since_open,
        "best_price_hours_ago": best_price_hours_ago,
    }

    escalation_reason = _mechanical_health_escalation(asset, ta, open_position)

    cooldown_active = False
    cooldown_bypassed_reason = None
    if escalation_reason is not None and open_trade.last_health_escalation_at is not None:
        last_esc = open_trade.last_health_escalation_at
        if last_esc.tzinfo is None:
            last_esc = last_esc.replace(tzinfo=timezone.utc)
        hours_since = (datetime.now(timezone.utc) - last_esc).total_seconds() / 3600
        cooldown_active = hours_since < config.HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS

        # 2026-08-27 (ADA #90 incident) - cooldown existuje na potlacenie
        # OPAKOVANIA toho isteho, uz posudeneho signalu, nie na umlcanie pozicie
        # kym dalej REALNE straca hodnotu. Ak sa P&L od poslednej eskalacie
        # zhorsil o dost (podiel SL vzdialenosti), toto uz je NOVY fakt -
        # cooldown sa obide bez ohladu na to, kolko z neho este zostava.
        if cooldown_active and open_trade.last_health_escalation_pnl_pct is not None:
            worsening_pct = open_trade.last_health_escalation_pnl_pct - pnl_pct
            bypass_threshold = asset["sl_pct"] * config.HEALTH_CHECK_COOLDOWN_BYPASS_WORSENING_FRACTION
            if worsening_pct >= bypass_threshold:
                cooldown_active = False
                cooldown_bypassed_reason = (
                    f"P&L sa od poslednej eskalacie zhorsil o dalsich {worsening_pct:.2f}% "
                    f"(>= {bypass_threshold:.2f}% = {config.HEALTH_CHECK_COOLDOWN_BYPASS_WORSENING_FRACTION * 100:.0f}% "
                    f"SL vzdialenosti {asset['sl_pct']}%)"
                )

    real_escalation = escalation_reason is not None and not cooldown_active
    if cooldown_bypassed_reason:
        # Claude by inak nemal ako vediet, ze tento cyklus je mimoriadny
        # dovolany-cez-cooldown re-check (nie bezna hodinova kontrola) - viz
        # claude_analyst.py position_block rendering nizsie.
        open_position["cooldown_bypass_reason"] = cooldown_bypassed_reason

    try:
        # 2026-08-17: "vcerajsok este nespracovany" (new_stats_text) je TERAZ
        # samostatny dovod na eskalaciu na plny Claude cyklus, nezavisly od
        # mechanickeho triggeru - inak (health check je mechanicky-default,
        # Claude sa vola len pri eskalacii) mohla retrospektiva pri dlho
        # drzanej pozicii ostat nespracovana aj viac dni, co viedlo k velmi
        # nekonzistentnym casom v dashboarde (spatna vazba pouzivatela).
        # Nepocita sa do last_health_escalation_at cooldownu nizsie - ten je
        # urceny pre opakovane danger-eskalacie, nie pre tento nezavisly
        # denny trigger.
        retrospective_reflection, new_stats_text, pending_stats = _get_retrospective_context(asset, session)
    except Exception as e:
        print(f"[{name}] Vypocet retrospektivy zlyhal (pokracujem bez nej): {e}")
        session.rollback()
        retrospective_reflection, new_stats_text, pending_stats = None, None, None

    if not real_escalation and new_stats_text is None:
        if escalation_reason is None:
            print(f"[{name}] Mechanicka kontrola: ziadny trigger (trend={ta.get('trend')}, "
                  f"P&L={pnl_pct * 100:.2f}%) - preskakujem plny Claude cyklus.")
            reasoning = (f"Mechanicka kontrola (bez Claude volania): trend={ta.get('trend')}, "
                         f"nerealizovany P&L={pnl_pct * 100:.2f}%, ziadny trigger na eskalaciu.")
        else:
            print(f"[{name}] Trigger na eskalaciu bol splneny ({escalation_reason}), ale posledna "
                  f"plna eskalacia bola pred menej nez {config.HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS}h "
                  "- preskakujem plny Claude cyklus (cooldown).")
            reasoning = (f"Mechanicka kontrola (bez Claude volania): trigger splneny ({escalation_reason}), "
                         f"ale eskalacia je v cooldowne (posledna pred menej nez "
                         f"{config.HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS}h).")
        session.add(CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session, config_snapshot=_config_snapshot(asset),
            direction=open_trade.direction, outcome="position_check",
            reasoning=reasoning,
            health_recommendation="hold",
            trade_id=open_trade.id,
        ))
        session.commit()
        return

    if real_escalation:
        if cooldown_bypassed_reason:
            print(f"[{name}] Cooldown OBIDENY ({cooldown_bypassed_reason}) - eskalujem na plny "
                  f"Claude cyklus: {escalation_reason}")
        else:
            print(f"[{name}] Mechanicka kontrola eskaluje na plny Claude cyklus: {escalation_reason}")
        open_trade.last_health_escalation_at = datetime.now(timezone.utc)
        open_trade.last_health_escalation_pnl_pct = pnl_pct
        session.add(open_trade)
        session.commit()
    else:
        print(f"[{name}] Ziadny mechanicky trigger (alebo je v cooldowne), ale vcerajsok este "
              "nie je spracovany v retrospektive - volam Claude len kvoli tomu.")

    social = social_sentiment.fetch_recent_posts(name)

    prev_log = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == symbol, CycleLog.key_assumptions.isnot(None))
        .order_by(CycleLog.created_at.desc())
        .first()
    )
    prev_assumptions = prev_log.key_assumptions if prev_log else None
    prev_cycle_time = prev_log.created_at if prev_log else None

    eia_data = None
    if asset.get("needs_eia_data"):
        try:
            eia_data = eia_client.get_weekly_crude_stocks()
        except Exception as e:
            print(f"[{name}] EIA fetch zlyhal (pokracujem bez neho): {e}")

    marketaux_news = None
    if asset.get("marketaux_query"):
        try:
            marketaux_news = marketaux_client.get_news_sentiment(asset["marketaux_query"])
        except Exception as e:
            print(f"[{name}] Marketaux fetch zlyhal (pokracujem bez neho): {e}")

    coinmarketcal_events = None
    if asset.get("coinmarketcal_slug"):
        try:
            coinmarketcal_events = coinmarketcal_client.get_cached_events(symbol, session)
        except Exception as e:
            print(f"[{name}] CoinMarketCal cache-read zlyhal (pokracujem bez neho): {e}")

    try:
        recent_trades_context = _get_recent_closed_trades_context(symbol, session)
    except Exception as e:
        print(f"[{name}] Vypocet nedavnej obchodnej historie zlyhal (pokracujem bez nej): {e}")
        recent_trades_context = None

    try:
        portfolio_performance = _get_portfolio_recent_performance(session)
    except Exception as e:
        print(f"[{name}] Vypocet portfolio-wide vykonnosti zlyhal (pokracujem bez neho): {e}")
        portfolio_performance = None

    try:
        portfolio_exposure = _get_portfolio_exposure_context(symbol, session)
    except Exception as e:
        print(f"[{name}] Vypocet portfolio-wide expozicie zlyhal (pokracujem bez nej): {e}")
        portfolio_exposure = None

    try:
        health, web_search_log, usage = claude_analyst.analyze_position_health(
            asset, open_position, ta, cross_market, market_session, social, btc_proxy,
            prev_assumptions, prev_cycle_time, retrospective_reflection,
            fred_macro, eia_data, marketaux_news, macro_event,
            new_stats_text=new_stats_text,
            coinmarketcal_events=coinmarketcal_events,
            recent_trades_context=recent_trades_context,
            portfolio_performance=portfolio_performance,
            portfolio_exposure=portfolio_exposure,
        )
    except Exception as e:
        print(f"[{name}] Position health check zlyhal: {e}")
        session.add(CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session, config_snapshot=_config_snapshot(asset),
            outcome="error", reject_reason=f"health_check_failed: {e}", trade_id=open_trade.id,
            **_source_usage_fields(asset, marketaux_news, social, coinmarketcal_events),
        ))
        session.commit()
        return

    print(f"[{name}] Position health check: {health}")
    _save_flagged_macro_event(health.get("upcoming_macro_event"), symbol, session)
    session.add(CycleLog(
        symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
        session_data=market_session, config_snapshot=_config_snapshot(asset),
        direction=open_trade.direction, outcome="position_check",
        reasoning=health.get("reasoning"),
        # key_assumptions je v POSITION_HEALTH_TOOL volitelne (viz claude_analyst.
        # _validate_health_decision) - ak ho Claude tento cyklus vynechal, radsej
        # prenesieme povodne predpoklady bez zmeny nez aby retazec pre buduci
        # cyklus (prev_log query vyssie) proste zmizol.
        key_assumptions=health.get("key_assumptions") or prev_assumptions,
        web_search_log=web_search_log,
        **_source_usage_fields(asset, marketaux_news, social, coinmarketcal_events),
        health_recommendation=health.get("recommendation"),
        health_expected_direction=health.get("expected_direction"),
        close_confidence=health.get("close_confidence"),
        trade_id=open_trade.id,
        triggered_by_macro_event=macro_event,
        usage_input_tokens=usage.get("input_tokens"),
        usage_cache_write_tokens=usage.get("cache_write_tokens"),
        usage_cache_read_tokens=usage.get("cache_read_tokens"),
        usage_output_tokens=usage.get("output_tokens"),
        effort=usage.get("effort"),
    ))
    session.commit()

    if pending_stats:
        _save_pending_retrospective(name, symbol, pending_stats, health, session)

    _maybe_ai_early_close(asset, open_trade, health, session)


# 2026-08-21 (na ziadost pouzivatela, po NAS100 SL incidente - Claude odporucil
# consider_closing s close_confidence=50 hodinu pred SL, pouzivatel to
# neposluchol a o hodinu padol SL) - vid tiez docstring _run_position_health_check
# vyssie, ktory teraz uz NIE JE cely presny ("bot sam poziciu nezatvara" platilo
# len DOTERAZ). Zamerne LEN presna zhoda (recommendation="consider_closing" A
# cislo >= config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD) - chybajuce/nespravne
# typovane close_confidence (napr. poskodena tool-call odpoved, viz
# claude_analyst._recover_malformed_fields) NIKDY nespusti akciu (fail-safe,
# nie fail-open). Rovnaky bezpecny uzatvaraci vzor ako existujuci timeout-close
# v position_monitor.py (cancel_all_orders + close_position_market), len tu v
# trade_cycle.py - ZIVU velkost pozicie si preto zisti cerstvo z burzy, nespolieha
# sa na (potencialne zastaranu) DB hodnotu Trade.size. NEVOLA _apply_exact_close/
# review/notifikaciu priamo (trade_cycle.py by musel importovat position_monitor,
# co by sposobilo cyklicky import - position_monitor uz importuje trade_cycle) -
# rovnaky vzor ako watch_monitor.py kill-switch: len nastavi status/close_reason/
# closed_at, zvysok (presny PnL, post-close review, Discord notifikacia, SL/TP
# recompute) doplni _backfill_missing_exact_data na najblizsom position_monitor
# tiku (do ~1 min).
def _maybe_ai_early_close(asset: dict, trade: Trade, health: dict, session) -> None:
    close_confidence = health.get("close_confidence")
    if health.get("recommendation") != "consider_closing" or not isinstance(close_confidence, (int, float)):
        return
    if close_confidence < config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD:
        return

    name = asset["name"]
    symbol = asset["strike_symbol"]
    print(f"[{name}] KRITICKE: Claude odporucil consider_closing s close_confidence="
          f"{close_confidence} (prah {config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD}) - "
          "zatvaram poziciu automaticky.")
    try:
        live_positions = strike_client.get_positions(symbol)
        live = next((p for p in live_positions if p.get("symbol") == symbol), None)
        if live is None:
            print(f"[{name}] AI early-close: pozicia uz nie je na burze (medzicasom uz zatvorena "
                  "inym mechanizmom) - preskakujem.")
            return
        strike_client.cancel_all_orders(symbol)
        # abs() - Strike "size" je znamienkove (zaporne pre short), ale
        # close_position_market ocakava absolutnu velkost (smer ide cez
        # trade.direction) - rovnaka chyba a oprava ako position_monitor.py
        # _check_and_reheal_bracket_legs (2026-08-22, NIGHT naked-position
        # incident) - bez tohto by AI early-close pre KAZDU short poziciu
        # vzdy zlyhal ("below minimum") a ticho sa vzdy len opakoval bez ucinku.
        strike_client.close_position_market(trade.direction, abs(float(live["size"])), symbol)
    except Exception as e:
        print(f"[{name}] AI early-close zlyhal (pozicia ostava otvorena, skusi sa znova "
              f"na dalsom cykle): {e}")
        return

    trade.status = "closed_by_ai"
    trade.close_reason = "ai_early_close"
    trade.closed_at = datetime.now(timezone.utc)
    session.add(trade)
    session.commit()


# Zamok DRZANY POCAS CELEHO run_cycle_for_asset behu (nielen "je uz otvoreny
# obchod?" kontrola na jeho zaciatku) - 2026-08-22 produkcny nalez (live peniaze):
# bezny naplanovany cyklus a watch-triggered cyklus (dispatch_triggered_check,
# samostatne vlakno) pre TEN ISTY symbol mohli bezat suvbezne. Kontrola "Trade uz
# otvoreny?" (nizsie) je len jednorazovy DB dotaz na zaciatku - kym prebieha
# zvysok cyklu (Claude + web_search, 30-90+ sekund), Trade este NIE JE
# commitnuty, takze druhy suvbezny beh pre ten isty symbol touto kontrolou
# prejde tiez a NEZAVISLE otvori DRUHU poziciu. Strike vsak nema koncept
# "dvoch pozicii" pre jeden symbol - obe sa interne zlucia do jednej
# agregovanej pozicie, co rozbije per-trade SL/TP aj nasledny PnL vypocet
# (viz ADA #82/#83 - dvojnasobne pripisana strata na dashboarde). Non-blocking
# acquire: ak je symbol prave "v lete", tento beh radsej preskocime (skusi sa
# znova nabuduce/o hodinu), nez aby scheduled beh cakal a blokoval spracovanie
# ostatnych tickerov v run_all_cycles.
_symbol_run_locks_guard = threading.Lock()
_symbol_run_locks: dict[str, threading.Lock] = {}

# 2026-08-24 - viz komentar pri closed_trade acquire nizsie v run_cycle_for_asset:
# post-close review dostane bounded WAIT (nie okamzity vzdanie sa) namiesto
# ciste non-blocking pokusu, kedze pre konkretny uz zatvoreny obchod ziadne
# "skus znova nabuduce" neexistuje. 90s dava rozumnu rezervu voci beznemu
# position_check/decision Claude volaniu, ktore ho mohlo predbehnut.
_POST_CLOSE_REVIEW_LOCK_WAIT_SECONDS = 90


def _get_symbol_run_lock(symbol: str) -> threading.Lock:
    with _symbol_run_locks_guard:
        if symbol not in _symbol_run_locks:
            _symbol_run_locks[symbol] = threading.Lock()
        return _symbol_run_locks[symbol]


def run_cycle_for_asset(asset: dict, cross_market: dict, market_session: dict,
                         btc_proxy: dict | None, fred_macro: dict | None = None,
                         skip_due_check: bool = False,
                         closed_trade: dict | None = None,
                         macro_event: str | None = None,
                         watch_triggered: bool = False) -> None:
    """Kompletny cyklus pre JEDEN asset - vlastna DB session/commit, aby chyba
    v jednom assete neponechala nedokoncenu transakciu pre dalsi.

    skip_due_check: run_triggered_check() (watch-trigger, viz nizsie) vola tuto
    funkciu MIMO bezneho hodinoveho tiku, prave preto, ze sledovana cenova
    podmienka sa splnila SKOR nez by bol dalsi pravidelny cyklus na rade -
    _is_due gate nizsie by takyto beh takmer vzdy zablokoval (interval od
    posledneho zaznamu este neuplynul), cim by celiplny zmysel watch_monitor.py
    (reagovat OKAMZITE na cenu, nie cakat na interval) - preto ho mimoriadny
    beh vynecha. Bezny naplanovany cyklus (run_all_cycles) tento parameter
    nenastavuje (default False), takze jeho gating ostava nezmeneny.

    closed_trade: ak nie None, tento beh je "post-close review" (viz
    position_monitor._fire_post_close_reviews) - dict s trade_id/direction/
    entry_price/exit_price/hours_held/pnl_usd/close_reason/evaluation_only o
    PRAVE zatvorenej pozicii, ktory sa vlozi do promptu (viz claude_analyst) a
    Claude zhodnoti, ci bolo zatvorenie spravne timeovane (closed_trade_reflection).
    Ak closed_trade["evaluation_only"] je True (SL/likvidacia - viz
    position_monitor._EVALUATION_ONLY_CLOSE_REASONS), Claudeho navrhnute
    otvaracie rozhodnutie z tohto behu sa NIKDY nevykona (viz kod nizsie tesne
    pred risk_manager.validate_and_size) - ziskame len hodnotu do retrospektivy,
    bez rizika revenge-tradingu okamzitym re-entry.

    macro_event: ak nie None, tento beh bol vyvolany PRAVE zverejnenou makro
    udalostou s pevne znamym casom (FOMC/CPI/NFP - viz macro_calendar.py +
    watch_monitor._check_macro_events), napr. "CPI". Vlozi sa do promptu
    (viz claude_analyst), aby Claude vedel, PRECO cyklus bezi mimo bezneho
    intervalu a cielene si to cez web_search overil. Nezavisle od closed_trade
    (obe sa mozu teoreticky zisst v tom istom cykle, ak makro udalost prijde
    tesne po zatvoreni pozicie).

    watch_triggered: True ak tento beh vyvolala splnena watch_price/watch_direction
    podmienka (viz watch_monitor.py) - zapise sa do CycleLog.triggered_by_watch
    a pouzije sa na _get_watch_retrigger_streak (viz jej docstring vyssie)."""
    name = asset["name"]
    symbol = asset["strike_symbol"]

    lock = _get_symbol_run_lock(symbol)
    # Post-close review (closed_trade nastavene) je JEDNORAZOVA prilezitost -
    # na rozdiel od bezneho/watch cyklu, ktory jednoducho skusi znova na svojom
    # dalsom pravidelnom tiku, pre KONKRETNY uz zatvoreny obchod ziadne
    # "nabuduce" neexistuje. 2026-08-24 produkcny nalez (ZEC #96): bezna
    # position_check kontrola drzala zamok pocas vlastneho Claude volania
    # presne v momente, kedy prisla poziadavka na review prave zatvoreneho
    # obchodu - povodny okamzity non-blocking pokus ju vtedy ticho a NATRVALO
    # zahodil. Preto LEN pre closed_trade davame kratky bounded wait namiesto
    # okamziteho vzdania - blokuje len TENTO dispatch-thread na pozadi
    # (position_monitor svoj vlastny tik uz davno dokoncil, nic tym
    # nezastavujeme), nie hlavny beh ostatnych tickerov.
    acquired = lock.acquire(timeout=_POST_CLOSE_REVIEW_LOCK_WAIT_SECONDS) if closed_trade \
        else lock.acquire(blocking=False)
    if not acquired:
        print(f"[{name}] Iny beh pre {symbol} prave prebieha (subezny cyklus) - "
              f"preskakujem, aby sme predisli duplicitnemu otvoreniu (viz ADA "
              f"#82/#83 incident 2026-08-22).")
        skip_session = get_session()
        try:
            skip_session.add(CycleLog(
                symbol=symbol,
                config_snapshot=_config_snapshot(asset),
                outcome="skipped_concurrent_cycle",
                reject_reason="iny beh pre tento symbol uz prebieha",
            ))
            skip_session.commit()
        finally:
            skip_session.close()
        return

    print(f"\n--- [{name}] ---")
    session = get_session()
    try:
        # SL/TP override (2026-08-19, viz risk_overrides.py + db.RiskOverride) -
        # ak pouzivatel cez nas100-monitor-web "Nastavit ako default" tlacidlo
        # aplikoval novu ATR-kalibrovanu hodnotu, MA PREDNOST pred config.py
        # defaultom. Resolvuje sa RAZ tu a dalej sa pouziva LOKALNA kopia asset
        # dictu (nemutuje zdielany assets.ALL_ASSETS) - vsetko nizsie (risk
        # sizing aj _config_snapshot pre dashboard) tak automaticky vidi
        # efektivnu hodnotu bez dalsich zmien.
        effective_sl, effective_tp = risk_overrides.get_effective_sl_tp(session, asset)
        if effective_sl != asset["sl_pct"] or effective_tp != asset["tp_pct"]:
            asset = {**asset, "sl_pct": effective_sl, "tp_pct": effective_tp}

        if not skip_due_check and not _is_due(asset, session):
            # Ziadny CycleLog zaznam - toto sa deje bezne (kazdy druhy/dalsi tick
            # mimo trading hours/cez vikend) a nema analyticku hodnotu, len by to
            # zahltilo historiu signalov nezaujimavymi zaznamami.
            print(f"[{name}] Mimo aktualneho intervalu (off-hours/vikend gating) - preskakujem.")
            return

        open_trade = session.query(Trade).filter(
            Trade.symbol == symbol, Trade.status == "open",
        ).first()
        if open_trade:
            _run_position_health_check(asset, open_trade, cross_market, market_session, btc_proxy,
                                        fred_macro, session, macro_event)
            return

        try:
            market_meta = strike_client.get_market(symbol)
            live_price = float(market_meta["mark_price"])

            ta = market_data.get_market_snapshot(asset, session)
            _check_ta_scale(ta, live_price, name)
            _add_spread_to_ta(ta, market_meta, live_price)
            social = social_sentiment.fetch_recent_posts(name)
            print(f"[{name}] Strike live_price={live_price} | TA: {ta}")
            print(f"[{name}] Nacitanych {len(social)} social prispevkov (spravy hlada Claude sam cez web_search).")
        except Exception as e:
            # Strike/yfinance API vypadok tu predtym zhodil cely cyklus neodchytenou
            # vynimkou - radsej zalogujeme a bezpecne preskocime len tento asset.
            print(f"[{name}] Zber trhovych dat zlyhal, preskakujem cyklus: {e}")
            session.add(CycleLog(
                symbol=symbol,
                config_snapshot=_config_snapshot(asset),
                outcome="error", reject_reason=f"market_data_fetch_failed: {e}",
            ))
            session.commit()
            return

        prev_log = (
            session.query(CycleLog)
            .filter(CycleLog.symbol == symbol, CycleLog.key_assumptions.isnot(None))
            .order_by(CycleLog.created_at.desc())
            .first()
        )
        prev_assumptions = prev_log.key_assumptions if prev_log else None
        prev_cycle_time = prev_log.created_at if prev_log else None

        try:
            confidence_streak = _get_confidence_streak(symbol, asset["min_confidence"], session)
        except Exception as e:
            print(f"[{name}] Vypocet confidence streak zlyhal (pokracujem bez neho): {e}")
            confidence_streak = None

        try:
            recent_trades_context = _get_recent_closed_trades_context(symbol, session)
        except Exception as e:
            print(f"[{name}] Vypocet nedavnej obchodnej historie zlyhal (pokracujem bez nej): {e}")
            recent_trades_context = None

        try:
            portfolio_performance = _get_portfolio_recent_performance(session)
        except Exception as e:
            print(f"[{name}] Vypocet portfolio-wide vykonnosti zlyhal (pokracujem bez neho): {e}")
            portfolio_performance = None

        try:
            portfolio_exposure = _get_portfolio_exposure_context(symbol, session)
        except Exception as e:
            print(f"[{name}] Vypocet portfolio-wide expozicie zlyhal (pokracujem bez nej): {e}")
            portfolio_exposure = None

        # 2026-08-22 produkcny nalez (ADA data_issue false-alarm): predtym sa
        # toto pocitalo VZDY, bez ohladu na to, ci JE TENTO beh watch-triggered -
        # ak PREDOSLY cyklus bol watch-triggered, ale TENTO je len bezny
        # naplanovany beh, watch_retrigger_block nizsie (claude_analyst.py) aj
        # tak Claude-ovi tvrdil "toto je uz N. mimoriadny cyklus za sebou", hoci
        # TENTO konkretny beh ziadny mimoriadny nebol - preukazatelne zavadzajuce
        # (over. cez DB - 29x v historii). Rovnaky gate ako pri watch_set_context
        # nizsie (ten isty princip: kontext o retazci ma zmysel LEN, ked je jeho
        # SUCASTOU aj tento beh) - _WATCH_RETRIGGER_HARD_LIMIT kontrola pri
        # otvoreni uz aj tak vyzaduje watch_triggered zvlast, takze toto nic
        # nemeni na jej sprvani.
        watch_retrigger_streak = None
        if watch_triggered:
            try:
                watch_retrigger_streak = _get_watch_retrigger_streak(symbol, session)
            except Exception as e:
                print(f"[{name}] Vypocet watch retrigger streak zlyhal (pokracujem bez neho): {e}")

        watch_set_context = None
        if watch_triggered:
            try:
                watch_set_context = _get_watch_set_context(symbol, session)
            except Exception as e:
                print(f"[{name}] Vypocet watch set contextu zlyhal (pokracujem bez neho): {e}")

        try:
            retrospective_reflection, new_stats_text, pending_stats = _get_retrospective_context(asset, session)
        except Exception as e:
            # Retrospektiva je cisto doplnkova (uciaci feature) - jej zlyhanie
            # (napr. yfinance vypadok pri prepocitavani vcerajsich stats) NESMIE
            # zhodit skutocny obchodny cyklus. Radsej pokracujeme bez nej (skusi
            # sa znova na dalsom cykle, kedze DailyRetrospective sa neulozila).
            print(f"[{name}] Vypocet retrospektivy zlyhal, pokracujem bez nej: {e}")
            session.rollback()
            retrospective_reflection, new_stats_text, pending_stats = None, None, None

        # Doplnkove datove zdroje (2026-07-31) - volitelne, nikdy neblokuju cyklus.
        eia_data = None
        if asset.get("needs_eia_data"):
            try:
                eia_data = eia_client.get_weekly_crude_stocks()
                print(f"[{name}] EIA tyzdenne zasoby ropy: {eia_data}")
            except Exception as e:
                print(f"[{name}] EIA fetch zlyhal (pokracujem bez neho): {e}")

        marketaux_news = None
        if asset.get("marketaux_query"):
            try:
                marketaux_news = marketaux_client.get_news_sentiment(asset["marketaux_query"])
                print(f"[{name}] Marketaux news: {marketaux_news}")
            except Exception as e:
                print(f"[{name}] Marketaux fetch zlyhal (pokracujem bez neho): {e}")

        coinmarketcal_events = None
        if asset.get("coinmarketcal_slug"):
            try:
                coinmarketcal_events = coinmarketcal_client.get_cached_events(symbol, session)
                print(f"[{name}] CoinMarketCal udalosti: {coinmarketcal_events}")
            except Exception as e:
                print(f"[{name}] CoinMarketCal cache-read zlyhal (pokracujem bez neho): {e}")

        try:
            decision, web_search_log, usage = claude_analyst.analyze(
                asset, ta, cross_market, market_session, social, btc_proxy,
                prev_assumptions, prev_cycle_time,
                retrospective_reflection, new_stats_text,
                fred_macro, eia_data, marketaux_news,
                confidence_streak, closed_trade, macro_event,
                coinmarketcal_events=coinmarketcal_events,
                watch_retrigger_streak=watch_retrigger_streak,
                watch_set_context=watch_set_context,
                recent_trades_context=recent_trades_context,
                portfolio_performance=portfolio_performance,
                portfolio_exposure=portfolio_exposure,
            )
        except Exception as e:
            print(f"[{name}] Claude analyza zlyhala, preskakujem cyklus: {e}")
            session.add(CycleLog(
                symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
                session_data=market_session,
                config_snapshot=_config_snapshot(asset),
                outcome="error", reject_reason=str(e),
                **_source_usage_fields(asset, marketaux_news, social, coinmarketcal_events),
            ))
            session.commit()
            return
        print(f"[{name}] Claude rozhodnutie: {decision}")
        print(f"[{name}] Web search log: {web_search_log}")
        _save_flagged_macro_event(decision.get("upcoming_macro_event"), symbol, session)

        if pending_stats:
            _save_pending_retrospective(name, symbol, pending_stats, decision, session)

        # 2026-08-19 (na ziadost pouzivatela, po HYPE zacykleni) - mechanicka
        # poistka NEZAVISLA od promptoveho kontextu (watch_retrigger_block v
        # claude_analyst.py): ak uz tento symbol ma _WATCH_RETRIGGER_HARD_LIMIT+
        # watch-triggered cyklov za sebou bez otvorenia pozicie, NOVU watch
        # uroven z tohto rozhodnutia zahodime bez ohladu na to, co Claude
        # vratil - direction/confidence/SL/TP ostavaju netknute, len sa
        # nenastavi dalsia cenova pascka, ktora by retazec predlzila donekonecna.
        watch_price = decision.get("watch_price")
        watch_direction = decision.get("watch_direction")
        watch_price_2 = decision.get("watch_price_2")
        watch_direction_2 = decision.get("watch_direction_2")
        watch_rationale = decision.get("watch_rationale")
        if (watch_triggered and watch_retrigger_streak
                and watch_retrigger_streak["count"] >= _WATCH_RETRIGGER_HARD_LIMIT
                and (watch_price is not None or watch_price_2 is not None)):
            print(f"[{name}] Watch retrigger streak ({watch_retrigger_streak['count']}) dosiahol limit "
                  f"({_WATCH_RETRIGGER_HARD_LIMIT}) - mazem novo navrhnutu watch uroven, "
                  "padam spat na bezny interval.")
            watch_price = watch_direction = watch_price_2 = watch_direction_2 = watch_rationale = None

        cycle_log = CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session,
            config_snapshot=_config_snapshot(asset),
            direction=decision.get("direction"), confidence=decision.get("confidence"),
            stop_loss_price=decision.get("stop_loss_price"), take_profit_price=decision.get("take_profit_price"),
            reasoning=decision.get("reasoning"),
            web_search_log=web_search_log,
            **_source_usage_fields(asset, marketaux_news, social, coinmarketcal_events),
            key_assumptions=decision.get("key_assumptions"),
            watch_price=watch_price,
            watch_direction=watch_direction,
            watch_price_2=watch_price_2,
            watch_direction_2=watch_direction_2,
            watch_rationale=watch_rationale,
            confidence_threshold_note=decision.get("confidence_threshold_note"),
            data_issue=decision.get("data_issue"),
            reviewed_trade_id=closed_trade["trade_id"] if closed_trade else None,
            closed_trade_reflection=decision.get("closed_trade_reflection"),
            sl_tp_calibration_verdict=decision.get("sl_tp_calibration_verdict"),
            triggered_by_macro_event=macro_event,
            triggered_by_watch=True if watch_triggered else None,
            usage_input_tokens=usage.get("input_tokens"),
            usage_cache_write_tokens=usage.get("cache_write_tokens"),
            usage_cache_read_tokens=usage.get("cache_read_tokens"),
            usage_output_tokens=usage.get("output_tokens"),
            effort=usage.get("effort"),
        )

        if closed_trade and closed_trade.get("evaluation_only"):
            # 2026-08-18 (na ziadost pouzivatela) - SL/likvidacia review (viz
            # position_monitor._EVALUATION_ONLY_CLOSE_REASONS): Claude vyssie
            # dostal plny kontext a zhodnotil zatvorenie (closed_trade_reflection
            # + decision.reasoning ulozene v cycle_log vyssie, hodnota do
            # retrospektivy), ale jeho navrhnuty smer/confidence z TOHTO
            # konkretneho behu sa STRUKTURALNE (nie len promptovou instrukciou)
            # NIKDY nepouzije na otvorenie novej pozicie - presne preto, aby
            # okamzity re-entry po stop-oute nemohol byt revenge-trading.
            # Bot moze znova vstupit len pri najblizsom BEZNOM cykle.
            cycle_log.outcome = "evaluation_only"
            session.add(cycle_log)
            session.commit()
            print(f"[{name}] Post-close vyhodnotenie ulozene (SL/likvidacia) - "
                  "ziadny novy obchod sa z tohto behu neotvara.")
            return

        try:
            sized = risk_manager.validate_and_size(
                decision, has_open_position=False,
                live_price=live_price, market_meta=market_meta,
                min_confidence=asset["min_confidence"], sl_pct=asset["sl_pct"],
                tp_pct=asset["tp_pct"], cushion_multiple=asset["liquidation_cushion_multiple"],
                margin_usd=asset["margin_usd"],
            )
        except risk_manager.RejectedTrade as e:
            print(f"[{name}] Obchod zamietnuty risk managerom: {e}")
            cycle_log.outcome = "rejected"
            cycle_log.reject_reason = str(e)
            session.add(cycle_log)
            session.commit()
            return

        print(f"[{name}] Otvaram {sized['direction']} | leverage={sized['leverage']} "
              f"| size={sized['size']} | notional=${sized['notional_usd']} "
              f"| margin=${sized['margin_usd']} | SL={sized['stop_loss_price']} "
              f"| TP={sized['take_profit_price']} | confidence={sized['confidence']}")

        # Preflight kontrola zostatku (2026-08-08) - devat tickerov teraz zdiela
        # JEDNU penazenku bez ziadneho koordinacneho mechanizmu medzi nimi, takze
        # ak by viacero assetov chcelo otvorit v tom istom scheduler behu, tie
        # spracovane neskor mohli doteraz narazit na surovu "insufficient
        # balance" chybu priamo zo Strike (zachytenu az v except Exception nizsie,
        # nerozlisitelnu od inej API chyby). Radsej to zistime VOPRED a
        # zamietneme cisto (rovnaky vzor ako risk_manager.RejectedTrade) - zlyhanie
        # SAMOTNEJ kontroly (napr. /v2/account nedostupne) nesmie zablokovat
        # obchod, ktory by inak presiel - vtedy len pokracujeme a necháme Strike
        # ako finalny backstop.
        if not config.DRY_RUN:
            available_balance = None
            try:
                available_balance = float(strike_client.get_account()["available_balance"])
            except Exception as e:
                print(f"[{name}] Nepodarilo sa zistit dostupny zostatok pred otvorenim ({e}) "
                      "- pokracujem, Strike API bude finalny backstop.")
            if available_balance is not None and available_balance < sized["margin_usd"]:
                print(f"[{name}] Nedostatocny zostatok: potrebna marza ${sized['margin_usd']:.2f}, "
                      f"dostupnych len ${available_balance:.2f}. Obchod preskakujem.")
                cycle_log.outcome = "rejected"
                cycle_log.reject_reason = (
                    f"insufficient_balance: potrebna marza ${sized['margin_usd']:.2f}, "
                    f"dostupny zostatok ${available_balance:.2f}"
                )
                session.add(cycle_log)
                session.commit()
                return

        trade = Trade(
            symbol=symbol,
            direction=sized["direction"],
            confidence=sized["confidence"],
            reasoning=sized["reasoning"],
            entry_price=sized["entry_price"],
            stop_loss_price=sized["stop_loss_price"],
            take_profit_price=sized["take_profit_price"],
            leverage=sized["leverage"],
            size=sized["size"],
            notional_usd=sized["notional_usd"],
            margin_usd=sized["margin_usd"],
            opened_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=config.POSITION_MAX_HOURS),
            status="dry_run" if config.DRY_RUN else "open",
            dry_run=config.DRY_RUN,
        )

        if config.DRY_RUN:
            print(f"[{name}] DRY_RUN=true - obchod sa NEODOSLAL na Strike, iba zalogovany do DB.")
        else:
            try:
                result = strike_client.open_bracket_position(
                    direction=sized["direction"],
                    size=sized["size"],
                    leverage=sized["leverage"],
                    stop_loss_price=sized["stop_loss_price"],
                    take_profit_price=sized["take_profit_price"],
                    symbol=symbol,
                )
            except Exception as e:
                # Otvorenie na Strike zlyhalo uplne (API chyba, nedostatok prostriedkov...) -
                # ziadna pozicia nevznikla, ale nesmieme stratit stopu po tomto pokuse.
                print(f"[{name}] Otvorenie pozicie na Strike zlyhalo: {e}")
                cycle_log.outcome = "error"
                cycle_log.reject_reason = f"open_position_failed: {e}"
                session.add(cycle_log)
                session.commit()
                return

            print(f"[{name}] Strike odpoved: {result}")
            trade.strategy_id = result.get("strategy_id")

            # Bezpecnostna kontrola: ak SL alebo TP noha bracket objednavky zlyhala
            # pripojit sa (Strike ju z nejakeho dovodu odmietol), pozicia by bola
            # nechranena az do dalsieho position_monitor cyklu (az 10 min). Radsej
            # ju hned teraz nudzovo zatvorime, nez by cakala nechranena na burze.
            if not result.get("sl_client_order_id") or not result.get("tp_client_order_id"):
                print(
                    f"[{name}] KRITICKE: chyba sl_client_order_id alebo "
                    "tp_client_order_id v odpovedi - pozicia je NECHRANENA. "
                    "Nudzovo zatvaram okamzite."
                )
                try:
                    strike_client.cancel_all_orders(trade.symbol)
                    strike_client.close_position_market(
                        sized["direction"], sized["size"], trade.symbol
                    )
                    trade.status = "closed_by_safety"
                    trade.close_reason = "missing_sl_or_tp_leg_after_open"
                    trade.closed_at = datetime.now(timezone.utc)
                except Exception as e:
                    print(f"[{name}] CHYBA pri nudzovom zatvoreni: {e}")
                    trade.close_reason = f"missing_sl_or_tp_leg_AND_safety_close_failed: {e}"

        session.add(trade)
        session.flush()  # priradi trade.id pred zapisom do cycle_log
        cycle_log.outcome = "opened"
        cycle_log.trade_id = trade.id
        session.add(cycle_log)
        session.commit()

        # Notifikacia AZ PO uspesnom commite - len pre skutocne otvorenu pozicii
        # (nie dry_run, nie closed_by_safety z nudzoveho zatvorenia vyssie).
        # Zlyhanie sa nikdy nesmie prejavit navonok (viz discord_client.py).
        if trade.status == "open":
            discord_client.notify_trade_opened(asset, sized)
    finally:
        session.close()
        lock.release()


def run_triggered_check(asset: dict, closed_trade: dict | None = None,
                         macro_event: str | None = None, watch_triggered: bool = False) -> None:
    """Mimoriadny cyklus LEN pre jeden asset, mimo bezneho zdielaneho hodinoveho
    tiku - vola ho watch_monitor.py (watch_price/watch_direction podmienka
    splnena ALEBO macro_event - viz nizsie) alebo position_monitor.py
    (post-close review - viz closed_trade nizsie). Makro data (cross-market/
    session/BTC proxy) sa fetchuju cerstvo - yfinance je zdarma, takze jediny
    realny naklad tu je samotne Claude volanie v run_cycle_for_asset - presne
    to je zmysel: platit za mimoriadnu analyzu len ked sa sledovana podmienka
    NAOZAJ splni, nie podla casu.

    closed_trade: viz run_cycle_for_asset - ak nastavene, ide o post-close
    review (nie watch trigger).
    macro_event: viz run_cycle_for_asset - ak nastavene, ide o beh vyvolany
    prave zverejnenou makro udalostou (FOMC/CPI/NFP)."""
    name = asset["name"]
    if macro_event:
        trigger_label = f"makro udalost {macro_event}"
    elif closed_trade and closed_trade.get("evaluation_only"):
        trigger_label = "post-close review (len vyhodnotenie)"
    elif closed_trade:
        trigger_label = "post-close review"
    else:
        trigger_label = "watch trigger"
    print(f"[trade_cycle] [{name}] mimoriadny beh ({trigger_label})")
    try:
        cross_market = market_data.get_cross_market_snapshot()
        market_session = market_data.get_session_snapshot()
    except Exception as e:
        print(f"[trade_cycle] [{name}] makro fetch pre mimoriadny beh zlyhal, preskakujem: {e}")
        return

    btc_proxy = None
    if asset.get("needs_btc_proxy"):
        try:
            btc_proxy = market_data.get_btc_proxy_snapshot()
        except Exception as e:
            print(f"[trade_cycle] [{name}] BTC proxy fetch zlyhal (pokracujem bez neho): {e}")

    fred_macro = None
    try:
        fred_macro = fred_client.get_macro_snapshot()
    except Exception as e:
        print(f"[trade_cycle] [{name}] FRED fetch zlyhal (pokracujem bez neho): {e}")

    run_cycle_for_asset(asset, cross_market, market_session, btc_proxy, fred_macro,
                         skip_due_check=True, closed_trade=closed_trade, macro_event=macro_event,
                         watch_triggered=watch_triggered)


# Symboly s momentalne beziacim mimoriadnym (background-thread) behom - viz
# dispatch_triggered_check() nizsie.
_triggered_check_lock = threading.Lock()
_triggered_check_in_flight: set[str] = set()

# Strop na POCET SUCASNE BEZIACICH Claude volani z dispatch_triggered_check
# (2026-08-19, crash-scenario audit) - bez neho by hromadne zatvorenie viacerych
# pozicii naraz (napr. cely trh padne, SL/TP sa vykonaju pre viacero tickerov v
# tom istom position_monitor tiku) spustilo NEOBMEDZENY pocet suecasnych
# vlakien, kazde s vlastnym Claude+web_search volanim aj vlastnou DB session
# drzanou po celu jeho dlzku (1-3+ min) - riziko narazenia na Anthropic rate
# limit aj vycerpania DB connection poolu naraz. Vlakno sa VZDY spusti hned
# (in-flight bookkeeping ostava presne - symbol je "in flight" aj pocas
# cakania), len samotna praca caka na volny slot - nadbytocne pozadovky sa
# spracuju postupne, ziadna sa nestrati (na rozdiel od explicitneho zamietnutia).
_DISPATCH_CONCURRENCY_LIMIT = 5
_dispatch_semaphore = threading.Semaphore(_DISPATCH_CONCURRENCY_LIMIT)


def is_triggered_check_in_flight(symbol: str) -> bool:
    """Umoznuje volajucemu (watch_monitor.py) zistit vopred, ci by dispatch_triggered_check()
    pre tento symbol aj tak len tichy zahodil beh (viz jej docstring nizsie) -
    2026-08-16 produkcny nalez: watch_monitor predtym pripisal TriggeredWatch
    (spotreboval hodinovy rozpocet WATCH_TRIGGER_MAX_PER_HOUR) EST PRED volanim
    dispatch_triggered_check, takze rychly sled tikov pocas prudkeho pohybu
    (kedy in-flight guard nizsie vzdy zahodi 2. a 3. pokus) vedel vycerpat cely
    hodinovy rozpocet na duplicity namiesto skutocnych novych analyz."""
    with _triggered_check_lock:
        return symbol in _triggered_check_in_flight


def dispatch_triggered_check(asset: dict, **kwargs) -> None:
    """Ako run_triggered_check() vyssie, ale NA POZADI (samostatny thread) -
    pouzivaju watch_monitor.py a position_monitor.py namiesto priameho volania.

    Dovod (2026-08-15, produkcny nalez): run_triggered_check zvykne trvat 1-3
    min (Claude + web_search, obzvlast pri effort=xhigh), a oba volajuce joby
    (watch_monitor.check_watch_triggers, position_monitor.check_open_trades)
    ho predtym volali PRIAMO/blokujuco - kym cakali na Claude odpoved pre JEDEN
    asset, kontrola OSTATNYCH tickerov v tom istom behu stala, a APScheduler
    (max_instances=1 default) preskocil aj CELY dalsi 1-min tik ("maximum
    number of running instances reached"). To znamena, ze na 2-3 min sa
    prestali kontrolovat VSETKY ostatne watch podmienky / otvorene pozicie,
    nielen ta jedna, co trigger vyvolala. Dispatch na pozadie drzi kazdy
    scheduler tik kratky (len lahke DB/HTTP volania), takze sa uz nikdy
    nepreskakuje. run_triggered_check() otvara VLASTNU nezavislu DB session
    (viz jej docstring), takze je bezpecne volat z ineho threadu.

    Per-symbol "in-flight" poistka (2026-08-16, produkcny nalez - ZEC dva
    mimoriadne cykly 38s od seba s protichodnym zaverom): kedze beh je na
    pozadi, watch_monitor.check_watch_triggers() (kazdu WATCH_INTERVAL_MINUTES=1
    min) sa moze spustit znova skor, nez sa predchadzajuci beh PRE TEN ISTY
    SYMBOL vobec zapise do DB (Claude+web_search bezne trva dlhsie nez 1 min) -
    poller vtedy este stale vidi STARU watch uroven a spusti duplicitny beh
    nad prakticky rovnakymi datami. _triggered_check_in_flight nizsie preto
    zabrani druhemu (paralelnemu) triggeru pre rovnaky symbol, kym prvy este
    nedobehol - hodinovy strop (WATCH_TRIGGER_MAX_PER_HOUR) tento pripad rieši
    len castocne (obmedzi pocet, nezabrani subehu)."""
    name = asset["name"]
    symbol = asset["strike_symbol"]

    with _triggered_check_lock:
        if symbol in _triggered_check_in_flight:
            print(f"[trade_cycle] [{name}] mimoriadny beh uz prebieha (predchadzajuci "
                  "trigger este nedobehol) - preskakujem duplicitny beh.")
            return
        _triggered_check_in_flight.add(symbol)

    def _run():
        with _dispatch_semaphore:
            try:
                run_triggered_check(asset, **kwargs)
            except Exception as e:
                print(f"[trade_cycle] [{name}] mimoriadny beh na pozadi zlyhal: {e}")
            finally:
                with _triggered_check_lock:
                    _triggered_check_in_flight.discard(symbol)

    threading.Thread(target=_run, daemon=True, name=f"triggered-{name}").start()


def _mark_disabled_assets() -> None:
    """Nulovy-naklad (ziadne Claude/web_search volanie) zapis CycleLog s
    outcome='disabled' pre kazdy asset, ktory je momentalne VYPNUTY
    (assets.py enabled=False, napr. NVDA od 2026-07-31 - pozastavene kvoli
    cost-optimalizacii). Zapise sa LEN RAZ pri prechode (kontrola voci
    poslednemu zaznamu), nie na kazdy tick - inak by to zbytocne zaplavovalo
    'Historia signalov'. Sluzi len na to, aby monitor-web vedel zobrazit
    'Pozastavene' namiesto zastaraneho (uz neplatneho) posledneho stavu."""
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            if asset["enabled"]:
                continue
            symbol = asset["strike_symbol"]
            last_log = (
                session.query(CycleLog)
                .filter(CycleLog.symbol == symbol)
                .order_by(CycleLog.created_at.desc())
                .first()
            )
            if last_log is not None and last_log.outcome == "disabled":
                continue
            session.add(CycleLog(
                symbol=symbol,
                config_snapshot=_config_snapshot(asset),
                outcome="disabled",
            ))
        session.commit()
    finally:
        session.close()


def run_all_cycles() -> None:
    """Vstupny bod scheduleru (viz main.py). Fetchne zdielane makro data RAZ
    (cross-market/session + BTC proxy ak treba) a potom prejde kazdy aktivny
    asset z assets.enabled_assets() nezavisle."""
    print(f"\n=== [trade_cycle] {datetime.now(timezone.utc).isoformat()} ===")
    try:
        _mark_disabled_assets()
    except Exception as e:
        print(f"[trade_cycle] _mark_disabled_assets zlyhal (neblokujuce): {e}")

    active = assets.enabled_assets()
    if not active:
        print("[trade_cycle] Ziadny aktivny asset (skontroluj ENABLE_NVDA/ENABLE_ADA).")
        return
    print(f"[trade_cycle] Aktivne assety: {[a['name'] for a in active]}")

    try:
        cross_market = market_data.get_cross_market_snapshot()
        market_session = market_data.get_session_snapshot()
        print(f"[trade_cycle] Zdielany cross-market: {cross_market}")
        print(f"[trade_cycle] Zdielany session: {market_session}")
    except Exception as e:
        # zdielany fetch je spolocny vstup pre vsetky assety - ak zlyha, ziaden
        # asset nema na com rozhodovat, radsej preskocime cely tick.
        print(f"[trade_cycle] Zdielany makro fetch zlyhal, preskakujem CELY cyklus: {e}")
        return

    btc_proxy = None
    if any(a.get("needs_btc_proxy") for a in active):
        try:
            btc_proxy = market_data.get_btc_proxy_snapshot()
            print(f"[trade_cycle] BTC proxy (krypto-makro pre ADA/NIGHT): {btc_proxy}")
        except Exception as e:
            print(f"[trade_cycle] BTC proxy fetch zlyhal (pokracujem bez neho): {e}")

    # FRED makro snapshot (CPI/Core CPI/Fed funds rate) - zdielane pre vsetky
    # assety rovnako ako cross_market/session, volitelne (viz fred_client.py).
    fred_macro = None
    try:
        fred_macro = fred_client.get_macro_snapshot()
        print(f"[trade_cycle] FRED makro: {fred_macro}")
    except Exception as e:
        print(f"[trade_cycle] FRED fetch zlyhal (pokracujem bez neho): {e}")

    for asset in active:
        try:
            run_cycle_for_asset(
                asset, cross_market, market_session,
                btc_proxy if asset.get("needs_btc_proxy") else None,
                fred_macro,
            )
        except Exception as e:
            # jeden asset nesmie zhodit ostatne v tom istom cykle
            print(f"[trade_cycle] [{asset['name']}] neocakavana chyba, pokracujem dalsim assetom: {e}")


if __name__ == "__main__":
    run_all_cycles()
