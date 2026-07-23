"""Analyticky/obchodny cyklus pre vsetky aktivne assety (NAS100/NVDA/ADA).

Jeden scheduler tick (viz main.py) = jeden vstup do run_all_cycles(): zdielany
makro fetch (cross-market/session, pripadne BTC proxy) sa spravi PRESNE RAZ a
potom sa pouzije pre kazdy aktivny asset z assets.py nezavisle - kazdy ma
vlastnu poziciu, vlastny risk (SL/TP%, leverage, margin, min_confidence) a
vlastne Claude rozhodnutie. Zlyhanie jedneho assetu nesmie zhodit ostatne."""
from datetime import datetime, timedelta, timezone

import assets
import claude_analyst
import config
import market_data
import risk_manager
import social_sentiment
import strike_client
from db import CycleLog, Trade, get_session


def _config_snapshot(asset: dict) -> dict:
    """Aktualne aktivne trading/risk nastavenia pre dany asset - uklada sa s
    kazdym cyklom, aby dashboard vzdy zobrazoval presne to, s cim bot naozaj
    bezal (zmena v Railway env premennych sa prejavi uz na dalsom cykle)."""
    return {
        "symbol": asset["strike_symbol"],
        "asset_name": asset["name"],
        "dry_run": config.DRY_RUN,
        "trade_interval_hours": config.TRADE_INTERVAL_HOURS,
        "monitor_interval_minutes": config.MONITOR_INTERVAL_MINUTES,
        "position_max_hours": config.POSITION_MAX_HOURS,
        "min_confidence": asset["min_confidence"],
        "margin_usd": asset["margin_usd"],
        "leverage": asset["leverage"],
        "default_sl_pct": asset["sl_pct"],
        "default_tp_pct": asset["tp_pct"],
    }


def run_cycle_for_asset(asset: dict, cross_market: dict, market_session: dict,
                         btc_proxy: dict | None) -> None:
    """Kompletny cyklus pre JEDEN asset - vlastna DB session/commit, aby chyba
    v jednom assete neponechala nedokoncenu transakciu pre dalsi."""
    name = asset["name"]
    symbol = asset["strike_symbol"]
    print(f"\n--- [{name}] ---")
    session = get_session()
    try:
        open_trade = session.query(Trade).filter(
            Trade.symbol == symbol, Trade.status == "open",
        ).first()
        if open_trade:
            print(f"[{name}] Uz existuje otvorena pozicia (trade_id={open_trade.id}), preskakujem.")
            session.add(CycleLog(
                symbol=symbol,
                config_snapshot=_config_snapshot(asset),
                outcome="skipped",
                reject_reason=f"Uz existuje otvorena pozicia (trade_id={open_trade.id}).",
                trade_id=open_trade.id,
            ))
            session.commit()
            return

        try:
            market_meta = strike_client.get_market(symbol)
            live_price = float(market_meta["mark_price"])

            ta = market_data.get_market_snapshot(asset["yf_symbol"], asset.get("yf_fallback"))
            social = social_sentiment.fetch_recent_posts(name)
            print(f"[{name}] Strike live_price={live_price} | TA: {ta}")
            print(f"[{name}] Nacitanych {len(social)} social prispevkov (spravy hlada Claude sam cez web_search).")
        except Exception as e:
            # Strike/yfinance API vypadok tu predtym zhodil cely cyklus neodchytenou
            # vynimkou - radsej zalogujeme a bezpecne preskocime len tento asset.
            print(f"[{name}] Zber trhovych dat zlyhal, preskakujem cyklus: {e}")
            session.add(CycleLog(
                symbol=symbol,
                config_snapshot=_config_snapshot(asset),
                outcome="error", reject_reason=f"market_data_fetch_failed: {e}",
            ))
            session.commit()
            return

        prev_log = (
            session.query(CycleLog)
            .filter(CycleLog.symbol == symbol, CycleLog.key_assumptions.isnot(None))
            .order_by(CycleLog.created_at.desc())
            .first()
        )
        prev_assumptions = prev_log.key_assumptions if prev_log else None
        prev_cycle_time = prev_log.created_at if prev_log else None

        try:
            decision, web_search_log = claude_analyst.analyze(
                asset, ta, cross_market, market_session, social, btc_proxy,
                prev_assumptions, prev_cycle_time,
            )
        except Exception as e:
            print(f"[{name}] Claude analyza zlyhala, preskakujem cyklus: {e}")
            session.add(CycleLog(
                symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
                session_data=market_session,
                config_snapshot=_config_snapshot(asset),
                outcome="error", reject_reason=str(e),
            ))
            session.commit()
            return
        print(f"[{name}] Claude rozhodnutie: {decision}")
        print(f"[{name}] Web search log: {web_search_log}")

        cycle_log = CycleLog(
            symbol=symbol, live_price=live_price, ta=ta, cross_market=cross_market,
            session_data=market_session,
            config_snapshot=_config_snapshot(asset),
            direction=decision.get("direction"), confidence=decision.get("confidence"),
            stop_loss_price=decision.get("stop_loss_price"), take_profit_price=decision.get("take_profit_price"),
            reasoning=decision.get("reasoning"),
            web_search_log=web_search_log,
            key_assumptions=decision.get("key_assumptions"),
        )

        try:
            sized = risk_manager.validate_and_size(
                decision, has_open_position=False,
                live_price=live_price, market_meta=market_meta,
                min_confidence=asset["min_confidence"], sl_pct=asset["sl_pct"],
                tp_pct=asset["tp_pct"], leverage=asset["leverage"], margin_usd=asset["margin_usd"],
            )
        except risk_manager.RejectedTrade as e:
            print(f"[{name}] Obchod zamietnuty risk managerom: {e}")
            cycle_log.outcome = "rejected"
            cycle_log.reject_reason = str(e)
            session.add(cycle_log)
            session.commit()
            return

        print(f"[{name}] Otvaram {sized['direction']} | leverage={sized['leverage']} "
              f"| size={sized['size']} | notional=${sized['notional_usd']} "
              f"| margin=${sized['margin_usd']} | SL={sized['stop_loss_price']} "
              f"| TP={sized['take_profit_price']} | confidence={sized['confidence']}")

        trade = Trade(
            symbol=symbol,
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
            print(f"[{name}] DRY_RUN=true - obchod sa NEODOSLAL na Strike, iba zalogovany do DB.")
        else:
            try:
                result = strike_client.open_bracket_position(
                    direction=sized["direction"],
                    size=sized["size"],
                    leverage=sized["leverage"],
                    stop_loss_price=sized["stop_loss_price"],
                    take_profit_price=sized["take_profit_price"],
                    symbol=symbol,
                )
            except Exception as e:
                # Otvorenie na Strike zlyhalo uplne (API chyba, nedostatok prostriedkov...) -
                # ziadna pozicia nevznikla, ale nesmieme stratit stopu po tomto pokuse.
                print(f"[{name}] Otvorenie pozicie na Strike zlyhalo: {e}")
                cycle_log.outcome = "error"
                cycle_log.reject_reason = f"open_position_failed: {e}"
                session.add(cycle_log)
                session.commit()
                return

            print(f"[{name}] Strike odpoved: {result}")
            trade.strategy_id = result.get("strategy_id")

            # Bezpecnostna kontrola: ak SL alebo TP noha bracket objednavky zlyhala
            # pripojit sa (Strike ju z nejakeho dovodu odmietol), pozicia by bola
            # nechranena az do dalsieho position_monitor cyklu (az 10 min). Radsej
            # ju hned teraz nudzovo zatvorime, nez by cakala nechranena na burze.
            if not result.get("sl_client_order_id") or not result.get("tp_client_order_id"):
                print(
                    f"[{name}] KRITICKE: chyba sl_client_order_id alebo "
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
                    print(f"[{name}] CHYBA pri nudzovom zatvoreni: {e}")
                    trade.close_reason = f"missing_sl_or_tp_leg_AND_safety_close_failed: {e}"

        session.add(trade)
        session.flush()  # priradi trade.id pred zapisom do cycle_log
        cycle_log.outcome = "opened"
        cycle_log.trade_id = trade.id
        session.add(cycle_log)
        session.commit()
    finally:
        session.close()


def run_all_cycles() -> None:
    """Vstupny bod scheduleru (viz main.py). Fetchne zdielane makro data RAZ
    (cross-market/session + BTC proxy ak treba) a potom prejde kazdy aktivny
    asset z assets.enabled_assets() nezavisle."""
    print(f"\n=== [trade_cycle] {datetime.now(timezone.utc).isoformat()} ===")
    active = assets.enabled_assets()
    if not active:
        print("[trade_cycle] Ziadny aktivny asset (skontroluj ENABLE_NVDA/ENABLE_ADA).")
        return
    print(f"[trade_cycle] Aktivne assety: {[a['name'] for a in active]}")

    try:
        cross_market = market_data.get_cross_market_snapshot()
        market_session = market_data.get_session_snapshot()
        print(f"[trade_cycle] Zdielany cross-market: {cross_market}")
        print(f"[trade_cycle] Zdielany session: {market_session}")
    except Exception as e:
        # zdielany fetch je spolocny vstup pre vsetky assety - ak zlyha, ziaden
        # asset nema na com rozhodovat, radsej preskocime cely tick.
        print(f"[trade_cycle] Zdielany makro fetch zlyhal, preskakujem CELY cyklus: {e}")
        return

    btc_proxy = None
    if any(a.get("needs_btc_proxy") for a in active):
        try:
            btc_proxy = market_data.get_btc_proxy_snapshot()
            print(f"[trade_cycle] BTC proxy (krypto-makro pre ADA): {btc_proxy}")
        except Exception as e:
            print(f"[trade_cycle] BTC proxy fetch zlyhal (pokracujem bez neho): {e}")

    for asset in active:
        try:
            run_cycle_for_asset(
                asset, cross_market, market_session,
                btc_proxy if asset.get("needs_btc_proxy") else None,
            )
        except Exception as e:
            # jeden asset nesmie zhodit ostatne v tom istom cykle
            print(f"[trade_cycle] [{asset['name']}] neocakavana chyba, pokracujem dalsim assetom: {e}")


if __name__ == "__main__":
    run_all_cycles()
