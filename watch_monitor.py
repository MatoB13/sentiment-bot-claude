"""
Lahky, NEPLATENY poller pre "watch" podmienky.

Ked je Claudeho rozhodnutie "none", ale vidi konkretnu cenovu uroven cakajucu
na potvrdenie (napr. retest), ulozi si ju do CycleLog.watch_price/watch_direction
(viz claude_analyst.py) - volitelne aj DRUHY, nezavisly par
watch_price_2/watch_direction_2 pre genuinne obojstranne neisty/range-bound
setup (nad X = long, pod Y = short). Tento modul kazdych WATCH_INTERVAL_MINUTES
(samostatny, tesnejsi interval nez MONITOR_INTERVAL_MINUTES - viz main.py)
skontroluje LEN live cenu zo Strike (ziadne Claude/web_search volanie, teda
nulovy naklad) voci najnovsiemu CycleLog zaznamu pre kazdy asset - ak sa
splni PRVY ALEBO DRUHY par, spusti mimoriadny (uz platny) Claude cyklus LEN
pre tento jeden asset cez trade_cycle.dispatch_triggered_check() - NA POZADI
(viz jej docstring), aby pomaly Claude beh pre jeden asset neblokoval
kontrolu ostatnych tickerov v tom istom tiku.

Preco staci pozerat len "najnovsi" zaznam: novy CycleLog z mimoriadneho (alebo
z beznej hodinovej) analyzy sa stane najnovsim zaznamom pre dany symbol, cim
stary watch prirodzene "zanikne" - poller uz nikdy nenajde stary riadok, takze
netreba samostatny "consumed" flag ani expiraciu.

Bezpecnostna poistka (2026-08-08, viz TriggeredWatch v db.py +
config.WATCH_TRIGGER_MAX_PER_HOUR): bez nej by mohol watch-trigger jedneho
assetu spustat mimoriadne cykly neobmedzene casto, ak by kazdy dalsi cyklus
znova nastavil (aj mierne inu) blizku watch uroven - max. N (default 3) za
poslednu hodinu, POCITANE OSOBITNE PRE KAZDY ASSET (na rozdiel od
MACRO_EVENT_MAX_TRIGGERS_PER_HOUR nizsie, ktory je jeden zdielany rozpocet
naprieč vsetkymi assetmi, kedze makro udalosti su casto "vsetky assety" burst).
"""
from datetime import datetime, timedelta, timezone

import assets
import config
import macro_calendar
import strike_client
import trade_cycle
from db import CycleLog, FlaggedMacroEvent, Trade, TriggeredMacroEvent, TriggeredWatch, get_session


def _is_triggered(live_price: float, watch_price: float, watch_direction: str) -> bool:
    if watch_direction == "above":
        return live_price >= watch_price
    if watch_direction == "below":
        return live_price <= watch_price
    return False


def _check_manual_close_requests(session) -> None:
    """Kill-switch: monitor-web "Zavriet" tlacidlo zapise Trade.manual_close_requested_at
    namiesto priameho volania Strike (API kluce zostavaju LEN tu vo worker-i,
    nikdy vo verejne dostupnom Vercel/monitor-web). Tento poller (1x/min) taketo
    ziadosti najde a hned zatvori - rovnaky mechanizmus (cancel_all_orders +
    close_position_market) ako existujuci POSITION_MAX_HOURS force-close v
    position_monitor.py. Exact PnL fill lookup nerobi sam - necha to na
    position_monitor._backfill_missing_exact_data (uz existujuci retry na
    kazdy zatvoreny trade s pnl_usd IS NULL), aby sa fill-lookup kod nezduploval."""
    pending = session.query(Trade).filter(
        Trade.status == "open", Trade.manual_close_requested_at.isnot(None),
    ).all()
    if not pending:
        return

    try:
        live_positions = strike_client.get_positions()
    except Exception as e:
        print(f"[watch_monitor] Kill-switch: nepodarilo sa nacitat /v2/positions: {e}")
        return
    live_by_symbol = {p.get("symbol"): p for p in live_positions}
    now = datetime.now(timezone.utc)

    for trade in pending:
        live = live_by_symbol.get(trade.symbol)
        if live is None:
            # Uz nie je otvorena na burze (napr. medzitym trafila SL/TP) - len
            # oznacime stav, presne PnL doplni position_monitor ako zvycajne.
            print(f"[watch_monitor] Kill-switch Trade {trade.id} [{trade.symbol}]: "
                  "uz nie je otvorena na burze, len oznacujem stav.")
            trade.status = "closed_by_exchange"
            trade.closed_at = now
            continue

        print(f"[watch_monitor] Kill-switch Trade {trade.id} [{trade.symbol}]: "
              "rucna ziadost o zatvorenie - zatvaram.")
        try:
            strike_client.cancel_all_orders(trade.symbol)
            strike_client.close_position_market(trade.direction, float(live["size"]), trade.symbol)
        except Exception as e:
            print(f"[watch_monitor] Kill-switch Trade {trade.id} [{trade.symbol}]: "
                  f"zatvorenie zlyhalo, skusim znova na dalsom tiku: {e}")
            continue
        trade.status = "closed_by_user"
        trade.closed_at = now
        trade.close_reason = "manual_kill_switch"
    session.commit()


def _pending_events_with_scope(session, now) -> list[dict]:
    """Zluci DVA zdroje makro udalosti s PEVNE ZNAMYM casom vopred do jednotneho
    zoznamu {name, datetime_utc, symbol, key}:
    1. macro_calendar.MACRO_EVENTS (FOMC/CPI/NFP) - rucne udrziavane, overene z
       oficialnych zdrojov, symbol=None = VSETKY aktivne assety (vsetky tri su
       uz sucastou Event Risk Gate pravidiel pre vsetkych 9 tickerov).
    2. FlaggedMacroEvent (viz trade_cycle._save_flagged_macro_event) - Claude
       ich priebezne SAM zaznaci pocas beznej analyzy alebo denneho
       retrospektivneho lookaheadu cez web_search. flagged_by_symbol=None
       (scope="all_assets" pri zaznacovani, napr. FOMC/CPI/NFP objavene
       Claudom) = VSETKY aktivne assety, rovnaky mechanizmus ako
       macro_calendar.py. flagged_by_symbol=konkretny asset (scope="this_asset",
       napr. OPEC+ pre WTI, bezpecnostny deadline pre NIGHT) = LEN preň."""
    out = []
    for e in macro_calendar.get_pending_events(now):
        out.append({
            "name": e["name"], "datetime_utc": e["datetime_utc"], "symbol": None,
            "key": macro_calendar.event_key(e),
        })

    lookback = now - timedelta(minutes=30)
    for row in session.query(FlaggedMacroEvent).all():
        dt = row.datetime_utc if row.datetime_utc.tzinfo else row.datetime_utc.replace(tzinfo=timezone.utc)
        if lookback <= dt <= now:
            key = (f"{row.name}_{dt.date().isoformat()}" if row.flagged_by_symbol is None
                   else f"{row.name}_{dt.date().isoformat()}_{row.flagged_by_symbol}")
            out.append({"name": row.name, "datetime_utc": dt, "symbol": row.flagged_by_symbol, "key": key})
    return out


def _check_macro_events(session) -> None:
    """Makro udalosti s PEVNE ZNAMYM casom vopred, na rozdiel od cenoveho watch
    vyssie NEPOTREBUJU cakat na nejaku podmienku - proste nastanu v znamy cas
    (viz _pending_events_with_scope pre oba zdroje). Spusti mimoriadny cyklus
    pre VSETKY aktivne assety (FOMC/CPI/NFP) alebo LEN pre asset, ktory
    udalost sam zaznacil (Claudom pridane udalosti), max
    config.MACRO_EVENT_MAX_TRIGGERS_PER_HOUR udalosti za hodinu (bezpecnostna
    poistka pri nahodnom zhluku) - zvysok sa spracuje na buducich tikoch, kym
    sa hodinove okno neposunie. "Pauza po poslednom" nepotrebuje vlastnu
    logiku - _is_due() v trade_cycle.py uz prirodzene zablokuje dalsi bezny
    tik daneho assetu, kym neuplynie jeho vlastny interval."""
    now = datetime.now(timezone.utc)
    pending = _pending_events_with_scope(session, now)
    if not pending:
        return

    already_triggered = {row.event_key for row in session.query(TriggeredMacroEvent).all()}
    due = [e for e in pending if e["key"] not in already_triggered]
    if not due:
        return

    hour_ago = now - timedelta(hours=1)
    triggered_this_hour = session.query(TriggeredMacroEvent).filter(
        TriggeredMacroEvent.triggered_at >= hour_ago,
    ).count()
    budget = config.MACRO_EVENT_MAX_TRIGGERS_PER_HOUR - triggered_this_hour
    if budget <= 0:
        print(f"[watch_monitor] Makro udalosti cakaju ({[e['key'] for e in due]}), "
              f"ale hodinovy limit ({config.MACRO_EVENT_MAX_TRIGGERS_PER_HOUR}) je vycerpany - skusim dalsi tik.")
        return

    due.sort(key=lambda e: e["datetime_utc"])
    for event in due[:budget]:
        key = event["key"]
        if event["symbol"] is None:
            target_assets = assets.enabled_assets()
            scope_label = "vsetky aktivne assety"
        else:
            target_assets = [a for a in assets.enabled_assets() if a["strike_symbol"] == event["symbol"]]
            scope_label = event["symbol"]
        print(f"[watch_monitor] Makro udalost {key} - spustam mimoriadne cykly pre {scope_label}.")
        # Zapisane HNED (pred behom cyklov), aby sa pri padnutom procese
        # uprostred slucky nizsie nespustala tato udalost znova od zaciatku.
        session.add(TriggeredMacroEvent(event_key=key))
        session.commit()
        for asset in target_assets:
            trade_cycle.dispatch_triggered_check(asset, macro_event=event["name"])


def check_watch_triggers() -> None:
    print(f"\n=== [watch_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        _check_manual_close_requests(session)
        _check_macro_events(session)

        # Jeden zdielany /v2/markets request pre vsetky assety naraz (rovnaky
        # vzor ako position_monitor.check_open_trades() pre /v2/positions) -
        # get_market(symbol) by inak interne volal cely get_markets() znova
        # pre kazdy sledovany ticker samostatne (zbytocne opakovane rovnake
        # bulk volanie, len s inym lokalnym filtrom).
        try:
            markets_by_symbol = {m.get("symbol"): m for m in strike_client.get_markets()}
        except Exception as e:
            print(f"[watch_monitor] nepodarilo sa nacitat /v2/markets: {e}")
            return

        for asset in assets.enabled_assets():
            symbol = asset["strike_symbol"]
            name = asset["name"]

            open_trade = session.query(Trade).filter(
                Trade.symbol == symbol, Trade.status == "open",
            ).first()
            if open_trade:
                continue  # uz je otvorena pozicia - watch uz nie je relevantny

            last_log = (
                session.query(CycleLog)
                .filter(CycleLog.symbol == symbol)
                .order_by(CycleLog.created_at.desc())
                .first()
            )
            has_pair_1 = last_log and last_log.watch_price is not None and last_log.watch_direction
            has_pair_2 = last_log and last_log.watch_price_2 is not None and last_log.watch_direction_2
            if not has_pair_1 and not has_pair_2:
                continue

            market = markets_by_symbol.get(symbol)
            if market is None:
                print(f"[watch_monitor] [{name}] symbol {symbol} sa nenasiel v /v2/markets.")
                continue
            live_price = float(market["mark_price"])

            # Obojstranny watch (viz claude_analyst.py watch_price_2/watch_direction_2) -
            # ktorykolvek z dvoch nezavislych parov staci na spustenie; Claude si
            # situaciu aj tak prehodnoti nanovo v mimoriadnom cykle, nemechanicky
            # nevykonava vopred urceny smer.
            triggered_pair = None
            if has_pair_1 and _is_triggered(live_price, last_log.watch_price, last_log.watch_direction):
                triggered_pair = (last_log.watch_price, last_log.watch_direction)
            elif has_pair_2 and _is_triggered(live_price, last_log.watch_price_2, last_log.watch_direction_2):
                triggered_pair = (last_log.watch_price_2, last_log.watch_direction_2)

            if triggered_pair is None:
                continue
            watch_price, watch_direction = triggered_pair

            # 2026-08-16 produkcny nalez: ak pre tento symbol uz mimoriadny beh
            # bezi (predchadzajuci tik ho este nestihol dokoncit), dispatch_triggered_check
            # nizsie by ho aj tak len tichy zahodil (viz jej in-flight guard) -
            # kontrola TU, PRED pripisanim do TriggeredWatch, zabrani zbytocnemu
            # spotrebovaniu hodinoveho rozpoctu na beh, ktory sa vobec nespusti.
            # Bez tohto by rychly sled tikov pocas prudkeho pohybu (kazdy dalsi
            # tik vidi rovnaku stalu watch uroven, kym prvy beh este nedobehol)
            # vedel vycerpat cely WATCH_TRIGGER_MAX_PER_HOUR na duplicity.
            if trade_cycle.is_triggered_check_in_flight(symbol):
                print(f"[watch_monitor] [{name}] watch podmienka splnena, ale mimoriadny beh "
                      "uz prebieha - nespotrebuvam hodinovy rozpocet, skusim dalsi tik.")
                continue

            hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            triggered_this_hour = session.query(TriggeredWatch).filter(
                TriggeredWatch.symbol == symbol, TriggeredWatch.triggered_at >= hour_ago,
            ).count()
            if triggered_this_hour >= config.WATCH_TRIGGER_MAX_PER_HOUR:
                print(f"[watch_monitor] [{name}] watch podmienka splnena, ale hodinovy limit "
                      f"({config.WATCH_TRIGGER_MAX_PER_HOUR}) je pre tento asset vycerpany - "
                      "skusim dalsi tik.")
                continue

            print(
                f"[watch_monitor] [{name}] watch podmienka splnena "
                f"(live={live_price}, watch={watch_direction} {watch_price}) "
                "- spustam mimoriadny cyklus."
            )
            # Zapisane HNED (pred behom cyklu), aby padnuty proces uprostred
            # Claude volania nizsie nespotreboval rozpocet bez ozajstneho zapisu.
            session.add(TriggeredWatch(symbol=symbol))
            session.commit()
            trade_cycle.dispatch_triggered_check(asset)
    finally:
        session.close()


if __name__ == "__main__":
    check_watch_triggers()
