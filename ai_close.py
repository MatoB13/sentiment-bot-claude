"""AI ZATVORENIE S 15-MINUTOVYM POTVRDENIM - 2026-09-14, schvalene pouzivatelom.

POVOD: ZHIPU #224 (14.9.) - Claude pri kontrole pozicie odporucil zatvorit
(istota 60) a bot zavrel na 95.78, tick pod maximom prudkeho odrazu (95.79);
SL 96.60 sa nikdy nezasiahol a cena potom padla na 92. Meranie na 30 AI
zatvoreniach: zatvaranie stoji -2.5 R voci drzaniu a deje sa do spicky
protipohybu (po zatvoreni sa cena v 9 zo 16 pripadov najprv vratila v prospech).
Istota (close_confidence) dobre a zle zatvorenia NEROZLISUJE, preto nie vyssi
prah, ale NACASOVANIE.

PRAVIDLO:
  1. Claude odporuci consider_closing s istotou >= prah -> NEZATVARA SA hned.
     Ulozi sa cena a cas rozhodnutia (Trade.ai_close_pending_*). SL/TP na burze
     platia dalej.
  2. O AI_CLOSE_CONFIRM_MINUTES (position_monitor, kazdu minutu):
       - cena NIE JE lepsia nez pri rozhodnuti -> zavriet trhovo (ako predtym),
       - cena JE lepsia -> nezatvarat, ale posunut SL na burze na cenu
         rozhodnutia (ochranny SL): ked sa vrati, zavrie sa tam, kde chcel
         zavriet Claude; ked pokracuje v prospech, pozicia bezi dalej.
  Backtest (krypto 1m, 16 obchodov): dnes -7.73 R, s pravidlom -2.66 R.

BEZPECNOST (rovnaky vzor ako tp_runner): posun SL = cancel_all + nove SL +
povodny/havarijny TP. Ked SL neprejde ani na druhy pokus, pozicia sa zavrie
trhovo (Claudovo rozhodnutie plati). Ochranny SL je v active_stop_price, takze
ho obnovi aj oprava stratenych noh. SL sa nikdy neuvolnuje - len sprisnuje.
Kazda udalost ide do tp_runner_events (kind ai_pending / ai_close / ai_protect
/ ai_fail) + Discord.

NIKDY nevola burzu v testoch (strike_client._request ma zamok)."""
from datetime import timedelta, timezone

import config
import discord_client
import strike_client
import tp_runner

REASON_PROTECT = "ai_protect_stop"     # ochranny SL na cene rozhodnutia AI


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start_pending(trade, price: float | None, conf, session, now) -> bool:
    """Volane z trade_cycle._maybe_ai_early_close. True = zatvorenie odlozene
    (volajuci nezatvara); False = potvrdenie vypnute alebo chyba cena
    (volajuci zatvori hned ako predtym)."""
    if config.AI_CLOSE_CONFIRM_MINUTES <= 0 or not price or price <= 0:
        return False
    if trade.ai_close_pending_at is not None:
        print(f"[ai_close] Trade {trade.id}: zatvorenie uz caka na potvrdenie - nove rozhodnutie ignorujem.")
        return True
    trade.ai_close_pending_at = _naive(now)
    trade.ai_close_pending_price = float(price)
    trade.ai_close_pending_conf = int(conf) if conf is not None else None
    due = _aware(now) + timedelta(minutes=config.AI_CLOSE_CONFIRM_MINUTES)
    tp_runner.log_event(session, trade, "ai_pending", float(price), None,
                        f"Claude chce zavriet (istota {conf}) - potvrdenie o {config.AI_CLOSE_CONFIRM_MINUTES} min "
                        f"({due:%H:%M} UTC): zavriet, ak cena nebude lepsia nez {price}")
    discord_client.notify_ai_close_pending(trade.symbol, trade.direction, price, conf, due)
    return True


def _close_market(trade, live_size, session, now, note) -> bool:
    try:
        strike_client.cancel_all_orders(trade.symbol)
        strike_client.close_position_market(trade.direction, live_size, trade.symbol)
    except Exception as e:
        print(f"[ai_close] KRITICKE Trade {trade.id}: trhove zatvorenie zlyhalo (skusim o minutu): {e}")
        return False
    trade.status = "closed_by_ai"
    trade.close_reason = "ai_early_close"
    trade.closed_at = now
    trade.ai_close_pending_at = None
    tp_runner.log_event(session, trade, "ai_close", trade.ai_close_pending_price, None, note)
    return True


def _set_protective_stop(trade, live_size, stop: float) -> bool:
    """cancel_all + ochranny SL + TP, ktory na burze patri (klasicky alebo havarijny)."""
    close_side = "sell" if trade.direction == "Long" else "buy"
    strike_client.cancel_all_orders(trade.symbol)
    placed = False
    for attempt in (1, 2):
        try:
            strike_client.place_stop_order(trade.symbol, close_side, live_size, stop)
            placed = True
            break
        except Exception as e:
            print(f"[ai_close] Trade {trade.id}: ochranny SL {stop} zlyhal (pokus {attempt}): {e}")
    tp_price = trade.tp_exchange_price or trade.take_profit_price
    if tp_price:
        try:
            strike_client.place_take_profit_order(trade.symbol, close_side, live_size, tp_price)
        except Exception as e:
            # TP nie je ochrana - oprava noh ho doplni v dalsom tiku.
            print(f"[ai_close] Trade {trade.id}: TP po posune SL sa nepodarilo polozit: {e}")
    return placed


def resolve(trade, live: dict, mark_price: float | None, tick: float | None, session, now) -> dict:
    """Volane z position_monitor kazdy tik pre otvoreny obchod s cakajucim AI
    zatvorenim. Vrati {"closed": bool, "orders_changed": bool}."""
    out = {"closed": False, "orders_changed": False}
    if trade.ai_close_pending_at is None:
        return out
    pending_at = _aware(trade.ai_close_pending_at)
    due = pending_at + timedelta(minutes=config.AI_CLOSE_CONFIRM_MINUTES)
    now_a = _aware(now)
    if now_a < due:
        return out
    live_size = abs(float(live["size"]))
    p0 = trade.ai_close_pending_price
    sg = 1 if trade.direction == "Long" else -1

    if mark_price is None or p0 is None:
        if now_a >= due + timedelta(minutes=config.AI_CLOSE_CONFIRM_MAX_WAIT_MINUTES):
            out["closed"] = _close_market(trade, live_size, session, now,
                                          "potvrdenie bez aktualnej ceny (vypadok) - plati Claudovo rozhodnutie")
            out["orders_changed"] = out["closed"]
        return out

    if (mark_price - p0) * sg <= 0:
        # cena nie je lepsia nez pri rozhodnuti -> Claude mal pravdu, zavriet
        out["closed"] = _close_market(trade, live_size, session, now,
                                      f"potvrdene po {config.AI_CLOSE_CONFIRM_MINUTES} min: cena {mark_price} "
                                      f"nie je lepsia nez pri rozhodnuti {p0}")
        out["orders_changed"] = out["closed"]
        if out["closed"]:
            discord_client.notify_ai_close_resolved(trade.symbol, trade.direction, "closed", p0, mark_price)
        return out

    # cena je lepsia -> nezatvarat, ochranny SL na cene rozhodnutia (len sprisnenie)
    stop = tp_runner._round_to_tick(p0, tick)
    current = trade.active_stop_price if trade.active_stop_price is not None else trade.stop_loss_price
    trade.ai_close_pending_at = None
    if current is not None and (current - stop) * sg >= 0:
        tp_runner.log_event(session, trade, "ai_protect", mark_price, current,
                            f"cena sa zlepsila ({p0} -> {mark_price}); SL {current} je uz tesnejsi, ostava")
        return out
    ok = _set_protective_stop(trade, live_size, stop)
    out["orders_changed"] = True
    if not ok:
        tp_runner.log_event(session, trade, "ai_fail", mark_price, stop,
                            "ochranny SL sa nepodarilo polozit - zatvaram trhovo (plati Claudovo rozhodnutie)")
        trade.ai_close_pending_price = p0
        out["closed"] = _close_market(trade, live_size, session, now, "ochranny SL zlyhal - zatvorene trhovo")
        return out
    trade.active_stop_price = stop
    trade.ai_protected_at = _naive(now)
    tp_runner.log_event(session, trade, "ai_protect", mark_price, stop,
                        f"cena sa za {config.AI_CLOSE_CONFIRM_MINUTES} min zlepsila ({p0} -> {mark_price}) - "
                        f"nezatvaram, SL posunuty na cenu rozhodnutia {stop}")
    discord_client.notify_ai_close_resolved(trade.symbol, trade.direction, "protected", p0, mark_price, stop)
    return out


def close_reason(trade, reason: str | None) -> str | None:
    """Obchod zatvoreny ochrannym SL (nie povodnym) dostane vlastny dovod.
    Zamknuty predlzeny TP ma prednost (tp_runner.runner_close_reason)."""
    if reason == "stop_loss" and trade.ai_protected_at and not trade.tp_locked_at:
        return REASON_PROTECT
    return reason
