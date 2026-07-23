"""
Lahky, NEPLATENY poller pre "watch" podmienky.

Ked je Claudeho rozhodnutie "none", ale vidi konkretnu cenovu uroven cakajucu
na potvrdenie (napr. retest), ulozi si ju do CycleLog.watch_price/watch_direction
(viz claude_analyst.py). Tento modul kazdych MONITOR_INTERVAL_MINUTES skontroluje
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


def check_watch_triggers() -> None:
    print(f"\n=== [watch_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
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

            try:
                live_price = float(strike_client.get_market(symbol)["mark_price"])
            except Exception as e:
                print(f"[watch_monitor] [{name}] nepodarilo sa nacitat live cenu: {e}")
                continue

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
