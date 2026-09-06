"""Zber verejnej Strike tape (obchody VSETKYCH uzivatelov) + open interest.

POVOD (2026-09-06): pouzivatel sa pytal, ci sa da forwardovat Strike Discord
kanal #strike-feed do vlastnej DB. Ukazalo sa, ze ten kanal je len renderovanie
dat, ktore Strike vydava verejne a strukturovane (viz strike_market_client.py) -
s ID sekvenciou a milisekundovou presnostou, takze sa da overit, ze nic
nechyba, a nerozbije sa to pri zmene formatu Discord embedu.

CO SA S TYM ZATIAL ROBI: NIC. Zber je zamerne oddeleny od obchodovania - je
zadarmo (ziadne Claude volanie) a data sa hromadia, aby sa neskor dalo ZMERAT,
ci maju predikcnu hodnotu. Ziadny signal z toho zatial do bota nevstupuje a
nesmie, kym to nepotvrdi backtest - viz [[feedback_backtest_before_proposing]].

FREKVENCIA: hodinovo. Endpoint vracia max 1000 obchodov, co pri najrusnejsom
tickeri (ZEC, 93 obch./h) pokryva 10.8 h - hodinovy zber ma teda desatnasobnu
rezervu proti diere v rade. Sietovo je to ~17 volani na tape + 1 na open
interest za hodinu.

ZLYHANIE je tiche voci botu (ziadna vynimka nevyjde von), ale NIE voci
pouzivatelovi: kazdy pokus - aj neuspesny - zapise riadok do
StrikeTapePollStatus a dashboard (tab "Strike tape") vypadok hlasi cervenym
pismom. Rovnaky dovod ako pri long_short_polleri: vypadok zberu vyzera v
statistikach identicky ako "na trhu sa nic nedeje".
"""
from datetime import datetime, timezone

import assets
import strike_market_client
from db import (OpenInterestBar, StrikeTapePollStatus, StrikeTrade,
                get_session)


def _hour_floor(dt: datetime) -> datetime:
    """Zaciatok hodiny, naive UTC - rovnaky tvar ako PriceBar.hour_start,
    aby sa rady dali spajat bez konverzii."""
    return dt.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None)


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)


def _newest_trade_id(symbol: str, session) -> int | None:
    row = (session.query(StrikeTrade.trade_id)
           .filter(StrikeTrade.symbol == symbol)
           .order_by(StrikeTrade.trade_id.desc()).first())
    return row[0] if row else None


def _store_trades(symbol: str, rows: list[dict], session) -> int:
    """Zapise LEN obchody novsie nez posledny ulozeny (podla trade_id).

    Dedup cez trade_id, nie cez cas: dva obchody mozu mat rovnaku milisekundu,
    ale id je jedinecne. Zaroven to robi zber idempotentnym - opakovany beh
    nad tou istou odpovedou nezapise nic navyse."""
    if not rows:
        return 0
    since = _newest_trade_id(symbol, session)
    fresh = rows if since is None else [r for r in rows if r["id"] > since]
    for r in fresh:
        session.add(StrikeTrade(
            symbol=symbol, trade_id=r["id"], ts=_ts(r["ts_ms"]),
            price=r["price"], qty=r["qty"], quote_qty=r["quote_qty"],
            is_buyer_maker=r["is_buyer_maker"],
        ))
    return len(fresh)


def _store_open_interest(rows: list[dict], session) -> int:
    """Jeden riadok na symbol a hodinu (upsert). OI je bodova hodnota, nie
    kumulativ - berie sa posledne odcitanie v ramci hodiny."""
    written = 0
    for r in rows:
        hour = _hour_floor(_ts(r["ts_ms"]).replace(tzinfo=timezone.utc))
        bar = (session.query(OpenInterestBar)
               .filter(OpenInterestBar.symbol == r["symbol"],
                       OpenInterestBar.hour_start == hour).first())
        if bar is None:
            bar = OpenInterestBar(symbol=r["symbol"], hour_start=hour)
            session.add(bar)
            written += 1
        bar.open_interest = r["open_interest"]
    return written


def _record_status(symbol: str, ok: bool, session, trades_written=None, error=None) -> None:
    row = (session.query(StrikeTapePollStatus)
           .filter(StrikeTapePollStatus.symbol == symbol).first())
    if row is None:
        row = StrikeTapePollStatus(symbol=symbol)
        session.add(row)
    row.polled_at = datetime.now(timezone.utc)
    row.ok = ok
    row.trades_written = trades_written
    row.error = None if error is None else str(error)[:300]


# Pseudo-symbol pre stav zberu open interest - ten je JEDNO volanie pre vsetky
# symboly naraz, takze nema zmysel viest jeho stav per ticker.
OI_STATUS_KEY = "_OPEN_INTEREST"


def poll_all() -> None:
    """Vstupny bod scheduleru (main.py, hodinovo). Zlyhanie jedneho symbolu
    nesmie zhodit ostatne ani cely job.

    Commituje sa PO KAZDOM symbole, nie raz na konci: pri spolocnom commite
    musi zlyhanie zavolat rollback, ktory zahodi aj to, co uz bolo spracovane -
    presne ta chyba, ktoru odhalil test long_short_pollera (zo 6 statusov by
    pri vypadku prezil jediny)."""
    print(f"\n=== [strike_tape_poller] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            symbol = asset.get("strike_symbol")
            if not symbol:
                continue
            try:
                rows = strike_market_client.get_recent_trades(symbol)
                added = _store_trades(symbol, rows, session)
                _record_status(symbol, True, session, trades_written=added)
                session.commit()
                print(f"[strike_tape_poller] [{asset['name']}] {len(rows)} vratenych, "
                      f"{added} novych.")
            except Exception as e:
                session.rollback()
                print(f"[strike_tape_poller] [{asset['name']}] ZLYHALO "
                      f"(pokracujem): {type(e).__name__}: {e}")
                try:
                    _record_status(symbol, False, session, error=e)
                    session.commit()
                except Exception as e2:
                    session.rollback()
                    print(f"[strike_tape_poller] [{asset['name']}] nepodarilo sa "
                          f"zapisat ani status: {e2}")

        # Open interest - JEDNO volanie pre vsetky symboly.
        try:
            oi = strike_market_client.get_open_interest()
            written = _store_open_interest(oi, session)
            _record_status(OI_STATUS_KEY, True, session, trades_written=written)
            session.commit()
            print(f"[strike_tape_poller] open interest: {len(oi)} symbolov, "
                  f"{written} novych hodin.")
        except Exception as e:
            session.rollback()
            print(f"[strike_tape_poller] open interest ZLYHAL: {type(e).__name__}: {e}")
            try:
                _record_status(OI_STATUS_KEY, False, session, error=e)
                session.commit()
            except Exception:
                session.rollback()
    except Exception as e:
        # Poistka: ani neocakavana chyba mimo slucky nesmie zabit job pre
        # dalsie behy. Uz commitnute symboly zostavaju zapisane.
        session.rollback()
        print(f"[strike_tape_poller] neocakavana chyba celeho behu: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    poll_all()
