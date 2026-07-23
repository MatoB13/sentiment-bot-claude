"""Entrypoint - beh na Railway ako worker service (Procfile: worker: python main.py)."""
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import assets
import config
import position_monitor
import trade_cycle
import watch_monitor


def main():
    active = [a["name"] for a in assets.enabled_assets()]
    print("=== Sentiment Bot (multi-asset) ===")
    print(f"Aktivne assety: {active}")
    print(f"DRY_RUN={config.DRY_RUN} | TRADE_INTERVAL_HOURS={config.TRADE_INTERVAL_HOURS} "
          f"| MONITOR_INTERVAL_MINUTES={config.MONITOR_INTERVAL_MINUTES}")

    # Prve spustenie kazdeho jobu je explicitne volanie nizsie ("hned na starte"),
    # takze scheduler ma zacat tikat az o jeden cely interval neskor - inak by sa
    # prvy beh zdvojil. POZOR: next_run_time=None (povodny pokus, ako tomu predist)
    # job namiesto toho NATRVALO vypne - APScheduler uz nikdy sam nenastavi dalsi
    # beh, kym ho nieco explicitne neprebudi. Over. Preto tu musi byt konkretny
    # buduci cas, nie None.
    # POZOR: TRADE_INTERVAL_HOURS/MONITOR_INTERVAL_MINUTES su zdielane pre VSETKY
    # assety (NAS100/NVDA/ADA bezia v tom istom tiku) - zmena v Railway env sa
    # prejavi pre vsetky naraz.
    now = datetime.now(timezone.utc)
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(trade_cycle.run_all_cycles, "interval",
                       hours=config.TRADE_INTERVAL_HOURS,
                       next_run_time=now + timedelta(hours=config.TRADE_INTERVAL_HOURS),
                       id="trade_cycle")
    scheduler.add_job(position_monitor.check_open_trades, "interval",
                       minutes=config.MONITOR_INTERVAL_MINUTES,
                       next_run_time=now + timedelta(minutes=config.MONITOR_INTERVAL_MINUTES),
                       id="position_monitor")
    # Rovnaky (lacny, cakty) interval ako position_monitor - watch_monitor nerobi
    # ziadne Claude/web_search volanie, kym sa sledovana cenova podmienka reálne
    # nesplni (viz watch_monitor.py).
    scheduler.add_job(watch_monitor.check_watch_triggers, "interval",
                       minutes=config.MONITOR_INTERVAL_MINUTES,
                       next_run_time=now + timedelta(minutes=config.MONITOR_INTERVAL_MINUTES),
                       id="watch_monitor")
    scheduler.start()

    # spusti oba joby hned na starte, potom uz podla intervalu. Na rozdiel od
    # scheduler.add_job beh tu nie je nicim odchytavany - nezachytena vynimka by
    # zhodila cely worker proces (Railway by ho restartoval, co sposobovalo
    # viachodinove diery v historii). Kazdy job si chyby loguje/zaznamenava sam,
    # tu len zabranime celkovemu padu procesu pri necakanej vynimke.
    try:
        trade_cycle.run_all_cycles()
    except Exception as e:
        print(f"[main] run_all_cycles zlyhal neocakavane: {e}")
    try:
        position_monitor.check_open_trades()
    except Exception as e:
        print(f"[main] check_open_trades zlyhal neocakavane: {e}")
    try:
        watch_monitor.check_watch_triggers()
    except Exception as e:
        print(f"[main] check_watch_triggers zlyhal neocakavane: {e}")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
