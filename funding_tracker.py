"""Sleduje periodicke funding platby na Strike (viz db.FundingPayment) -
UPLNE nezavisle od Trade/fill trackovania, kedze /v2/history/fill funding
vobec neobsahuje (overene 2026-08-15: nas trackovany PnL z fillov sedel na
cent presne s realized fill PnL na Strike, ale leaderboard ukazoval o dost
viac - ten rozdiel bol presne sucet funding platieb). Rovnaky backfill+poll
vzor ako price_poller.py."""
from datetime import datetime, timezone

import assets
import strike_client
from db import FundingPayment, get_session


def _to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def _upsert_records(records: list[dict], symbol: str, session) -> int:
    """Vlozi len zaznamy, ktore este nemame (podla strike_id) - bezpecne volat
    opakovane s prekryvajucimi sa oknami (poll_new nizsie zamerne pouziva
    maly overlap, aby nestratil zaznamy medzi tikmi)."""
    if not records:
        return 0
    existing_ids = {
        row[0] for row in session.query(FundingPayment.strike_id)
        .filter(FundingPayment.strike_id.in_([r["id"] for r in records]))
        .all()
    }
    added = 0
    for r in records:
        if r["id"] in existing_ids:
            continue
        session.add(FundingPayment(
            strike_id=r["id"],
            symbol=symbol,
            position_side=r.get("position_side"),
            position_size=float(r["position_size"]) if r.get("position_size") is not None else None,
            funding_rate=float(r["funding_rate"]) if r.get("funding_rate") is not None else None,
            amount=float(r["amount"]),
            occurred_at=_to_datetime(r["timestamp"]),
        ))
        added += 1
    return added


def backfill_if_empty() -> None:
    """JEDNORAZOVY backfill celej dostupnej funding historie - idempotentne
    (ak uz mame aspon 1 zaznam pre dany symbol, preskocime ho), bezpecne
    volat pri kazdom starte rovnako ako price_poller.backfill_if_empty."""
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            symbol = asset["strike_symbol"]
            already_has_data = session.query(FundingPayment.id).filter(
                FundingPayment.symbol == symbol
            ).first()
            if already_has_data:
                continue
            try:
                records = strike_client.get_funding_history(symbol, limit=1000)
            except Exception as e:
                print(f"[funding_tracker] Backfill pre {symbol} zlyhal: {e}")
                continue
            added = _upsert_records(records, symbol, session)
            session.commit()
            if added:
                print(f"[funding_tracker] Backfill {symbol}: {added} funding zaznamov.")
    finally:
        session.close()


def poll_new() -> None:
    """Zavola sa periodicky (viz main.py, rovnaky interval ako position_monitor) -
    dotiahne funding zaznamy od poslednej znamej platby KAZDEHO symbolu dalej
    (s malym prekryvom, aby nestratil zaznam presne na hranici), dedup cez
    strike_id v _upsert_records vyssie postara o zvysok."""
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            symbol = asset["strike_symbol"]
            last = (
                session.query(FundingPayment)
                .filter(FundingPayment.symbol == symbol)
                .order_by(FundingPayment.occurred_at.desc())
                .first()
            )
            start_ms = None
            if last is not None:
                last_ts = last.occurred_at.replace(tzinfo=timezone.utc)
                start_ms = int(last_ts.timestamp() * 1000) - 3_600_000  # 1h prekryv
            try:
                records = strike_client.get_funding_history(symbol, start_ms=start_ms, limit=1000)
            except Exception as e:
                print(f"[funding_tracker] Poll pre {symbol} zlyhal: {e}")
                continue
            added = _upsert_records(records, symbol, session)
            if added:
                session.commit()
                print(f"[funding_tracker] {symbol}: {added} novych funding zaznamov.")
            else:
                session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    backfill_if_empty()
    poll_new()
