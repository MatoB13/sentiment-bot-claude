"""Periodicka kontrola otvorenych pozicii (naprieč vsetkymi assetmi - NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX)
- zaznamenanie zatvorenia a PnL."""
from datetime import datetime, timezone

import assets
import config
import discord_client
import risk_overrides
import strike_client
import trade_cycle
import watch_monitor
from db import AtrCalibration, SlTpBacktestCandidate, SrCalibration, Trade, get_session

# /v2/closedPositions (povodny zdroj) nema ZIADNE price/PnL/fee polia - viz
# memory strike_api_integration_lessons. Presne udaje sa daju ziskat len cez
# /v2/history/order (ktora BRACKET noha - stop/take_profit_limit - sa realne
# vykonala) + /v2/history/fill (skutocna cena/poplatok/realized_pnl kazdeho
# fillu) - viz docs.strikefinance.org/api/trade/history, overene 2026-07-30
# naozivo voci prvemu realnemu obchodu.
_CLOSE_REASON_BY_TYPE = {"stop": "stop_loss", "take_profit_limit": "take_profit", "take_profit": "take_profit"}

# TP a nase vlastne force-close (timeout ALEBO manualny kill-switch - obe idu
# cez rovnaky cancel_all_orders+close_position_market mechanizmus, preto sa
# "force_closed_by_bot" tyka oboch) su podnetom na okamzity "co teraz" cyklus,
# ktory MOZE otvorit novu poziciu.
#
# SL a likvidacia (2026-08-18, na ziadost pouzivatela - predtym VEDOME
# vynechane, viz stara verzia tohto komentara nizsie) TIEZ TERAZ spustaju
# review - ale VYHRADNE na vyhodnotenie (viz _EVALUATION_ONLY_CLOSE_REASONS
# nizsie a trade_cycle.run_cycle_for_asset). Claude dostane closed_trade_reflection
# kontext a zapise sa do retrospektivy (hodnota do uciaceho loopu), ale
# vysledny smer/confidence z TOHTO konkretneho behu sa NIKDY nepouzije na
# otvorenie novej pozicie - blokovane priamo v kode (trade_cycle), nie len
# promptovou instrukciou. Povodny dovod vylucenia SL (revenge-trading riziko
# okamziteho re-entry) je takto vyriesenej - bot moze znova vstupit len pri
# najblizsom BEZNOM cykle, nikdy ako priama reakcia na stop-out.
_TRIGGER_REVIEW_REASONS = {"take_profit", "force_closed_by_bot", "manual_kill_switch",
                            "stop_loss", "liquidation"}

# Podmnozina vyssie - tieto dovody spustaju review LEN na vyhodnotenie (viz
# _build_closed_trade_context nizsie, ktora tento flag vlozi do closed_trade
# dictu pre trade_cycle.py).
_EVALUATION_ONLY_CLOSE_REASONS = {"stop_loss", "liquidation"}

# Discord notifikacia o zatvoreni (2026-08-15, na ziadost pouzivatela) - TP/SL/
# likvidacia/timeout, ale ZAMERNE NIE manual_kill_switch (to uz pouzivatel
# vyvolal sam, netreba mu to pripominat). Nezavisle od _TRIGGER_REVIEW_REASONS
# vyssie - iny ucel, iny filter (SL/likvidacia tu SU zahrnute, hoci review ich
# vedome vynechava).
_NOTIFY_CLOSE_REASONS = {"take_profit", "stop_loss", "liquidation", "force_closed_by_bot"}


def _sum_fills(fills: list[dict], order_id) -> dict | None:
    """Velkostou-vazeny priemer ceny + sucet poplatku/realized_pnl VSETKYCH
    fills patriacich danej objednavke (moze sa vykonat po castiach)."""
    matched = [f for f in fills if f.get("order_id") == order_id]
    total_size = sum(float(f["size"]) for f in matched)
    if total_size <= 0:
        return None
    avg_price = sum(float(f["size"]) * float(f["price"]) for f in matched) / total_size
    total_fee = sum(float(f.get("fee") or 0) for f in matched)
    total_realized_pnl = sum(float(f.get("realized_pnl") or 0) for f in matched)
    return {"avg_price": avg_price, "fee": total_fee, "realized_pnl": total_realized_pnl}


def _lookup_exact_close(trade: Trade) -> dict | None:
    """Presne (NIE odhadovane) udaje o zatvoreni priamo z burzy. Vrati
    {close_reason, entry_fill_price, close_fill_price, fees_usd, pnl_usd}
    alebo None ak sa este nepodarilo najst (napr. Strike este nestihol
    zaindexovat fill tesne po zatvoreni) - volajuci v tom pripade necha
    tieto polia prazdne a _backfill_missing_exact_data() to skusi znova
    pri buducom behu, kym sa nepodari."""
    closed_at = trade.closed_at
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)
    opened_at = trade.opened_at
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)

    start_ms = int(opened_at.timestamp() * 1000) - 60_000
    end_ms = int(closed_at.timestamp() * 1000) + 120_000

    try:
        orders = strike_client.get_order_history(trade.symbol, start_ms=start_ms, end_ms=end_ms, limit=100)
        fills = strike_client.get_fill_history(trade.symbol, start_ms=start_ms, end_ms=end_ms, limit=200)
    except Exception as e:
        print(f"[position_monitor] Trade {trade.id}: nepodarilo sa nacitat order/fill history: {e}")
        return None

    strategy_orders = [o for o in orders if o.get("strategy_id") == trade.strategy_id]
    entry_order = next((o for o in strategy_orders if o.get("is_primary")), None)
    bracket_legs = [o for o in strategy_orders if not o.get("is_primary")]
    filled_leg = next((o for o in bracket_legs if o.get("status") == "filled"), None)

    close_reason = None
    closing_order_id = None
    if filled_leg is not None:
        close_reason = _CLOSE_REASON_BY_TYPE.get(filled_leg.get("type"), filled_leg.get("type"))
        closing_order_id = filled_leg.get("id")
    else:
        # Ani jedna nasa TP/SL bracket noha sa nevykonala - bud sme poziciu
        # zatvorili sami (timeout force-close cez close_position_market, ktora
        # posiela SAMOSTATNU objednavku BEZ strategy_id), alebo isla do
        # likvidacie (burzou generovana objednavka, tiez bez naseho
        # strategy_id). Najdeme ju sirsim hladanim v ramci toho isteho
        # symbolu/okna: opacna strana od vstupu, reduce_only, filled,
        # najneskorsia v okne (najblizsie k realnemu zatvoreniu).
        entry_side = entry_order.get("side") if entry_order else None
        close_side = "sell" if entry_side == "buy" else "buy" if entry_side == "sell" else None
        bracket_ids = {leg.get("id") for leg in bracket_legs}
        candidates = [
            o for o in orders
            if o.get("status") == "filled" and o.get("reduce_only")
            and (close_side is None or o.get("side") == close_side)
            and o.get("id") not in bracket_ids
        ]
        if candidates:
            closing = max(candidates, key=lambda o: o.get("event_timestamp") or 0)
            closing_order_id = closing.get("id")
            close_reason = "liquidation" if closing.get("auto_close_type") else "force_closed_by_bot"
        elif entry_order is not None:
            # 2026-08-18 produkcny nalez: /v2/history/order niekedy STALE (aj po
            # 9+ opakovanych pokusoch cez viac minut) nevrati zatvaraciu
            # objednavku, hoci jej FILLS uz su v /v2/history/fill indexovane
            # (overene naozivo - XAU timeout close, fill.order_id vobec nebol
            # medzi vratenymi orders). Bez tejto vetvy close_reason ostane
            # navzdy na surovom fallback stringu volajuceho (napr.
            # "max_hold_24.0h_reached"), ktory NESEDI so ziadnou hodnotou v
            # _TRIGGER_REVIEW_REASONS/_NOTIFY_CLOSE_REASONS - review aj
            # notifikacia by sa tak NIKDY nespustili. Rovnaka logika ako vyssie
            # (opacna strana, najneskorsi fill), len zdroj su rovno fills, nie
            # orders - fill.order_id je vsetko, co _sum_fills nizsie potrebuje.
            entry_order_id = entry_order.get("id")
            fill_candidates = [
                f for f in fills
                if f.get("order_id") != entry_order_id
                and (close_side is None or f.get("side") == close_side)
            ]
            if fill_candidates:
                closing_fill = max(fill_candidates, key=lambda f: f.get("timestamp") or 0)
                closing_order_id = closing_fill.get("order_id")
                close_reason = "liquidation" if closing_fill.get("auto_close_type") else "force_closed_by_bot"

    if entry_order is None or closing_order_id is None:
        return None

    entry_agg = _sum_fills(fills, entry_order.get("id"))
    close_agg = _sum_fills(fills, closing_order_id)
    if entry_agg is None or close_agg is None:
        return None

    fees_usd = entry_agg["fee"] + close_agg["fee"]
    return {
        "close_reason": close_reason,
        "entry_fill_price": entry_agg["avg_price"],
        "close_fill_price": close_agg["avg_price"],
        "fees_usd": fees_usd,
        "pnl_usd": close_agg["realized_pnl"] - fees_usd,  # realized_pnl je hruby, bez poplatkov
    }


def _apply_exact_close(trade: Trade, fallback_close_reason: str) -> None:
    """Skusi doplnit presne udaje; ak sa nepodari (napr. burza este
    neindexovala fill), necha PnL polia prazdne - _backfill_missing_exact_data
    to skusi znova pri buducom behu namiesto toho, aby sme sa navzdy vzdali."""
    exact = _lookup_exact_close(trade)
    if exact:
        # manual_kill_switch je zname so 100% istotou uz v momente zatvorenia
        # (viz watch_monitor._check_manual_close_requests) - burza vidi len
        # "nasa reduce_only objednavka mimo TP/SL bracket nôh", nevie odlisit
        # manualny kill-switch od timeout force-close, takze by ho tu
        # prepisala na menej presne (a zavadzajuce) "force_closed_by_bot".
        if trade.close_reason != "manual_kill_switch":
            trade.close_reason = exact["close_reason"]
        trade.entry_fill_price = exact["entry_fill_price"]
        trade.close_fill_price = exact["close_fill_price"]
        trade.fees_usd = exact["fees_usd"]
        trade.pnl_usd = exact["pnl_usd"]
        print(f"[position_monitor] Trade {trade.id}: presne udaje najdene "
              f"(dovod={exact['close_reason']}, pnl=${exact['pnl_usd']:.2f}).")
    else:
        trade.close_reason = fallback_close_reason
        print(f"[position_monitor] Trade {trade.id}: presne udaje zatial nedostupne, "
              f"skusim znova pri buducom behu.")


def _build_closed_trade_context(trade: Trade) -> dict:
    """Plain dict (nie ORM objekt) so vsetkym, co claude_analyst._build_user_prompt
    potrebuje na popis prave zatvorenej pozicie - extrahovane HNED, kym je
    session este ziva (viz _fire_post_close_reviews, ktora bezi az PO
    session.close()).

    evaluation_only (2026-08-18): True pre SL/likvidaciu - trade_cycle.
    run_cycle_for_asset toto pole precita a po Claude volani STRUKTURALNE
    preskoci risk_manager/otvorenie novej pozicie, bez ohladu na to, aky smer/
    confidence Claude v tomto behu navrhol (viz _EVALUATION_ONLY_CLOSE_REASONS
    vyssie)."""
    opened_at = trade.opened_at
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    closed_at = trade.closed_at
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)
    return {
        "trade_id": trade.id,
        "direction": trade.direction,
        "entry_price": trade.entry_fill_price,
        "exit_price": trade.close_fill_price,
        "hours_held": (closed_at - opened_at).total_seconds() / 3600,
        "pnl_usd": trade.pnl_usd,
        "close_reason": trade.close_reason,
        "evaluation_only": trade.close_reason in _EVALUATION_ONLY_CLOSE_REASONS,
    }


def _build_review_context(trade: Trade, session, asset: dict) -> dict:
    """Rozsiruje _build_closed_trade_context (2026-08-19, na ziadost pouzivatela)
    o SL/TP+kalibracia+historia kontext - POUZE pre post-close review (Discord
    notifikacia si vystaci so zakladnym _build_closed_trade_context, netreba
    pre nu tieto navyse DB dotazy).

    Cely blok je zabaleny v try/except - ak nieco zlyha (napr. tento ticker
    este nema ziadny kalibracny prepocet), review sa aj tak spusti so
    zakladnym kontextom namiesto toho, aby padol celkom."""
    ctx = _build_closed_trade_context(trade)
    try:
        entry = trade.entry_price
        if entry and trade.stop_loss_price is not None:
            ctx["sl_pct_chosen"] = abs(entry - trade.stop_loss_price) / entry * 100
        if entry and trade.take_profit_price is not None:
            ctx["tp_pct_chosen"] = abs(trade.take_profit_price - entry) / entry * 100

        default_sl, default_tp = risk_overrides.get_effective_sl_tp(session, asset)
        ctx["default_sl_pct"] = default_sl
        ctx["default_tp_pct"] = default_tp

        prior = (
            session.query(Trade)
            .filter(Trade.symbol == trade.symbol, Trade.id != trade.id, Trade.pnl_usd.isnot(None))
            .order_by(Trade.closed_at.desc())
            .limit(5)
            .all()
        )
        history = []
        for pt in prior:
            pt_entry = pt.entry_price
            history.append({
                "pnl_usd": pt.pnl_usd,
                "close_reason": pt.close_reason,
                "sl_pct": abs(pt_entry - pt.stop_loss_price) / pt_entry * 100
                          if pt_entry and pt.stop_loss_price is not None else None,
                "tp_pct": abs(pt.take_profit_price - pt_entry) / pt_entry * 100
                          if pt_entry and pt.take_profit_price is not None else None,
            })
        ctx["history"] = history

        atr_calib = (
            session.query(AtrCalibration)
            .filter(AtrCalibration.symbol == trade.symbol)
            .order_by(AtrCalibration.computed_at.desc())
            .first()
        )
        atr_pct = atr_calib.atr_pct if atr_calib else None
        sr_by_rank = {
            r.rank: r for r in session.query(SrCalibration).filter(SrCalibration.symbol == trade.symbol).all()
        }
        candidates = []
        for c in (
            session.query(SlTpBacktestCandidate)
            .filter(SlTpBacktestCandidate.symbol == trade.symbol)
            .order_by(SlTpBacktestCandidate.rank)
            .all()
        ):
            entry_row = {
                "rank": c.rank, "sl_k": c.sl_k, "tp_k": c.tp_k,
                "total_pnl": c.total_pnl, "win_rate": c.win_rate, "trade_count": c.trade_count,
                "atr_sl_pct": (c.sl_k * atr_pct) if atr_pct else None,
                "atr_tp_pct": (c.tp_k * atr_pct) if atr_pct else None,
                "sr_total_pnl": c.sr_total_pnl, "sr_win_rate": c.sr_win_rate,
            }
            sr = sr_by_rank.get(c.rank)
            if sr:
                entry_row["sr_sl_pct"] = sr.sl_pct
                entry_row["sr_tp_pct"] = sr.tp_pct
            candidates.append(entry_row)
        ctx["calibration_candidates"] = candidates
    except Exception as e:
        print(f"[position_monitor] Trade {trade.id}: nepodarilo sa zostavit SL/TP kalibracny "
              f"kontext pre review (neblokujuce, review pobezi bez neho): {e}")
    return ctx


def _check_and_queue_review(trade: Trade, session, pending_reviews: list) -> None:
    """Po _apply_exact_close over, ci uz je close_reason VYRIESENY (nie surovy
    fallback text - viz _apply_exact_close) a ci patri medzi _TRIGGER_REVIEW_REASONS.
    Nastavi post_close_review_triggered_at HNED (v tej istej transakcii ako
    close_reason), aby sa pri dalsom tiku uz neopakovalo, aj keby samotny
    mimoriadny cyklus neskor zlyhal (radsej vynechame jeden review nez
    spamovali opakovane volania pri kazdom pollovacom tiku)."""
    if trade.close_reason not in _TRIGGER_REVIEW_REASONS or trade.post_close_review_triggered_at is not None:
        return
    asset = assets.by_symbol(trade.symbol)
    if asset is None:
        return
    trade.post_close_review_triggered_at = datetime.now(timezone.utc)
    pending_reviews.append((asset, _build_review_context(trade, session, asset)))


def _fire_post_close_reviews(pending_reviews: list) -> None:
    """Bezi AZ PO session.close() (viz check_open_trades) - trade_cycle.dispatch_triggered_check
    otvara vlastnu nezavislu session, netreba (ani nesmieme) zdielat tu s uz
    zatvorenou position_monitor session. NA POZADI (viz jej docstring) - inak
    by pomaly review jedneho zatvoreneho obchodu blokoval kontrolu OSTATNYCH
    (stale otvorenych) pozicii na dalsom tiku."""
    for asset, closed_trade in pending_reviews:
        mode = "len vyhodnotenie, bez noveho obchodu" if closed_trade.get("evaluation_only") else "plny cyklus"
        print(f"[position_monitor] [{asset['name']}] post-close review ({mode}, "
              f"dovod={closed_trade['close_reason']}, pnl=${closed_trade['pnl_usd']:.2f}).")
        trade_cycle.dispatch_triggered_check(asset, closed_trade=closed_trade)


def _check_and_queue_close_notification(trade: Trade, pending_notifications: list) -> None:
    """Rovnaky vzor ako _check_and_queue_review vyssie, len iny filter dovodov
    (_NOTIFY_CLOSE_REASONS) a vlastny dedup stlpec (close_notified_at) -
    nezavisle od review-triggeru, aby sa dali nezavisle zapinat/vypinat."""
    if trade.pnl_usd is None or trade.close_notified_at is not None:
        return
    if trade.close_reason not in _NOTIFY_CLOSE_REASONS:
        return
    trade.close_notified_at = datetime.now(timezone.utc)
    pending_notifications.append((trade.symbol, _build_closed_trade_context(trade)))


def _fire_close_notifications(pending_notifications: list) -> None:
    """Bezi AZ PO session.close(), rovnaky dovod ako _fire_post_close_reviews -
    hoci Discord notifikacia sama o sebe DB nepotrebuje, drzime rovnaky vzor
    pre konzistentnost a aby sa nikdy necitalo z uz odpojeneho ORM objektu."""
    for symbol, closed_trade in pending_notifications:
        try:
            discord_client.notify_trade_closed(symbol, closed_trade)
        except Exception as e:
            print(f"[position_monitor] [{symbol}] Discord notifikacia o zatvoreni zlyhala: {e}")


def _backfill_missing_exact_data(session, pending_reviews: list, pending_notifications: list) -> None:
    """Self-healing retry: obchody, ktore sa uz zatvorili, ale minule
    _lookup_exact_close nenasiel data (burza este neindexovala fill) - skusi
    znova. Nedotyka sa hlavnej trading logiky, len doplna historicke udaje."""
    pending = session.query(Trade).filter(
        Trade.status.in_(["closed_by_exchange", "closed_by_timeout", "closed_by_safety", "closed_by_user"]),
        Trade.pnl_usd.is_(None),
    ).all()
    if not pending:
        return
    print(f"[position_monitor] Dohladavam presne udaje pre {len(pending)} skorsie zatvorenych obchodov...")
    for trade in pending:
        _apply_exact_close(trade, trade.close_reason)
        session.add(trade)
        _check_and_queue_review(trade, session, pending_reviews)
        _check_and_queue_close_notification(trade, pending_notifications)


def check_open_trades():
    print(f"\n=== [position_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    pending_reviews: list = []
    pending_notifications: list = []
    try:
        _backfill_missing_exact_data(session, pending_reviews, pending_notifications)

        open_trades = session.query(Trade).filter(Trade.status == "open").all()
        if not open_trades:
            print("[position_monitor] Ziadne otvorene pozicie.")
            session.commit()
            session.close()
            _fire_post_close_reviews(pending_reviews)
            _fire_close_notifications(pending_notifications)
            return

        # Bez symbol filtra - vsetky otvorene pozicie na ucte v JEDNOM volani,
        # zdielanom pre vsetky sledovane assety (NAS100/NVDA/ADA), namiesto
        # samostatneho volania na kazdy symbol zvlast.
        live_positions = strike_client.get_positions()
        live_by_symbol = {p.get("symbol"): p for p in live_positions}

        now = datetime.now(timezone.utc)
        for trade in open_trades:
            try:
                # Bot drzi vzdy najviac 1 poziciu naraz (viz has_open_position v trade_cycle.py),
                # takze zhoda podla symbolu je jednoznacna.
                live = live_by_symbol.get(trade.symbol)

                if live is None:
                    # uz nie je medzi otvorenymi poziciami na burze -> zatvorena (TP/SL/likvidacia)
                    trade.status = "closed_by_exchange"
                    trade.closed_at = now
                    _apply_exact_close(trade, "not_found_in_open_positions (TP/SL/liquidation)")
                    session.add(trade)
                    _check_and_queue_review(trade, session, pending_reviews)
                    _check_and_queue_close_notification(trade, pending_notifications)
                    # 2026-08-16 na ziadost pouzivatela ("mela" po zatvoreni pri
                    # prudkom pohybe) - VSETKY dovody zatvorenia, SL/likvidacia
                    # najviac zo vsetkych (viz watch_monitor.mark_hot docstring).
                    watch_monitor.mark_hot(trade.symbol)
                    continue

                expires_at = trade.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)

                if now >= expires_at:
                    print(f"[position_monitor] Trade {trade.id} presiahol {config.POSITION_MAX_HOURS}h, zatvaram.")
                    try:
                        strike_client.cancel_all_orders(trade.symbol)  # zrusi visiace TP/SL objednavky
                        strike_client.close_position_market(trade.direction, float(live["size"]), trade.symbol)
                    except Exception as e:
                        print(f"[position_monitor] Chyba pri force-close: {e}")
                        continue
                    trade.status = "closed_by_timeout"
                    trade.closed_at = now
                    _apply_exact_close(trade, f"max_hold_{config.POSITION_MAX_HOURS}h_reached")
                    session.add(trade)
                    _check_and_queue_review(trade, session, pending_reviews)
                    _check_and_queue_close_notification(trade, pending_notifications)
                    watch_monitor.mark_hot(trade.symbol)
                else:
                    print(f"[position_monitor] Trade {trade.id} stale otvoreny "
                          f"(expiruje {expires_at.isoformat()}).")
            except Exception as e:
                # 2026-08-16 stress-test nalez: bez tejto izolacie by neocakavana
                # vynimka pri SPRACOVANI JEDNEHO obchodu (napr. nezvycajny tvar
                # odpovede z burzy pocas prudkeho pohybu trhu) preskocila kontrolu
                # VSETKYCH dalsich otvorenych pozicii v tomto tiku (najblizsi pokus
                # az o MONITOR_INTERVAL_MINUTES neskor) - rovnaky princip izolacie
                # "jeden asset nesmie blokovat ostatne" ako inde v tomto module
                # (viz run_cycle_for_asset vlastna DB session, dispatch_triggered_check).
                # Realne SL/TP ochranu tejto pozicie to neovplyvni - tá zije na
                # burze ako samostatna objednavka nezavisla od tohto monitoringu.
                print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: neocakavana chyba "
                      f"pri spracovani, preskakujem na dalsi obchod: {e}")
                continue

        session.commit()
    finally:
        session.close()

    # AZ PO session.close() - viz _fire_post_close_reviews docstring.
    _fire_post_close_reviews(pending_reviews)
    _fire_close_notifications(pending_notifications)


if __name__ == "__main__":
    check_open_trades()
