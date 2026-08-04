"""
Lahky, NEPLATENY poller pre "watch" podmienky.

Ked je Claudeho rozhodnutie "none", ale vidi konkretnu cenovu uroven cakajucu
na potvrdenie (napr. retest), ulozi si ju do CycleLog.watch_price/watch_direction
(viz claude_analyst.py). Tento modul kazdych WATCH_INTERVAL_MINUTES (samostatny,
tesnejsi interval nez MONITOR_INTERVAL_MINUTES - viz main.py) skontroluje
LEN live cenu zo Strike (ziadne Claude/web_search volanie, teda nulovy naklad) voci
najnovsiemu CycleLog zaznamu pre kazdy asset - ak sa podmienka splni, spusti
mimoriadny (uz platny) Claude cyklus LEN pre tento jeden asset cez
trade_cycle.run_triggered_check().

Preco staci pozerat len "najnovsi" zaznam: novy CycleLog z mimoriadneho (alebo
z beznej hodinovej) analyzy sa stane najnovsim zaznamom pre dany symbol, cim
stary watch prirodzene "zanikne" - poller uz nikdy nenajde stary riadok, takze
netreba samostatny "consumed" flag ani expiraciu.
"""
from datetime import datetime, timezone

import assets
import strike_client
import trade_cycle
from db import CycleLog, Trade, get_session


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


def check_watch_triggers() -> None:
    print(f"\n=== [watch_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        _check_manual_close_requests(session)

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
            if not last_log or last_log.watch_price is None or not last_log.watch_direction:
                continue

            market = markets_by_symbol.get(symbol)
            if market is None:
                print(f"[watch_monitor] [{name}] symbol {symbol} sa nenasiel v /v2/markets.")
                continue
            live_price = float(market["mark_price"])

            if not _is_triggered(live_price, last_log.watch_price, last_log.watch_direction):
                continue

            print(
                f"[watch_monitor] [{name}] watch podmienka splnena "
                f"(live={live_price}, watch={last_log.watch_direction} {last_log.watch_price}) "
                "- spustam mimoriadny cyklus."
            )
            try:
                trade_cycle.run_triggered_check(asset)
            except Exception as e:
                # jeden asset nesmie zhodit kontrolu ostatnych
                print(f"[watch_monitor] [{name}] mimoriadny cyklus zlyhal: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    check_watch_triggers()
