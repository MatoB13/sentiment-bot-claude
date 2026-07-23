"""Periodicka kontrola otvorenych pozicii (naprieč vsetkymi assetmi - NAS100/NVDA/ADA)
- zaznamenanie zatvorenia a PnL."""
from datetime import datetime, timezone

import config
import strike_client
from db import Trade, get_session


def _lookup_realized_pnl(trade: Trade):
    """Najde realized_pnl v /v2/closedPositions pre poziciu zatvorenu po `trade.opened_at`."""
    opened_at_ms = int(trade.opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    try:
        closed = strike_client.get_closed_positions(symbol=trade.symbol, limit=10)
    except Exception as e:
        print(f"[position_monitor] Nepodarilo sa nacitat closedPositions: {e}")
        return None
    for p in closed:
        closed_at_ms = p.get("closed_at")
        if closed_at_ms and closed_at_ms >= opened_at_ms:
            try:
                return float(p.get("realized_pnl"))
            except (TypeError, ValueError):
                return None
    return None


def check_open_trades():
    print(f"\n=== [position_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        open_trades = session.query(Trade).filter(Trade.status == "open").all()
        if not open_trades:
            print("[position_monitor] Ziadne otvorene pozicie.")
            return

        # Bez symbol filtra - vsetky otvorene pozicie na ucte v JEDNOM volani,
        # zdielanom pre vsetky sledovane assety (NAS100/NVDA/ADA), namiesto
        # samostatneho volania na kazdy symbol zvlast.
        live_positions = strike_client.get_positions()
        live_by_symbol = {p.get("symbol"): p for p in live_positions}

        now = datetime.now(timezone.utc)
        for trade in open_trades:
            # Bot drzi vzdy najviac 1 poziciu naraz (viz has_open_position v trade_cycle.py),
            # takze zhoda podla symbolu je jednoznacna.
            live = live_by_symbol.get(trade.symbol)

            if live is None:
                # uz nie je medzi otvorenymi poziciami na burze -> zatvorena (TP/SL/likvidacia)
                trade.status = "closed_by_exchange"
                trade.closed_at = now
                trade.close_reason = "not_found_in_open_positions (TP/SL/liquidation)"
                trade.pnl_usd = _lookup_realized_pnl(trade)
                print(f"[position_monitor] Trade {trade.id} zatvoreny burzou "
                      f"(pnl={trade.pnl_usd}).")
                session.add(trade)
                continue

            expires_at = trade.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if now >= expires_at:
                print(f"[position_monitor] Trade {trade.id} presiahol {config.POSITION_MAX_HOURS}h, zatvaram.")
                try:
                    strike_client.cancel_all_orders(trade.symbol)  # zrusi visiace TP/SL objednavky
                    strike_client.close_position_market(trade.direction, float(live["Size"]), trade.symbol)
                except Exception as e:
                    print(f"[position_monitor] Chyba pri force-close: {e}")
                    continue
                trade.status = "closed_by_timeout"
                trade.closed_at = now
                trade.close_reason = f"max_hold_{config.POSITION_MAX_HOURS}h_reached"
                trade.pnl_usd = _lookup_realized_pnl(trade)
                session.add(trade)
            else:
                print(f"[position_monitor] Trade {trade.id} stale otvoreny "
                      f"(expiruje {expires_at.isoformat()}).")

        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    check_open_trades()
