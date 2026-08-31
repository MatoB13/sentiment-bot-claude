"""Entrypoint - beh na Railway ako worker service (Procfile: worker: python main.py)."""
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import assets
import coinmarketcal_client
import config
import funding_tracker
import heartbeat_check
import position_monitor
import price_poller
import sl_calibration
import trade_cycle
import watch_monitor


def main():
    enabled = assets.enabled_assets()
    active = [a["name"] for a in enabled]
    # Kazdy asset ma teraz VLASTNY trade_interval_hours (viz assets.py/config.py) -
    # scheduler job nizsie musi tikat aspon tak casto ako najrychlejsie
    # pozadovany asset, inak by ho _is_due nikdy nestihol spustit vcas. Samotne
    # per-asset spomalenie (menej casty beh) rieši _is_due/_required_interval_hours.
    base_tick_hours = min(a["trade_interval_hours"] for a in enabled)
    print("=== Sentiment Bot (multi-asset) ===")
    print(f"Aktivne assety: {active}")
    print(f"DRY_RUN={config.DRY_RUN} | base_tick_hours={base_tick_hours} "
          f"| MONITOR_INTERVAL_MINUTES={config.MONITOR_INTERVAL_MINUTES} "
          f"| WATCH_INTERVAL_MINUTES={config.WATCH_INTERVAL_MINUTES}")

    # Jednorazovy backfill (idempotentne - preskoci assety, ktore uz vlastne
    # data maju) MUSI dobehnut PRED prvym trade_cycle behom nizsie, inak by
    # prvy cyklus dna nemal ziadnu vlastnu historiu k dispozicii a zbytocne by
    # padol na yfinance fallback.
    try:
        price_poller.backfill_if_empty()
    except Exception as e:
        print(f"[main] price_poller.backfill_if_empty zlyhal neocakavane: {e}")
    # Rovnaky vzor ako vyssie, ale pre funding platby (viz funding_tracker.py,
    # 2026-08-15) - uplne nezavisle od trade/fill trackovania.
    try:
        funding_tracker.backfill_if_empty()
    except Exception as e:
        print(f"[main] funding_tracker.backfill_if_empty zlyhal neocakavane: {e}")

    # Prve spustenie kazdeho jobu je explicitne volanie nizsie ("hned na starte"),
    # takze scheduler ma zacat tikat az o jeden cely interval neskor - inak by sa
    # prvy beh zdvojil. POZOR: next_run_time=None (povodny pokus, ako tomu predist)
    # job namiesto toho NATRVALO vypne - APScheduler uz nikdy sam nenastavi dalsi
    # beh, kym ho nieco explicitne neprebudi. Over. Preto tu musi byt konkretny
    # buduci cas, nie None.
    # POZOR: MONITOR_INTERVAL_MINUTES je zdielany pre VSETKY assety (position_monitor
    # kontroluje vsetky otvorene pozicie v jednom volani) - zmena v Railway env sa
    # prejavi pre vsetky naraz. trade_cycle scheduler job nizsie tika na
    # base_tick_hours (najrychlejsi pozadovany asset) - kazdy asset sa realne
    # rozhoduje/obchoduje na SVOJOM vlastnom (pomalsom alebo rovnakom) intervale
    # cez _is_due (viz trade_cycle.py).
    now = datetime.now(timezone.utc)
    scheduler = BackgroundScheduler(timezone="UTC")
    # 2026-08-31 - tick uz NIE JE min(trade_interval_hours), ale pevnych
    # SCHEDULER_TICK_MINUTES. Dovod: pri odvodenom ticku sa vsetky tickery
    # vyhodnotili v jednej davke a potom bolo dlho ticho (namerane: 73%
    # desatminutovych okien bez cyklu, max ticho 119 min, spicka 10 cyklov
    # naraz - presne na _DISPATCH_CONCURRENCY_LIMIT). Pri 2h baseline by to
    # bolo horsie (simulacia: 91% stvrthodin bez aktivity, ticho 240 min).
    # Frekvenciu jednotlivych tickerov teraz plne riesi trade_cycle._is_due
    # cez slotovu mriezku - tick len urcuje, ako presne vie slot trafit.
    # run_all_cycles si na zaciatku overi, ci je vobec niekto due, a ak nie,
    # skonci PRED akymkolvek fetchom (inak by 12x castejsi tick znamenal 12x
    # viac yfinance volani, ktore uz raz sposobili rate-limit).
    scheduler.add_job(trade_cycle.run_all_cycles, "interval",
                       minutes=config.SCHEDULER_TICK_MINUTES,
                       next_run_time=now + timedelta(minutes=config.SCHEDULER_TICK_MINUTES),
                       id="trade_cycle")
    # 2026-08-16 (produkcny nalez pouzivatela): predtym bezalo na MONITOR_INTERVAL_MINUTES
    # (10 min) - financna ochrana pozicie tym netrpela (TP/SL/likvidaciu vzdy
    # vykonava Strike sam v realnom case ako bracket objednavku, nezavisle od
    # tohto pollera), ale DETEKCIA zatvorenia (a teda presny PnL lookup, Discord
    # notifikacia, a hned nasledujuci post-close review Claude cyklus - viz
    # position_monitor._fire_post_close_reviews/_fire_close_notifications) mohla
    # meskat az 10 min za skutocnym zatvorenim - kriticke prave pri prudkom
    # pohybe, kedy pouzivatel chce vediet OKAMZITE. check_open_trades() je
    # rovnako lacna ako watch_monitor (1 bulk GET /v2/positions + DB, ziadne
    # Claude/web_search v hlavnej ceste - eskaluje na platene volanie len ked sa
    # NAOZAJ nieco zatvorilo), preto teraz zdiela WATCH_INTERVAL_MINUTES namiesto
    # vlastneho pomalsieho intervalu. Vedlajsi bonus: aj POSITION_MAX_HOURS
    # force-close je teraz presnejsi (do ~1 min od expiracie, nie do 10 min).
    scheduler.add_job(position_monitor.check_open_trades, "interval",
                       minutes=config.WATCH_INTERVAL_MINUTES,
                       next_run_time=now + timedelta(minutes=config.WATCH_INTERVAL_MINUTES),
                       id="position_monitor")
    # Ostava na pomalsom MONITOR_INTERVAL_MINUTES (na rozdiel od position_monitor
    # vyssie) - funding sa akumuluje priebezne pocas drzania pozicie a nepotrebuje
    # rovnaku reaktivitu ako detekcia zatvorenia; navyse robi 1 API volanie PER
    # symbol (nie 1 bulk volanie ako position_monitor/watch_monitor), takze
    # zdielanie tesnejsieho intervalu by zbytocne 10x zvysilo zatazenie Strike API.
    scheduler.add_job(funding_tracker.poll_new, "interval",
                       minutes=config.MONITOR_INTERVAL_MINUTES,
                       next_run_time=now + timedelta(minutes=config.MONITOR_INTERVAL_MINUTES),
                       id="funding_tracker")
    # Samostatny (tesnejsi) interval nez povodny MONITOR_INTERVAL_MINUTES -
    # watch_monitor nerobi ziadne Claude/web_search volanie, kym sa sledovana
    # cenova podmienka reálne nesplni (viz watch_monitor.py), takze castejsi tik
    # je lacny - castejsia kontrola realne znizuje sancu, ze prehliadneme kratky
    # dotyk/odraz od sledovanej hladiny. Teraz zdielany aj s position_monitor
    # vyssie (rovnaky lacny-poll-so-vzacnou-eskalaciou profil).
    scheduler.add_job(watch_monitor.check_watch_triggers, "interval",
                       minutes=config.WATCH_INTERVAL_MINUTES,
                       next_run_time=now + timedelta(minutes=config.WATCH_INTERVAL_MINUTES),
                       id="watch_monitor")
    # "Hot watch" (2026-08-16, viz watch_monitor.mark_hot docstring) - beh kazdu
    # POST_CLOSE_HOT_WATCH_SECONDS, ale skoro vzdy je NOOP (ziaden "hot" symbol =
    # okamzity return bez DB/API volania). Aktivuje sa len na kratke okno hned po
    # zatvoreni pozicie, aby sa rychly pokracujuci pohyb zachytil skor nez za
    # WATCH_INTERVAL_MINUTES.
    scheduler.add_job(watch_monitor.check_hot_watch_triggers, "interval",
                       seconds=config.POST_CLOSE_HOT_WATCH_SECONDS,
                       next_run_time=now + timedelta(seconds=config.POST_CLOSE_HOT_WATCH_SECONDS),
                       id="hot_watch_monitor")
    # Kazdu minutu - primarny zdroj TA dat (viz market_data.get_price_history),
    # ziadne Claude/web_search volanie, len 1 lahky GET /v2/markets.
    scheduler.add_job(price_poller.poll_prices, "interval",
                       minutes=1,
                       next_run_time=now + timedelta(minutes=1),
                       id="price_poller")
    # 2026-08-21 (na ziadost pouzivatela, pred cestou bez pocitaca) - "je bot
    # nazivo?" kontrola, viz heartbeat_check.py pre plny kontext a DOLEZITE
    # OBMEDZENIE (zachyti len zaseknuty proces, nie uplny pad - ten sa neda
    # zachytit zvnutra toho isteho procesu). 5 min interval pri 15 min prahu
    # dava rozumne rozlisenie bez zbytocneho zatazenia.
    scheduler.add_job(heartbeat_check.check_heartbeat, "interval",
                       minutes=5,
                       next_run_time=now + timedelta(minutes=5),
                       id="heartbeat_check")
    # Denne (2026-08-19) - ATR-zalozena SL/TP kalibracia (viz sl_calibration.py).
    # Ziadne Claude volanie, len OHLC + aritmetika - lacne ako funding_tracker.
    # Vysledok je LEN navrh (db.AtrCalibration), nic sa tu automaticky nemeni.
    scheduler.add_job(sl_calibration.compute_all, "interval",
                       hours=24,
                       next_run_time=now + timedelta(hours=24),
                       id="sl_calibration")
    # Denne (2026-08-19, na ziadost pouzivatela) - CoinMarketCal krypto-projektovy
    # event kalendar (viz coinmarketcal_client.py) pre kazdy asset s nastavenym
    # coinmarketcal_slug (ADA/ZEC/HYPE/NIGHT). Free plan ma kreditovy kvoten
    # (resetuje sa ~13 dni), preto LEN raz denne, nikdy zivo pocas cyklu.
    scheduler.add_job(coinmarketcal_client.poll_events, "interval",
                       hours=24,
                       next_run_time=now + timedelta(minutes=2),
                       id="coinmarketcal_poller")
    # "Zivy" TOP-5 SL/TP grid-search rebricek PER TICKER (viz sl_grid_backtest.py)
    # UZ NIE JE scheduler job (2026-08-19, na ziadost pouzivatela) - vysledok
    # zavisi VYHRADNE od historie obchodov daneho tickera, takze denny beh pre
    # VSETKY tickery bol cisty odpad (a naopak, hned po uzavreti bol rebricek
    # az 24h neaktualny). Prepocet je teraz EVENT-DRIVEN - position_monitor.py
    # vola sl_grid_backtest.recompute_symbol() PRIAMO PO uzavreti kazdeho
    # obchodu, LEN pre ten jeden ticker (viz position_monitor._fire_recomputes).
    scheduler.start()

    # spusti oba joby hned na starte, potom uz podla intervalu. Na rozdiel od
    # scheduler.add_job beh tu nie je nicim odchytavany - nezachytena vynimka by
    # zhodila cely worker proces (Railway by ho restartoval, co sposobovalo
    # viachodinove diery v historii). Kazdy job si chyby loguje/zaznamenava sam,
    # tu len zabranime celkovemu padu procesu pri necakanej vynimke.
    try:
        price_poller.poll_prices()
    except Exception as e:
        print(f"[main] poll_prices zlyhal neocakavane: {e}")
    try:
        trade_cycle.run_all_cycles()
    except Exception as e:
        print(f"[main] run_all_cycles zlyhal neocakavane: {e}")
    try:
        position_monitor.check_open_trades()
    except Exception as e:
        print(f"[main] check_open_trades zlyhal neocakavane: {e}")
    try:
        funding_tracker.poll_new()
    except Exception as e:
        print(f"[main] funding_tracker.poll_new zlyhal neocakavane: {e}")
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
