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

    # spusti oba joby hned na starte, potom uz podla intervalu
    trade_cycle.run_cycle()
    position_monitor.check_open_trades()

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
