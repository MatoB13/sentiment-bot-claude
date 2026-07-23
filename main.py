"""Entrypoint - beh na Railway ako worker service (Procfile: worker: python main.py)."""
import time

from apscheduler.schedulers.background import BackgroundScheduler

import config
import position_monitor
import trade_cycle


def main():
    print("=== NAS100 Sentiment Bot ===")
    print(f"DRY_RUN={config.DRY_RUN} | TRADE_INTERVAL_HOURS={config.TRADE_INTERVAL_HOURS} "
          f"| MONITOR_INTERVAL_MINUTES={config.MONITOR_INTERVAL_MINUTES}")

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(trade_cycle.run_cycle, "interval",
                       hours=config.TRADE_INTERVAL_HOURS, next_run_time=None, id="trade_cycle")
    scheduler.add_job(position_monitor.check_open_trades, "interval",
                       minutes=config.MONITOR_INTERVAL_MINUTES, id="position_monitor")
    scheduler.start()

    # spusti oba joby hned na starte, potom uz podla intervalu. Na rozdiel od
    # scheduler.add_job beh tu nie je nicim odchytavany - nezachytena vynimka by
    # zhodila cely worker proces (Railway by ho restartoval, co sposobovalo
    # viachodinove diery v historii). Kazdy job si chyby loguje/zaznamenava sam,
    # tu len zabranime celkovemu padu procesu pri necakanej vynimke.
    try:
        trade_cycle.run_cycle()
    except Exception as e:
        print(f"[main] run_cycle zlyhal neocakavane: {e}")
    try:
        position_monitor.check_open_trades()
    except Exception as e:
        print(f"[main] check_open_trades zlyhal neocakavane: {e}")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
