"""Jeden analyticky/obchodny cyklus - spusta ho scheduler v main.py."""
from datetime import datetime, timedelta, timezone

import claude_analyst
import config
import market_data
import risk_manager
import social_sentiment
import strike_client
from db import CycleLog, Trade, get_session


def has_open_position(session) -> bool:
    return session.query(Trade).filter(Trade.status == "open").count() > 0


def _config_snapshot() -> dict:
    """Aktualne aktivne trading/risk nastavenia - uklada sa s kazdym cyklom, aby
    dashboard vzdy zobrazoval presne to, s cim bot naozaj bezal (zmena v Railway
    env premennych sa prejavi uz na dalsom cykle, bez rucnej synchronizacie)."""
    return {
        "symbol": config.STRIKE_NAS100_SYMBOL,
        "dry_run": config.DRY_RUN,
        "trade_interval_hours": config.TRADE_INTERVAL_HOURS,
        "monitor_interval_minutes": config.MONITOR_INTERVAL_MINUTES,
        "position_max_hours": config.POSITION_MAX_HOURS,
        "min_confidence": config.MIN_CONFIDENCE,
        "margin_usd": config.MARGIN_USD,
        "leverage": config.LEVERAGE,
        "default_sl_pct": config.DEFAULT_SL_PCT,
        "default_tp_pct": config.DEFAULT_TP_PCT,
    }


def run_cycle():
    print(f"\n=== [trade_cycle] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        if has_open_position(session):
            print("[trade_cycle] Uz existuje otvorena pozicia, preskakujem.")
            return

        market_meta = strike_client.get_market(config.STRIKE_NAS100_SYMBOL)
        live_price = float(market_meta["mark_price"])

        ta = market_data.get_market_snapshot()
        cross_market = market_data.get_cross_market_snapshot()
        market_session = market_data.get_session_snapshot()
        social = social_sentiment.fetch_recent_posts()
        print(f"[trade_cycle] Strike live_price={live_price} | TA: {ta}")
        print(f"[trade_cycle] Cross-market: {cross_market}")
        print(f"[trade_cycle] Session: {market_session}")
        print(f"[trade_cycle] Nacitanych {len(social)} social prispevkov (spravy hlada Claude sam cez web_search).")

        try:
            decision = claude_analyst.analyze(ta, cross_market, market_session, social)
        except Exception as e:
            print(f"[trade_cycle] Claude analyza zlyhala, preskakujem cyklus: {e}")
            session.add(CycleLog(
                live_price=live_price, ta=ta, cross_market=cross_market, session_data=market_session,
                config_snapshot=_config_snapshot(),
                outcome="error", reject_reason=str(e),
            ))
            session.commit()
            return
        print(f"[trade_cycle] Claude rozhodnutie: {decision}")

        cycle_log = CycleLog(
            live_price=live_price, ta=ta, cross_market=cross_market, session_data=market_session,
            config_snapshot=_config_snapshot(),
            direction=decision.get("direction"), confidence=decision.get("confidence"),
            stop_loss_price=decision.get("stop_loss_price"), take_profit_price=decision.get("take_profit_price"),
            reasoning=decision.get("reasoning"),
        )

        try:
            sized = risk_manager.validate_and_size(
                decision, ta, has_open_position=False,
                live_price=live_price, market_meta=market_meta,
            )
        except risk_manager.RejectedTrade as e:
            print(f"[trade_cycle] Obchod zamietnuty risk managerom: {e}")
            cycle_log.outcome = "rejected"
            cycle_log.reject_reason = str(e)
            session.add(cycle_log)
            session.commit()
            return

        print(f"[trade_cycle] Otvaram {sized['direction']} | leverage={sized['leverage']} "
              f"| size={sized['size']} | notional=${sized['notional_usd']} "
              f"| margin=${sized['margin_usd']} | SL={sized['stop_loss_price']} "
              f"| TP={sized['take_profit_price']} | confidence={sized['confidence']}")

        trade = Trade(
            symbol=config.STRIKE_NAS100_SYMBOL,
            direction=sized["direction"],
            confidence=sized["confidence"],
            reasoning=sized["reasoning"],
            entry_price=sized["entry_price"],
            stop_loss_price=sized["stop_loss_price"],
            take_profit_price=sized["take_profit_price"],
            leverage=sized["leverage"],
            size=sized["size"],
            notional_usd=sized["notional_usd"],
            margin_usd=sized["margin_usd"],
            opened_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=config.POSITION_MAX_HOURS),
            status="dry_run" if config.DRY_RUN else "open",
            dry_run=config.DRY_RUN,
        )

        if config.DRY_RUN:
            print("[trade_cycle] DRY_RUN=true - obchod sa NEODOSLAL na Strike, iba zalogovany do DB.")
        else:
            result = strike_client.open_bracket_position(
                direction=sized["direction"],
                size=sized["size"],
                leverage=sized["leverage"],
                stop_loss_price=sized["stop_loss_price"],
                take_profit_price=sized["take_profit_price"],
            )
            print(f"[trade_cycle] Strike odpoved: {result}")
            trade.strategy_id = result.get("strategy_id")

        session.add(trade)
        session.flush()  # priradi trade.id pred zapisom do cycle_log
        cycle_log.outcome = "opened"
        cycle_log.trade_id = trade.id
        session.add(cycle_log)
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    run_cycle()
