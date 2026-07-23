"""Jeden analyticky/obchodny cyklus - spusta ho scheduler v main.py."""
from datetime import datetime, timedelta, timezone

import claude_analyst
import config
import market_data
import risk_manager
import social_sentiment
import strike_client
from db import CycleLog, Trade, get_session


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
        open_trade = session.query(Trade).filter(Trade.status == "open").first()
        if open_trade:
            print(f"[trade_cycle] Uz existuje otvorena pozicia (trade_id={open_trade.id}), preskakujem.")
            session.add(CycleLog(
                config_snapshot=_config_snapshot(),
                outcome="skipped",
                reject_reason=f"Uz existuje otvorena pozicia (trade_id={open_trade.id}).",
                trade_id=open_trade.id,
            ))
            session.commit()
            return

        try:
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
        except Exception as e:
            # Strike/yfinance API vypadok tu predtym zhodil cely cyklus neodchytenou
            # vynimkou - ak islo o ten uvodny priamy beh v main.py (mimo schedulera),
            # spadol cely worker proces a Railway ho restartoval, co sposobilo viachodinove
            # diery v historii bez akejkolvek stopy. Radsej zalogujeme a bezpecne preskocime.
            print(f"[trade_cycle] Zber trhovych dat zlyhal, preskakujem cyklus: {e}")
            session.add(CycleLog(
                config_snapshot=_config_snapshot(),
                outcome="error", reject_reason=f"market_data_fetch_failed: {e}",
            ))
            session.commit()
            return

        prev_log = (
            session.query(CycleLog)
            .filter(CycleLog.key_assumptions.isnot(None))
            .order_by(CycleLog.created_at.desc())
            .first()
        )
        prev_assumptions = prev_log.key_assumptions if prev_log else None
        prev_cycle_time = prev_log.created_at if prev_log else None

        try:
            decision, web_search_log = claude_analyst.analyze(
                ta, cross_market, market_session, social, prev_assumptions, prev_cycle_time,
            )
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
        print(f"[trade_cycle] Web search log: {web_search_log}")

        cycle_log = CycleLog(
            live_price=live_price, ta=ta, cross_market=cross_market, session_data=market_session,
            config_snapshot=_config_snapshot(),
            direction=decision.get("direction"), confidence=decision.get("confidence"),
            stop_loss_price=decision.get("stop_loss_price"), take_profit_price=decision.get("take_profit_price"),
            reasoning=decision.get("reasoning"),
            web_search_log=web_search_log,
            key_assumptions=decision.get("key_assumptions"),
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
            try:
                result = strike_client.open_bracket_position(
                    direction=sized["direction"],
                    size=sized["size"],
                    leverage=sized["leverage"],
                    stop_loss_price=sized["stop_loss_price"],
                    take_profit_price=sized["take_profit_price"],
                )
            except Exception as e:
                # Otvorenie na Strike zlyhalo uplne (API chyba, nedostatok prostriedkov...) -
                # ziadna pozicia nevznikla, ale nesmieme stratit stopu po tomto pokuse.
                print(f"[trade_cycle] Otvorenie pozicie na Strike zlyhalo: {e}")
                cycle_log.outcome = "error"
                cycle_log.reject_reason = f"open_position_failed: {e}"
                session.add(cycle_log)
                session.commit()
                return

            print(f"[trade_cycle] Strike odpoved: {result}")
            trade.strategy_id = result.get("strategy_id")

            # Bezpecnostna kontrola: ak SL alebo TP noha bracket objednavky zlyhala
            # pripojit sa (Strike ju z nejakeho dovodu odmietol), pozicia by bola
            # nechranena az do dalsieho position_monitor cyklu (az 10 min). Radsej
            # ju hned teraz nudzovo zatvorime, nez by cakala nechranena na burze.
            if not result.get("sl_client_order_id") or not result.get("tp_client_order_id"):
                print(
                    "[trade_cycle] KRITICKE: chyba sl_client_order_id alebo "
                    "tp_client_order_id v odpovedi - pozicia je NECHRANENA. "
                    "Nudzovo zatvaram okamzite."
                )
                try:
                    strike_client.cancel_all_orders(trade.symbol)
                    strike_client.close_position_market(
                        sized["direction"], sized["size"], trade.symbol
                    )
                    trade.status = "closed_by_safety"
                    trade.close_reason = "missing_sl_or_tp_leg_after_open"
                    trade.closed_at = datetime.now(timezone.utc)
                except Exception as e:
                    print(f"[trade_cycle] CHYBA pri nudzovom zatvoreni: {e}")
                    trade.close_reason = f"missing_sl_or_tp_leg_AND_safety_close_failed: {e}"

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
