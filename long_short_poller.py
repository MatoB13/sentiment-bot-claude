"""Hodinovy zber Binance "global long/short account ratio" do long_short_bars
(2026-09-06, na ziadost pouzivatela) - aby sa dal vykreslit ako ciara v cenovom
grafe vedla hodinovych sviecok.

PRECO SAMOSTATNY JOB, a nie v price_poller.py (minutovom):
1. Najjemnejsia granularita endpointu je 5 MINUT, pri period="1h" sa hodnota
   meni raz za hodinu - minutove volanie by 60x stiahlo to iste cislo.
2. Minutovy poller robi dnes JEDNO lahke GET /v2/markets pre vsetky tickery.
   Pridanim 6 Binance volani by sa z neho stalo 7 volani za minutu, cize
   8640 denne namiesto 144. Ako samostatny hodinovy job bezi na vlastnom
   vlakne BackgroundScheduleru (default 10 workerov, max_instances=1), takze
   nevie zdrzat ani price_poller, ani obchodny cyklus.

PRECO NIE `cycle_logs.ta`: tam sa jedna hodnota uz uklada, ale LEN ked bezi
cyklus - namerane 44-89 bodov za 10 dni podla tickera (priemerny odstup
1.6-3.0 h, najvacsia diera 12 h) proti 240 hodinovym cenovym sviecram. Ta rada
je na graf prilis reda. `ta` sa touto zmenou NEMENI - Claude ju ma v prompte a
kvoli grafu sa obchodny cyklus nedotyka.

ZLYHANIE je voci botu uplne tiche (ziadna vynimka nevyjde von, cyklus o tomto
module nevie), ale NIE voci pouzivatelovi: kazdy pokus - aj neuspesny - zapise
riadok do LongShortPollStatus a dashboard nad grafom cervenym pismom ukaze, ze
zber padol. Endpoint je na fapi.binance.com, nie na oficialnom mirrore
data-api.binance.vision, takze regionalny blok je realne riziko a nesmie
vyzerat ako "pomer sa nemeni".
"""
from datetime import datetime, timedelta, timezone

import assets
import binance_client
from db import LongShortBar, LongShortPollStatus, get_session

# Prvy beh (prazdna tabulka) stiahne maximum, ktore endpoint da - 500
# hodinovych bodov = ~21 dni. Graf je tak plny hned, netreba cakat tyzden.
_BACKFILL_LIMIT = 500
# Bezny beh: staci posledna hodina, ale beriem par navyse ako prekryv, aby
# jeden vynechany/zlyhany beh nenechal v rade trvalu dieru (upsert nizsie
# existujuce riadky len prepise).
_INCREMENTAL_LIMIT = 6
# Pod tolko barov v tabulke sa symbol povazuje za "este nenaplneny" a ide sa
# znova na plny backfill (napr. po pridani noveho tickera).
_BACKFILL_THRESHOLD = 100


def _hour_floor(ms: int) -> datetime:
    """ms epoch UTC -> zaciatok tej hodiny, naive UTC (rovnaky tvar ako
    PriceBar.hour_start, aby sa rady dali joinovat)."""
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=None)


def _upsert(symbol: str, rows: list[dict], session) -> int:
    """Zapise/prepise bary. Vrati pocet NOVYCH riadkov."""
    if not rows:
        return 0
    hours = [_hour_floor(r["timestamp"]) for r in rows]
    existing = {
        b.hour_start: b
        for b in session.query(LongShortBar).filter(
            LongShortBar.symbol == symbol,
            LongShortBar.hour_start >= min(hours),
            LongShortBar.hour_start <= max(hours),
        )
    }
    added = 0
    for row, hour in zip(rows, hours):
        bar = existing.get(hour)
        if bar is None:
            bar = LongShortBar(symbol=symbol, hour_start=hour)
            session.add(bar)
            existing[hour] = bar
            added += 1
        bar.long_pct = row["long_pct"]
        bar.short_pct = row["short_pct"]
        bar.ratio = row["long_short_ratio"]
    return added


def _record_status(symbol: str, ok: bool, session, bars_written=None, error=None) -> None:
    row = session.query(LongShortPollStatus).filter(
        LongShortPollStatus.symbol == symbol,
    ).first()
    if row is None:
        row = LongShortPollStatus(symbol=symbol)
        session.add(row)
    row.polled_at = datetime.now(timezone.utc)
    row.ok = ok
    row.bars_written = bars_written
    # Orezane - staci na rozlisenie typu zlyhania (403 vs timeout vs DNS),
    # cely traceback by v tabulke aj na dashboarde len zavadzal.
    row.error = None if error is None else str(error)[:300]


def poll_all() -> None:
    """Vstupny bod scheduleru (main.py, hodinovo). Zlyhanie jedneho symbolu
    nesmie zhodit ostatne ani cely job."""
    print(f"\n=== [long_short_poller] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            binance_symbol = asset.get("binance_volume_symbol")
            if not binance_symbol:
                continue  # ticker nema Binance futures trh - normalny stav
            symbol = asset["strike_symbol"]
            # Commit sa robi PO KAZDOM tickeri, nie raz na konci. Dovod (chytene
            # testom): pri spolocnom commite musi zlyhanie zavolat rollback, a ten
            # zahodi VSETKO nezapisane - teda aj bary a statusy uz spracovanych
            # tickerov. Pri vypadku Binance (403 pre vsetky) tak z 6 statusov
            # prezil jediny a dashboard by vypadok nenahlasil spravne.
            try:
                have = session.query(LongShortBar).filter(
                    LongShortBar.symbol == symbol,
                ).count()
                limit = _BACKFILL_LIMIT if have < _BACKFILL_THRESHOLD else _INCREMENTAL_LIMIT
                rows = binance_client.get_long_short_history(binance_symbol, "1h", limit)
                added = _upsert(symbol, rows, session)
                _record_status(symbol, True, session, bars_written=added)
                session.commit()
                print(f"[long_short_poller] [{asset['name']}] {len(rows)} bodov "
                      f"(limit {limit}), z toho novych {added}.")
            except Exception as e:
                session.rollback()
                print(f"[long_short_poller] [{asset['name']}] ZLYHALO "
                      f"(pokracujem): {type(e).__name__}: {e}")
                # Samotny zapis statusu tiez v try - keby padla DB, nesmie to
                # zhodit zvysok behu (inak by jeden vypadok DB umlcal cely job).
                try:
                    _record_status(symbol, False, session, error=e)
                    session.commit()
                except Exception as e2:
                    session.rollback()
                    print(f"[long_short_poller] [{asset['name']}] nepodarilo sa "
                          f"zapisat ani status: {e2}")
    except Exception as e:
        # Poistka: ani neocakavana chyba mimo per-ticker slucky (napr. pad pri
        # iteracii assetov) nesmie vyjst von do scheduleru a zabit job pre
        # dalsie behy. Uz commitnute tickery zostavaju zapisane.
        session.rollback()
        print(f"[long_short_poller] neocakavana chyba celeho behu: {e}")
    finally:
        session.close()


def latest_hours_missing(symbol: str, session) -> float | None:
    """Kolko hodin uplynulo od najnovsieho baru (None ak ziadny nie je) -
    pouzitelne na 'ako cerstva je ta ciara' aj mimo dashboardu."""
    newest = (session.query(LongShortBar.hour_start)
              .filter(LongShortBar.symbol == symbol)
              .order_by(LongShortBar.hour_start.desc()).first())
    if newest is None:
        return None
    return (datetime.now(timezone.utc).replace(tzinfo=None) - newest[0]) / timedelta(hours=1)


if __name__ == "__main__":
    poll_all()
