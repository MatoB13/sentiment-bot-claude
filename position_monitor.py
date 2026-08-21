"""Periodicka kontrola otvorenych pozicii (naprieč vsetkymi assetmi - NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX)
- zaznamenanie zatvorenia a PnL."""
from datetime import datetime, timedelta, timezone

import assets
import config
import discord_client
import risk_overrides
import sl_grid_backtest
import strike_client
import trade_cycle
import watch_monitor
from db import AtrCalibration, SlTpBacktestCandidate, Trade, get_session

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
                            "stop_loss", "liquidation", "ai_early_close"}

# Podmnozina vyssie - tieto dovody spustaju review LEN na vyhodnotenie (viz
# _build_closed_trade_context nizsie, ktora tento flag vlozi do closed_trade
# dictu pre trade_cycle.py). ai_early_close (2026-08-21, viz
# trade_cycle._maybe_ai_early_close) je tu z rovnakeho dovodu ako SL/likvidacia -
# vyhne sa impulzivnemu okamzitemu re-entry priamo po tom, co bot sam proaktivne
# usudil, ze povodna teza je vyvratena.
_EVALUATION_ONLY_CLOSE_REASONS = {"stop_loss", "liquidation", "ai_early_close"}

# Discord notifikacia o zatvoreni (2026-08-15, na ziadost pouzivatela) - TP/SL/
# likvidacia/timeout, ale ZAMERNE NIE manual_kill_switch (to uz pouzivatel
# vyvolal sam, netreba mu to pripominat). Nezavisle od _TRIGGER_REVIEW_REASONS
# vyssie - iny ucel, iny filter (SL/likvidacia tu SU zahrnute, hoci review ich
# vedome vynechava).
_NOTIFY_CLOSE_REASONS = {"take_profit", "stop_loss", "liquidation", "force_closed_by_bot", "ai_early_close"}

# Kolko minut NAVYSE po POSITION_MAX_HOURS sa cakalo pred SL/TP grid-search
# prepoctom (2026-08-19, na ziadost pouzivatela) - vid _check_and_queue_recompute
# nizsie pre plne zdovodnenie (garancia kompletnej 24h cenovej historie).
_RECOMPUTE_DELAY_BUFFER_MINUTES = 11


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


# Tolerancia pri porovnavani realnej ceny zatvorenia s SL/TP urovnou pozicie
# (viz _reclassify_by_close_price) - stop-market objednavky mozu preklznut
# cez SL uroven, limit TP objednavky sa naopak vykonaju AT-ALEBO-LEPSIE nez
# zadana cena, takze male percento tolerancie pokryje bezny slippage bez
# rizika falosnej zhody so vzdialenejsou SL/TP urovnou druhej strany.
_CLOSE_PRICE_TOLERANCE = 0.003


def _reclassify_by_close_price(trade: Trade, close_price: float) -> str | None:
    """Ak nasa TP/SL bracket noha chyba v /v2/history/order (staleness, viz
    volajuci), skusi urcit skutocny dovod zatvorenia podla toho, ci sa realna
    cena zatvorenia zhoduje s TP alebo SL urovnou TEJTO pozicie (s malou
    tolerantnostou na slippage/zaokruhlenie). Vrati None, ak sa nezhoduje so
    ziadnou z nich (teda ide skutocne o timeout/manualne zatvorenie mimo TP/SL)."""
    sl, tp = trade.stop_loss_price, trade.take_profit_price
    is_long = (trade.direction or "").lower() == "long"
    if tp is not None:
        tp_hit = close_price >= tp * (1 - _CLOSE_PRICE_TOLERANCE) if is_long \
            else close_price <= tp * (1 + _CLOSE_PRICE_TOLERANCE)
        if tp_hit:
            return "take_profit"
    if sl is not None:
        sl_hit = close_price <= sl * (1 + _CLOSE_PRICE_TOLERANCE) if is_long \
            else close_price >= sl * (1 - _CLOSE_PRICE_TOLERANCE)
        if sl_hit:
            return "stop_loss"
    return None


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

    # 2026-08-19 produkcny nalez (ZEC trade #46, "timeout" flag na zjavnom TP
    # hite): rovnaky /v2/history/order staleness problem ako 2026-08-18
    # komentar vyssie (XAU) sa moze tykat aj samotnej TP/SL bracket nohy, nie
    # len timeout/likvidacnej objednavky - potom ju fallback vyssie najde cez
    # siroke hladanie a OMYLOM oznaci "force_closed_by_bot", hoci realna
    # cena zatvorenia je zjavne pri TP/SL urovni tejto pozicie. Preto tu, LEN
    # pre tento neisty fallback (nie pre uz spolahlivo urcenu "liquidation"),
    # este dorefinujeme podla skutocnej ceny zatvorenia - ak sedi na TP/SL
    # (s malou tolerantnostou na slippage), pouzijeme presnejsi dovod.
    if close_reason == "force_closed_by_bot":
        close_reason = _reclassify_by_close_price(trade, close_agg["avg_price"]) or close_reason

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
        # manual_kill_switch/ai_early_close su zname so 100% istotou uz v
        # momente zatvorenia (viz watch_monitor._check_manual_close_requests
        # a trade_cycle._maybe_ai_early_close) - burza vidi len "nasa
        # reduce_only objednavka mimo TP/SL bracket nôh", nevie ich odlisit od
        # obycajneho timeout force-close, takze by ich tu prepisala na menej
        # presne (a pre ai_early_close zavadzajuce - vyzeralo by to ako
        # mechanicky POSITION_MAX_HOURS timeout, nie ako Claudeho vlastne
        # rozhodnutie) "force_closed_by_bot".
        if trade.close_reason not in ("manual_kill_switch", "ai_early_close"):
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
        candidates = []
        for c in (
            session.query(SlTpBacktestCandidate)
            .filter(SlTpBacktestCandidate.symbol == trade.symbol)
            .order_by(SlTpBacktestCandidate.rank)
            .all()
        ):
            candidates.append({
                "rank": c.rank, "sl_k": c.sl_k, "tp_k": c.tp_k,
                "total_pnl": c.total_pnl, "win_rate": c.win_rate, "trade_count": c.trade_count,
                "atr_sl_pct": (c.sl_k * atr_pct) if atr_pct else None,
                "atr_tp_pct": (c.tp_k * atr_pct) if atr_pct else None,
            })
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


def _check_and_queue_recompute(trade: Trade) -> None:
    """NAPLANUJE (neodpali hned) event-driven SL/TP grid-search prepocet pre
    tento ticker (2026-08-19, na ziadost pouzivatela - nahradza povodny 24h
    scheduler job, ktory prepocitaval VSETKY tickery denne bez ohladu na to,
    ci mali novy obchod).

    DOLEZITE: prepocet sa NEODPALI HNED pri zatvoreni, ale az o
    POSITION_MAX_HOURS + _RECOMPUTE_DELAY_BUFFER_MINUTES OD OTVORENIA tohto
    obchodu (nie od zatvorenia). Dovod: ak sa obchod zatvoril SKOR (napr. SL
    po 2h), grid search skusa aj SIRSIE hypoteticke SL/TP kombinacie, ktore
    tento konkretny obchod nikdy nepouzil - tie potrebuju cenovu historiu AZ
    DO KONCA 24h okna, inak by simulacia pre kazdu takuto sirsiu kombinaciu
    ticho pouzila len neuplnu cast historie (viz sl_grid_backtest._prepare_trade
    guard, ktory takyto pripad aj tak este raz odmietne ako poistku).
    _fire_due_recomputes nizsie potom kazdy tik kontroluje, ci uz tento cas
    nastal."""
    if trade.recompute_due_at is not None:
        return
    opened_at = trade.opened_at
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    trade.recompute_due_at = opened_at + timedelta(hours=config.POSITION_MAX_HOURS,
                                                     minutes=_RECOMPUTE_DELAY_BUFFER_MINUTES)


def _fire_due_recomputes(session, pending_recompute: set) -> None:
    """Vola sa na KAZDOM position_monitor tiku (nezavisle od toho, ci prave
    teraz nieco zatvara) - najde vsetky obchody, ktorych naplanovany
    recompute_due_at (viz _check_and_queue_recompute) uz nastal a este
    nebol spracovany, a prida ich symbol do frontu. Viac obchodov toho
    isteho tickera naraz splatnych sa zluci do jedneho volania
    recompute_symbol() (ten aj tak prepocitava VSETKY uzavrete obchody
    tickera, nie len ten jeden, co due_at spustil)."""
    now = datetime.now(timezone.utc)
    due = session.query(Trade).filter(
        Trade.recompute_due_at.isnot(None),
        Trade.recompute_due_at <= now,
        Trade.post_close_recompute_triggered_at.is_(None),
    ).all()
    for trade in due:
        trade.post_close_recompute_triggered_at = now
        pending_recompute.add(trade.symbol)


def _fire_recomputes(symbols: set) -> None:
    """Bezi AZ PO session.close(), rovnaky dovod ako _fire_post_close_reviews -
    sl_grid_backtest.recompute_symbol otvara vlastnu nezavislu session. Kazdy
    symbol izolovane (jeden zlyhany prepocet neblokuje ostatne)."""
    for symbol in symbols:
        try:
            sl_grid_backtest.recompute_symbol(symbol)
        except Exception as e:
            print(f"[position_monitor] [{symbol}] SL/TP grid-search prepocet zlyhal (neblokujuce): {e}")


def _backfill_missing_exact_data(session, pending_reviews: list, pending_notifications: list) -> None:
    """Self-healing retry: obchody, ktore sa uz zatvorili, ale minule
    _lookup_exact_close nenasiel data (burza este neindexovala fill) - skusi
    znova. Nedotyka sa hlavnej trading logiky, len doplna historicke udaje.

    Toto je zaroven JEDINY spolocny bod pre VSETKY sposoby uzavretia obchodu
    (kill-switch cez watch_monitor.py, safety-net cez trade_cycle.py aj
    obycajne TP/SL/timeout tu nizsie) - preto je to (spolu s dvoma inline
    vetvami v check_open_trades()) spravne miesto na _check_and_queue_recompute."""
    pending = session.query(Trade).filter(
        Trade.status.in_(["closed_by_exchange", "closed_by_timeout", "closed_by_safety",
                           "closed_by_user", "closed_by_ai"]),
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
        _check_and_queue_recompute(trade)


# Relativna tolerancia pri porovnavani nevyplnenej velkosti SL objednavky
# voci zivej velkosti pozicie (2026-08-21, ZEC nalez - viz nizsie) - male
# zaokruhlovacie/step-size odchylky (burza kazdy symbol zaokruhluje na vlastny
# order_*_step_size) nesmu spustat zbytocne prekreslovanie pri kazdom tiku.
_SL_SIZE_TOLERANCE = 0.02


def _check_and_reheal_bracket_legs(trade: Trade, live: dict) -> None:
    """2026-08-21 (na ziadost pouzivatela, po ADA incidente) - zivy SL objednavky
    otvorenej ADA pozicie sa na burzi sama "expirovala" (status="expired",
    close_reason="order_strategy_secondary_oco") BEZ vyplnenia, cim ostala
    pozicia docasne nechranena zdola - potvrdene, ze ani jeden z troch
    cancel_all_orders volani v tomto kode (kill-switch/timeout/emergency-close-
    at-open) sa nespustil, teda anomalia je na strane burzy, nie naseho kodu.

    Kazdy tik (1x/min) overi cez get_open_orders (LIVE stav, nie historicky
    log), ci je TP noha stale medzi zivymi objednavkami. TP svoju vyplnenu
    cast sleduje SAMA (Filled pole tej istej objednavky), takze jej
    "outstanding" (Size-Filled) prirodzene kopiruje scvrkavajucu sa ziva
    poziciu bez naseho zasahu - kontrolujeme len PRITOMNOST, nie velkost.

    SL noha je naopak SAMOSTATNA, este nevyplnena objednavka - jej Size
    ostava "zamrznuty" na hodnote z okamihu vytvorenia/poslednej opravy. Ak
    TP medzitym dalej CIASTOCNE plni (2026-08-21, ZEC nalez: TP sa vykonava
    postupne po castiach, kazdy ciastocny fill mohol znova spustit tu istu
    burzovu anomaliu a zrusit SL), stara SL sa vzdy LEN preddimenzuje voci
    aktualnej zivej pozicii (bezpecne vdaka reduce_only - nikdy sa nevyplni
    na viac nez skutocne otvorenu poziciu, teda nikdy nie poddimenzuje, lebo
    pozicia moze len klesat cez TP fill, nikdy rast). Pre presnost ju napriek
    tomu prepocitame, ak sa odchylka od zivej velkosti stane vyznamna.

    Ak treba prekreslit HOCIKTORU nohu (chybajuca, alebo SL so zastaralou
    velkostou), cancel_all_orders (jediny dostupny nastroj - ziadne single-
    order cancel v strike_client) zrusi OBE a znovu sa nastavia OBE na
    aktualnu zivu velkost - zarucuje konzistentny vysledny stav bez ohladu
    na to, ktora noha bola povodne zla. NIKDY nezatvara ani inak nemeni
    samotnu poziciu, len doplna/opravuje chranu."""
    try:
        open_orders = strike_client.get_open_orders(trade.symbol)
    except Exception as e:
        print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: nepodarilo sa "
              f"nacitat openOrders na kontrolu bracket noh (skusim znova o {config.WATCH_INTERVAL_MINUTES} min): {e}")
        return

    live_orders = [o for o in open_orders if o.get("Status") in ("open", "untriggered")]
    tp_order = next((o for o in live_orders
                      if o.get("OriginType") == "take_profit_limit" or o.get("Type") == "take_profit_limit"),
                     None)
    sl_order = next((o for o in live_orders if o.get("Type") == "stop"), None)

    live_size = float(live["size"])
    sl_outstanding = (
        float(sl_order.get("Size") or 0) - float(sl_order.get("Filled") or 0)
        if sl_order is not None else None
    )
    sl_stale = sl_outstanding is not None and live_size > 0 and \
        abs(sl_outstanding - live_size) > max(live_size * _SL_SIZE_TOLERANCE, 1e-9)

    tp_missing = tp_order is None and trade.take_profit_price is not None
    sl_needs_fix = (sl_order is None or sl_stale) and trade.stop_loss_price is not None
    if not tp_missing and not sl_needs_fix:
        return

    reason = []
    if tp_missing:
        reason.append("chyba TP noha")
    if sl_order is None:
        reason.append("chyba SL noha")
    elif sl_stale:
        reason.append(f"SL velkost zastarala ({sl_outstanding} vs ziva pozicia {live_size})")
    print(f"[position_monitor] KRITICKE: Trade {trade.id} [{trade.symbol}]: {', '.join(reason)} - "
          f"prekreslujem obe nohy na aktualnu velkost {live_size}.")

    try:
        strike_client.cancel_all_orders(trade.symbol)
    except Exception as e:
        print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: cancel_all_orders pred "
              f"obnovou bracket noh zlyhalo (skusim znova o {config.WATCH_INTERVAL_MINUTES} min): {e}")
        return

    close_side = "sell" if trade.direction == "Long" else "buy"

    if trade.take_profit_price:
        try:
            strike_client.place_take_profit_order(trade.symbol, close_side, live_size, trade.take_profit_price)
            discord_client.notify_bracket_leg_restored(trade.symbol, "TP", trade.take_profit_price)
        except Exception as e:
            print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: obnovenie TP zlyhalo "
                  f"(skusim znova o {config.WATCH_INTERVAL_MINUTES} min): {e}")

    if trade.stop_loss_price:
        try:
            strike_client.place_stop_order(trade.symbol, close_side, live_size, trade.stop_loss_price)
            discord_client.notify_bracket_leg_restored(trade.symbol, "SL", trade.stop_loss_price)
        except Exception as e:
            print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: obnovenie SL zlyhalo "
                  f"(skusim znova o {config.WATCH_INTERVAL_MINUTES} min): {e}")


def check_open_trades():
    print(f"\n=== [position_monitor] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    pending_reviews: list = []
    pending_notifications: list = []
    pending_recompute: set = set()
    try:
        _backfill_missing_exact_data(session, pending_reviews, pending_notifications)
        # Kazdy tik, nezavisle od toho, ci prave teraz nieco zatvara - viz
        # _fire_due_recomputes docstring.
        _fire_due_recomputes(session, pending_recompute)

        open_trades = session.query(Trade).filter(Trade.status == "open").all()
        if not open_trades:
            print("[position_monitor] Ziadne otvorene pozicie.")
            session.commit()
            session.close()
            _fire_post_close_reviews(pending_reviews)
            _fire_close_notifications(pending_notifications)
            _fire_recomputes(pending_recompute)
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
                    #
                    # 2026-08-21 (na ziadost pouzivatela, po ADA incidente) - PRED
                    # _check_and_reheal_bracket_legs vyssie tu ODPADOK NIKDY nemohol
                    # ostat (OCO strategy vzdy zrusila sesterku niohu automaticky).
                    # Teraz vsak reheal moze doplnit SAMOSTATNU (nie strategy-linked)
                    # nohu - ked sa pozicia neskor zatvori DRUHOU (povodnou OCO) nohou,
                    # tato nova samostatna noha uz NEMA OCO partnera, ktory by ju
                    # zrusil, a ostala by visiet ako sirota (reduce_only bez pozicie
                    # na redukciu). cancel_all_orders je bezpecny aj ked ziadne
                    # visiace objednavky nie su (no-op) - preto sa vola VZDY tu,
                    # nie len podmienene.
                    try:
                        strike_client.cancel_all_orders(trade.symbol)
                    except Exception as e:
                        print(f"[position_monitor] Trade {trade.id} [{trade.symbol}]: "
                              f"upratanie zvysnych objednavok po zatvoreni zlyhalo "
                              f"(neblokujuce, skusi sa znova nabuduce): {e}")
                    trade.status = "closed_by_exchange"
                    trade.closed_at = now
                    _apply_exact_close(trade, "not_found_in_open_positions (TP/SL/liquidation)")
                    session.add(trade)
                    _check_and_queue_review(trade, session, pending_reviews)
                    _check_and_queue_close_notification(trade, pending_notifications)
                    _check_and_queue_recompute(trade)
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
                    _check_and_queue_recompute(trade)
                    watch_monitor.mark_hot(trade.symbol)
                else:
                    print(f"[position_monitor] Trade {trade.id} stale otvoreny "
                          f"(expiruje {expires_at.isoformat()}).")
                    _check_and_reheal_bracket_legs(trade, live)
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
    _fire_recomputes(pending_recompute)


if __name__ == "__main__":
    check_open_trades()
