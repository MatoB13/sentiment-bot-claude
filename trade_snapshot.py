"""Snimka ceny v momente zatvorenia obchodu (2026-09-15, na ziadost pouzivatela).

Dashboard ukazuje pri OTVORENEJ pozicii graf 24 h (hodinove sviecky) a poslednu
hodinu (minutove ceny). Po zatvoreni to zmizlo - pouzivatel chce v tabe "Vsetky
obchody" rovnaky pohlad, ale ZAMRZNUTY v momente zatvorenia.

- Hodinove sviecky (price_bars) su trvale, ale sviecka hodiny, v ktorej obchod
  skoncil, sa po zatvoreni dalej meni - snimka ju preto nahradi bodom = cena
  zatvorenia (close_fill_price, inak posledna minutova cena).
- Minutove ceny (price_minutes) sa drzia len PRICE_MINUTES_KEEP_DAYS (3) dni -
  snimka ich ulozi do trades.close_snapshot SKOR, nez ich price_poller zmaze.

Zatvara sa na 8 miestach v kode (TP/SL burzou, timeout, kill-switch, AI, tp_runner,
dust...) - preto sa snimka NEROBI v okamihu zatvorenia, ale raz za hodinu pre vsetky
obchody zatvorene za posledne 3 dni, ktore ju este nemaju (jedno miesto, nic sa
neprehliadne). Hned po zatvoreni si dashboard to iste posklada priamo z DB
(api/trade-detail.js) - vysledok je rovnaky.

NIKDY nevyhodi vynimku volajucemu (price_poller) - chyba snimky nesmie zastavit ceny.
"""
from datetime import datetime, timedelta, timezone

from db import PriceBar, PriceMinute, Trade

MINUTES_BEFORE = 65      # graf "posledna hodina" + rezerva
HOURS_BEFORE = 25        # graf "24 h" + rezerva
LOOKBACK_DAYS = 3        # = price_poller.PRICE_MINUTES_KEEP_DAYS


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def build_snapshot(session, trade: Trade) -> dict:
    closed = _naive(trade.closed_at)
    minutes = (session.query(PriceMinute.ts, PriceMinute.price)
               .filter(PriceMinute.symbol == trade.symbol,
                       PriceMinute.ts >= closed - timedelta(minutes=MINUTES_BEFORE),
                       PriceMinute.ts <= closed)
               .order_by(PriceMinute.ts).all())
    close_hour = closed.replace(minute=0, second=0, microsecond=0)
    bars = (session.query(PriceBar.hour_start, PriceBar.close)
            .filter(PriceBar.symbol == trade.symbol,
                    PriceBar.hour_start >= close_hour - timedelta(hours=HOURS_BEFORE),
                    PriceBar.hour_start < close_hour)
            .order_by(PriceBar.hour_start).all())
    close_price = trade.close_fill_price
    if close_price is None and minutes:
        close_price = minutes[-1][1]
    return {
        "v": 1,
        "minutes": [[ts.isoformat(), float(p)] for ts, p in minutes],
        "bars": [[hs.isoformat(), float(c)] for hs, c in bars if c is not None],
        "close": [closed.isoformat(), float(close_price)] if close_price is not None else None,
    }


def snapshot_recent_closes(session, now: datetime | None = None) -> int:
    """Doplni close_snapshot obchodom zatvorenym za posledne LOOKBACK_DAYS, ktore ho
    nemaju. Vola price_poller raz za hodinu PRED mazanim starych minutovych cien."""
    now = _naive(now or datetime.now(timezone.utc))
    try:
        todo = (session.query(Trade)
                .filter(Trade.closed_at.isnot(None), Trade.close_snapshot.is_(None),
                        Trade.closed_at >= now - timedelta(days=LOOKBACK_DAYS),
                        Trade.closed_at <= now - timedelta(minutes=2))
                .all())
        for t in todo:
            t.close_snapshot = build_snapshot(session, t)
        if todo:
            session.commit()
            print(f"[trade_snapshot] ulozene snimky pre {len(todo)} zatvorenych obchodov")
        return len(todo)
    except Exception as e:
        session.rollback()
        print(f"[trade_snapshot] snimka zlyhala (pokracujem): {e}")
        return 0
