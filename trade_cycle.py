"""Analyticky/obchodny cyklus pre vsetky aktivne assety (NAS100/NVDA/ADA/GOLD/WTI/NIGHT).

Jeden scheduler tick (viz main.py) = jeden vstup do run_all_cycles(): zdielany
makro fetch (cross-market/session, pripadne BTC proxy) sa spravi PRESNE RAZ a
potom sa pouzije pre kazdy aktivny asset z assets.py nezavisle - kazdy ma
vlastnu poziciu, vlastny risk (SL/TP%, leverage, margin, min_confidence) a
vlastne Claude rozhodnutie. Zlyhanie jedneho assetu nesmie zhodit ostatne."""
from datetime import datetime, timedelta, timezone

import assets
import claude_analyst
import config
import eia_client
import fred_client
import market_data
import marketaux_client
import retrospective
import risk_manager
import social_sentiment
import strike_client
from db import CycleLog, DailyRetrospective, RollingRetrospective, Trade, get_session


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
    zbytocny naklad."""
    if now.weekday() >= 5:  # sobota=5, nedela=6
        return asset["weekend_interval_hours"]
    if config.TRADING_HOURS_START_UTC <= now.hour < config.TRADING_HOURS_END_UTC:
        return asset["trade_interval_hours"]
    return asset["off_hours_interval_hours"]


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
        "monitor_interval_minutes": config.MONITOR_INTERVAL_MINUTES,
        "watch_interval_minutes": config.WATCH_INTERVAL_MINUTES,
        "position_max_hours": config.POSITION_MAX_HOURS,
        "min_confidence": asset["min_confidence"],
        "margin_usd": asset["margin_usd"],
        "leverage": asset["leverage"],
        "default_sl_pct": asset["sl_pct"],
        "default_tp_pct": asset["tp_pct"],
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


def _run_position_health_check(asset: dict, open_trade: Trade, cross_market: dict, market_session: dict,
                                btc_proxy: dict | None, fred_macro: dict | None, session) -> None:
    """Ked uz je otvorena pozicia, namiesto predosleho ticheho 'skipped' zaznamu
    (2026-08 spatna vazba pouzivatela: chcel Claudeho priebezny nazor na
    otvorenu poziciu, nie len zahltenu historiu signalov bez obsahu) spustime
    'position health check' - Claude posudi, ci povodne kluc. predpoklady este
    platia a ci by mal pouzivatel zvazit rucne zatvorenie (kill-switch tlacidlo
    v monitor-web). Bot SAM poziciu nezatvara ani nemeni SL/TP - je to len
    opinion pre cloveka. Bezi na rovnakom _is_due() intervale ako bezny
    otvaraci cyklus (ziadny samostatny interval navyse)."""
    name = asset["name"]
    symbol = asset["strike_symbol"]
    print(f"[{name}] Otvorena pozicia (trade_id={open_trade.id}) - position health check namiesto skipu.")

    try:
        market_meta = strike_client.get_market(symbol)
        live_price = float(market_meta["mark_price"])
        ta = market_data.get_market_snapshot(asset, session)
        social = social_sentiment.fetch_recent_posts(name)
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
    }

    prev_log = (
        session.query(CycleLog)
        .filter(CycleLog.symbol == symbol, CycleLog.key_assumptions.isnot(None))
        .order_by(CycleLog.created_at.desc())
        .first()
    )
    prev_assumptions = prev_log.key_assumptions if prev_log else None
    prev_cycle_time = prev_log.created_at if prev_log else None

    try:
        # Len aktualne priebezne zhrnutie (nie generovanie noveho denneho
        # zaznamu) - daily_reflection/summary_reflection su polia specificke
        # pre DECISION_TOOL, position health tool ich nema. Ak vcerajsok
        # este nebol zapracovany, spracuje sa az na buducom BEZNOM cykle
        # (rovnako ako predtym, ked sa pri otvorenej pozicii vobec nic
        # nefetchovalo) - nie regresia, len nerozsirujeme scope tejto zmeny.
        retrospective_reflection, _, _ = _get_retrospective_context(asset, session)
    except Exception as e:
        print(f"[{name}] Vypocet retrospektivy zlyhal (pokracujem bez nej): {e}")
        session.rollback()
        retrospective_reflection = None

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

    try:
        health, web_search_log = claude_analyst.analyze_position_health(
            asset, open_position, ta, cross_market, market_session, social, btc_proxy,
            prev_assumptions, prev_cycle_time, retrospective_reflection,
            fred_macro, eia_data, marketaux_news,
        )
    except Exception as e:
        print(f"[{name}] Position health check zlyhal: {e}")
        session.add(CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session, config_snapshot=_config_snapshot(asset),
            outcome="error", reject_reason=f"health_check_failed: {e}", trade_id=open_trade.id,
        ))
        session.commit()
        return

    print(f"[{name}] Position health check: {health}")
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
        web_search_log=web_search_log, health_recommendation=health.get("recommendation"),
        health_expected_direction=health.get("expected_direction"),
        trade_id=open_trade.id,
    ))
    session.commit()


def run_cycle_for_asset(asset: dict, cross_market: dict, market_session: dict,
                         btc_proxy: dict | None, fred_macro: dict | None = None,
                         skip_due_check: bool = False,
                         closed_trade: dict | None = None) -> None:
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
    entry_price/exit_price/hours_held/pnl_usd/close_reason o PRAVE zatvorenej
    pozicii, ktory sa vlozi do promptu (viz claude_analyst) a Claude popri
    beznom otvaracom rozhodnuti zaroven zhodnoti, ci bolo zatvorenie spravne
    timeovane (closed_trade_reflection)."""
    name = asset["name"]
    symbol = asset["strike_symbol"]
    print(f"\n--- [{name}] ---")
    session = get_session()
    try:
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
                                        fred_macro, session)
            return

        try:
            market_meta = strike_client.get_market(symbol)
            live_price = float(market_meta["mark_price"])

            ta = market_data.get_market_snapshot(asset, session)
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

        try:
            decision, web_search_log = claude_analyst.analyze(
                asset, ta, cross_market, market_session, social, btc_proxy,
                prev_assumptions, prev_cycle_time,
                retrospective_reflection, new_stats_text,
                fred_macro, eia_data, marketaux_news,
                confidence_streak, closed_trade,
            )
        except Exception as e:
            print(f"[{name}] Claude analyza zlyhala, preskakujem cyklus: {e}")
            session.add(CycleLog(
                symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
                session_data=market_session,
                config_snapshot=_config_snapshot(asset),
                outcome="error", reject_reason=str(e),
            ))
            session.commit()
            return
        print(f"[{name}] Claude rozhodnutie: {decision}")
        print(f"[{name}] Web search log: {web_search_log}")

        if pending_stats:
            for_date = pending_stats["for_date"]
            # Dve NEZAVISLE izolovane transakcie - zlyhanie jednej nesmie
            # zobrat so sebou druhu ani nizsie cycle_log/trade zapisy. Duplicity
            # osetrene explicitne (existence check), lebo based_through_date
            # (gate v _get_retrospective_context) sa posunie az pri uspesnom
            # summary_reflection - ak ten chyba/zlyha, tento cyklus sa moze na
            # dalsom tiku zopakovat a bez tejto kontroly by vznikol duplicitny
            # DailyRetrospective riadok za rovnaky den.
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

        cycle_log = CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session,
            config_snapshot=_config_snapshot(asset),
            direction=decision.get("direction"), confidence=decision.get("confidence"),
            stop_loss_price=decision.get("stop_loss_price"), take_profit_price=decision.get("take_profit_price"),
            reasoning=decision.get("reasoning"),
            web_search_log=web_search_log,
            key_assumptions=decision.get("key_assumptions"),
            watch_price=decision.get("watch_price"),
            watch_direction=decision.get("watch_direction"),
            data_issue=decision.get("data_issue"),
            reviewed_trade_id=closed_trade["trade_id"] if closed_trade else None,
            closed_trade_reflection=decision.get("closed_trade_reflection"),
        )

        try:
            sized = risk_manager.validate_and_size(
                decision, has_open_position=False,
                live_price=live_price, market_meta=market_meta,
                min_confidence=asset["min_confidence"], sl_pct=asset["sl_pct"],
                tp_pct=asset["tp_pct"], leverage=asset["leverage"], margin_usd=asset["margin_usd"],
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
    finally:
        session.close()


def run_triggered_check(asset: dict, closed_trade: dict | None = None) -> None:
    """Mimoriadny cyklus LEN pre jeden asset, mimo bezneho zdielaneho hodinoveho
    tiku - vola ho watch_monitor.py (watch_price/watch_direction podmienka
    splnena) alebo position_monitor.py (post-close review - viz closed_trade
    nizsie). Makro data (cross-market/session/BTC proxy) sa fetchuju cerstvo -
    yfinance je zdarma, takze jediny realny naklad tu je samotne Claude
    volanie v run_cycle_for_asset - presne to je zmysel: platit za mimoriadnu
    analyzu len ked sa sledovana podmienka NAOZAJ splni, nie podla casu.

    closed_trade: viz run_cycle_for_asset - ak nastavene, ide o post-close
    review (nie watch trigger)."""
    name = asset["name"]
    print(f"[trade_cycle] [{name}] mimoriadny beh "
          f"({'post-close review' if closed_trade else 'watch trigger'})")
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
                         skip_due_check=True, closed_trade=closed_trade)


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
