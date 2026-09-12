"""PREDLZOVANY TP ("nechat vyhry bezat") - 2026-09-12, schvalene pouzivatelom v
ramci strategie "chop prezit, v silnom trende/crashi naplno zarobit".

VARIANT D (2026-09-12 vecer, pouzivatel: "potrebujem mat istotu TP na burze"):
obchod sa otvara s NORMALNYM TP na burze (tp_mode = ARMED - "pripraveny").
Kym trh nie je v akcnom rezime, obchod je uplne klasicky (TP aj SL na burze,
vypadok bota nic nestoji). Monitor kazdu minutu pozera, ci je trh V SMERE
obchodu v akcnom rezime:
  - za TP_RUNNER_FAST_MINUTES >= FAST_ATR x ATR a zaroven >= FAST_MIN_PCT %
    (rychly spustac - crash/vystrel), ALEBO
  - za 24 h >= TP_RUNNER_DAY_PCT % (postupny silny trend).
Ked ano, PREPNE (tp_mode = RUNNER): cancel_all + ten isty SL + HAVARIJNY TP
(TP_RUNNER_EXCHANGE_TP_MULT x dalej). Rezim sa uz spat neprepina. Povodny
variant A (predlzenie pre kazdy obchod, TP na burze nikdy) pouzivatel odmietol.

V REZIME RUNNER: skutocny TP nie je na burze, len HAVARIJNY TP. Bot kazdu
minutu (position_monitor) pozera mark cenu; ked dosiahne TP:
  1. pozicia sa NEZATVORI,
  2. SL na burze sa posunie na zamknuty zisk:
       TP - min(LOCK_ATR x ATR, LOCK_MAX_FRACTION x vzdialenost TP)
     (v crashi je ATR vacsi nez vzdialenost TP - bez druheho clena by zamok vysiel
     pod vstup; tak je zisk VZDY aspon 3/4 cesty k TP),
  3. dalej sa SL posuva za najlepsou cenou o min(TRAIL_ATR x ATR, 1 x vzdialenost TP),
     na burze az ked sa zlepsi aspon o MIN_STEP_ATR x ATR,
  4. pozicia smie bezat do TP_RUNNER_MAX_HOURS od otvorenia (bez TP ostava 24 h).

Backtest (12.9., 1-min data, 98 krypto obchodov + crash 10.10.2025 s opatovnym
vstupom) viz pamat trend_mechanics_tested.

BEZPECNOST (na ziadost pouzivatela - burza obcas "straca" SL/TP nohy):
- Posun SL ide cez cancel_all_orders + nove SL + havarijny TP (jediny nastroj,
  rovnaky ako oprava stratenych noh). Ak polozenie noveho SL zlyha aj na druhy
  pokus, pozicia sa HNED zatvori trhovo - sme nad TP, takze to je zisk, nikdy
  nie pozicia bez ochrany.
- Aktualny SL je v Trade.active_stop_price - oprava stratenych noh obnovi PRAVE
  jeho (zamknuty zisk), nie povodny SL.
- Kazda udalost ide do tp_runner_events + Discord pri zamknuti.
- PREPNUTIE: ked sa pri nom nepodari polozit SL, tp_exchange_price sa vrati na
  None (obchod ostava ARMED) a oprava noh v tom istom tiku doplni SL aj
  KLASICKY TP - pozicia nikdy neostane bez SL ani bez TP na burze. Dalsi pokus
  o prepnutie az o TP_RUNNER_SWITCH_RETRY_MINUTES.

NIKDY nevola burzu v testoch (strike_client._request ma zamok).
"""
from datetime import datetime, timedelta, timezone

import config
import discord_client
import price_buffer
import strike_client
from db import CycleLog, PriceBar, TpRunnerEvent

ARMED = "armed"     # normalny TP na burze, caka na akcny rezim
RUNNER = "runner"   # akcny rezim - havarijny TP na burze, skutocny TP sleduje bot
MANAGED_MODES = {ARMED, RUNNER}
# Novy dovod zatvorenia - "nebol to klasicky TP, ale predlzovany".
REASON_STOP = "tp_runner_stop"          # zamknuty/posunuty SL po zasahu TP
REASON_TIMEOUT = "tp_runner_timeout"    # dobehol TP_RUNNER_MAX_HOURS
REASON_FAR_TP = "tp_runner_far_tp"      # trafil havarijny TP na burze
REASON_EMERGENCY = "tp_runner_emergency_close"  # SL sa nepodarilo polozit -> zatvorene na zisku
RUNNER_REASONS = {REASON_STOP, REASON_TIMEOUT, REASON_FAR_TP, REASON_EMERGENCY}


def _sign(direction: str) -> int:
    return 1 if (direction or "").lower() == "long" else -1


def _round_to_tick(price: float, tick: float | None) -> float:
    if not tick:
        return round(price, 8)
    return round(round(price / tick) * tick, 8)


def exchange_tp_price(direction: str, entry: float, tp: float, tick: float | None) -> float:
    """Havarijny TP pre burzu - TP_RUNNER_EXCHANGE_TP_MULT x dalej od vstupu.
    Pri shorte nikdy pod 10 % vstupnej ceny (nezmyselna/zaporna cena by bracket
    prikaz zhodila)."""
    sg = _sign(direction)
    far = entry + sg * abs(tp - entry) * config.TP_RUNNER_EXCHANGE_TP_MULT
    if sg < 0:
        far = max(far, entry * 0.10)
    return _round_to_tick(far, tick)


def lock_and_trail(direction: str, entry: float, tp: float, atr: float | None) -> tuple[float, float]:
    """(uroven zamknuteho SL, vzdialenost trailingu) - cista funkcia, testuje sa priamo."""
    sg = _sign(direction)
    dist = abs(tp - entry)
    a = atr if atr and atr > 0 else dist / 2
    lock_off = min(config.TP_RUNNER_LOCK_ATR * a, config.TP_RUNNER_LOCK_MAX_FRACTION * dist)
    trail = min(config.TP_RUNNER_TRAIL_ATR * a, config.TP_RUNNER_TRAIL_MAX_FRACTION * dist)
    return tp - sg * lock_off, trail


def regime_hit(direction: str, price: float, ref_fast: float | None, ref_day: float | None,
               atr: float | None) -> str | None:
    """Cista funkcia: je trh V SMERE obchodu v akcnom rezime? Vrati popis spustaca
    (do logu/Discordu) alebo None. Pohyb proti smeru obchodu sa nepocita nikdy."""
    if not price or price <= 0:
        return None
    sg = _sign(direction)
    if ref_fast and atr and atr > 0:
        pct = sg * (price / ref_fast - 1) * 100
        in_atr = sg * (price - ref_fast) / atr
        if in_atr >= config.TP_RUNNER_FAST_ATR and pct >= config.TP_RUNNER_FAST_MIN_PCT:
            return (f"rychly pohyb {pct:+.2f} % ({in_atr:.1f} ATR) za "
                    f"{config.TP_RUNNER_FAST_MINUTES} min")
    if ref_day:
        pct = sg * (price / ref_day - 1) * 100
        if pct >= config.TP_RUNNER_DAY_PCT:
            return f"pohyb {pct:+.1f} % za 24 h"
    return None


def _day_ref(session, symbol: str, now) -> float | None:
    """Cena pred ~24 h: otvaracia cena hodinovej sviecky, ktora zacala 24 h pred
    aktualnou hodinou (okno 24-25 h - pre spustac staci)."""
    n = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    start = n.replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)
    row = session.query(PriceBar.open).filter(PriceBar.symbol == symbol, PriceBar.hour_start == start).first()
    return float(row[0]) if row and row[0] else None


def _recent_switch_fail(session, trade, now) -> bool:
    n = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    since = n - timedelta(minutes=config.TP_RUNNER_SWITCH_RETRY_MINUTES)
    return session.query(TpRunnerEvent.id).filter(
        TpRunnerEvent.trade_id == trade.id, TpRunnerEvent.kind == "switch_fail",
        TpRunnerEvent.at >= since).first() is not None


def runner_close_reason(trade, reason: str | None) -> str | None:
    """Premenuje dovod zatvorenia obchodu, ktory uz bezal v predlzovanom rezime -
    aby bolo v DB, na Discorde aj na dashboarde vidiet, ze to nebol klasicky TP/SL."""
    if not trade.tp_locked_at:
        return reason
    if reason == "stop_loss":
        return REASON_STOP
    if reason == "take_profit":
        return REASON_FAR_TP
    if reason == "force_closed_by_bot":
        return trade.close_reason if trade.close_reason in RUNNER_REASONS else REASON_TIMEOUT
    return reason


def log_event(session, trade, kind: str, price=None, stop=None, note=None) -> None:
    session.add(TpRunnerEvent(trade_id=trade.id, symbol=trade.symbol, kind=kind,
                              price=price, stop_price=stop, note=note))
    print(f"[tp_runner] Trade {trade.id} [{trade.symbol}] {kind}: cena {price}, SL {stop}"
          + (f" ({note})" if note else ""))


def _entry_atr(trade, session) -> float | None:
    if trade.entry_atr:
        return trade.entry_atr
    try:
        log = (session.query(CycleLog).filter(CycleLog.trade_id == trade.id)
               .order_by(CycleLog.created_at).first())
        atr = float(((log.ta or {}) if log else {}).get("atr14") or 0)
        return atr if atr > 0 else None
    except Exception:
        return None


def _replace_stop(trade, live_size: float, new_stop: float) -> bool:
    """cancel_all + nove SL + havarijny TP. True = SL je na burze. Pri zlyhani SL
    druhy pokus; ked zlyha aj ten, vrati False (volajuci poziciu zatvori)."""
    close_side = "sell" if trade.direction == "Long" else "buy"
    strike_client.cancel_all_orders(trade.symbol)
    placed = False
    for attempt in (1, 2):
        try:
            strike_client.place_stop_order(trade.symbol, close_side, live_size, new_stop)
            placed = True
            break
        except Exception as e:
            print(f"[tp_runner] Trade {trade.id}: polozenie SL {new_stop} zlyhalo (pokus {attempt}): {e}")
    if trade.tp_exchange_price:
        try:
            strike_client.place_take_profit_order(trade.symbol, close_side, live_size, trade.tp_exchange_price)
        except Exception as e:
            # Havarijny TP nie je ochrana - chybu len zaznamename, oprava noh ho doplni.
            print(f"[tp_runner] Trade {trade.id}: havarijny TP sa nepodarilo polozit: {e}")
    return placed


def manage(trade, live: dict, mark_price: float | None, tick: float | None, session, now) -> dict:
    """Vola position_monitor kazdy tik pre otvoreny obchod. Vrati
    {"orders_changed": bool, "closed": bool} - pri orders_changed sa v tom istom
    tiku preskoci oprava noh (openOrders by este nemuseli ukazat prave polozene
    objednavky a spustila by sa falosna "oprava" s REPAIR notifikaciou)."""
    out = {"orders_changed": False, "closed": False}
    if trade.tp_mode not in MANAGED_MODES or trade.take_profit_price is None or mark_price is None:
        return out
    sg = _sign(trade.direction)
    entry = trade.entry_fill_price or trade.entry_price
    tp = trade.take_profit_price
    atr = _entry_atr(trade, session)
    lock_stop, trail = lock_and_trail(trade.direction, entry, tp, atr)
    live_size = abs(float(live["size"]))

    if trade.tp_mode == ARMED:
        return _maybe_switch(trade, live_size, mark_price, tick, atr, session, now, out)

    if trade.tp_locked_at is None:
        if not ((mark_price >= tp) if sg > 0 else (mark_price <= tp)):
            return out
        new_stop = _round_to_tick(lock_stop, tick)
        ok = _replace_stop(trade, live_size, new_stop)
        out["orders_changed"] = True
        trade.tp_locked_at = now
        trade.trail_best_price = mark_price
        opened = trade.opened_at if trade.opened_at.tzinfo else trade.opened_at.replace(tzinfo=timezone.utc)
        trade.expires_at = opened + timedelta(hours=config.TP_RUNNER_MAX_HOURS)
        if not ok:
            _emergency_close(trade, live_size, mark_price, session, now)
            out["closed"] = True
            return out
        trade.active_stop_price = new_stop
        log_event(session, trade, "lock", mark_price, new_stop,
                  f"TP {tp} dosiahnuty - zisk zamknuty, trailing {trail:.6g}, max do {trade.expires_at:%d.%m %H:%M} UTC")
        discord_client.notify_tp_runner_locked(trade.symbol, trade.direction, tp, new_stop, trade.expires_at)
        return out

    # uz zamknute - posuvanie za cenou
    best = trade.trail_best_price or tp
    best = max(best, mark_price) if sg > 0 else min(best, mark_price)
    trade.trail_best_price = best
    current = trade.active_stop_price if trade.active_stop_price is not None else lock_stop
    cand = _round_to_tick(best - sg * trail, tick)
    step = config.TP_RUNNER_MIN_STEP_ATR * (atr or abs(tp - entry) / 2)
    if (cand - current) * sg >= step:
        ok = _replace_stop(trade, live_size, cand)
        out["orders_changed"] = True
        if not ok:
            _emergency_close(trade, live_size, mark_price, session, now)
            out["closed"] = True
            return out
        trade.active_stop_price = cand
        log_event(session, trade, "trail", mark_price, cand, f"najlepsia cena {best}")
    return out


def _maybe_switch(trade, live_size, mark_price, tick, atr, session, now, out) -> dict:
    """ARMED -> RUNNER, ked je trh v smere obchodu v akcnom rezime."""
    ref_fast = price_buffer.price_at(trade.symbol, now - timedelta(minutes=config.TP_RUNNER_FAST_MINUTES),
                                     tolerance_min=2)
    why = regime_hit(trade.direction, mark_price, ref_fast, _day_ref(session, trade.symbol, now), atr)
    if why is None or _recent_switch_fail(session, trade, now):
        return out
    entry = trade.entry_fill_price or trade.entry_price
    stop = trade.active_stop_price if trade.active_stop_price is not None else trade.stop_loss_price
    trade.tp_exchange_price = exchange_tp_price(trade.direction, entry, trade.take_profit_price, tick)
    ok = _replace_stop(trade, live_size, stop)
    if not ok:
        # SL nie je na burze - vratit klasicky TP a nechat opravu noh v TOMTO
        # tiku doplnit SL aj TP (orders_changed ostava False, oprava sa nepreskoci).
        trade.tp_exchange_price = None
        log_event(session, trade, "switch_fail", mark_price, stop,
                  f"{why} - SL sa nepodarilo polozit, ostava klasicky TP (dalsi pokus o "
                  f"{config.TP_RUNNER_SWITCH_RETRY_MINUTES} min)")
        return out
    out["orders_changed"] = True
    trade.tp_mode = RUNNER
    trade.active_stop_price = stop
    log_event(session, trade, "regime", mark_price, stop,
              f"akcny rezim: {why} - TP na burze odsunuty na {trade.tp_exchange_price}, "
              f"pri TP {trade.take_profit_price} sa zisk zamkne")
    discord_client.notify_tp_runner_regime(trade.symbol, trade.direction, why, trade.take_profit_price)
    return out


def _emergency_close(trade, live_size, mark_price, session, now) -> None:
    """SL sa nepodarilo polozit ani na druhy pokus - pozicia NESMIE ostat bez
    ochrany. Sme nad TP, takze trhove zatvorenie = zisk (ako klasicky TP)."""
    log_event(session, trade, "repair_fail", mark_price, None, "SL sa nepodarilo polozit - zatvaram trhovo")
    try:
        strike_client.cancel_all_orders(trade.symbol)
        strike_client.close_position_market(trade.direction, live_size, trade.symbol)
        trade.status = "closed_by_timeout"
        trade.closed_at = now
        trade.close_reason = REASON_EMERGENCY
    except Exception as e:
        # Aj toto zlyhalo - oprava noh v dalsom tiku doplni SL z active_stop_price
        # (ten je stale nastaveny na posledny platny), pripadne povodny SL.
        print(f"[tp_runner] KRITICKE Trade {trade.id}: ani nudzove zatvorenie neprebehlo: {e}")
