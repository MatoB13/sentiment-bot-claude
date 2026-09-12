"""ALARM NA EXTREMNY POHYB - 2026-09-12, schvalene pouzivatelom ("v ziadnom
pripade nechcem prist o moznost vyrazneho zarobku pri brutalnom raste alebo
crashi").

Kazdu minutu (main.py) pozrie mark cenu vsetkych aktivnych tickerov BEZ otvorenej
pozicie. Ked sa cena pohne o >= EXTREME_ALARM_1H_ATR x ATR14(1h) za hodinu
(a zaroven >= MIN_1H_PCT %), alebo o >= 4H_ATR x ATR za 4 h (a >= MIN_4H_PCT %),
spusti okamzity plny Claude cyklus (trigger_source="alarm") - bez ohladu na plan
a na to, ci Claude nastavil watch. O vstupe rozhoduje stale Claude.

PRECO LEN EXTREMY: test 12.9. - po beznych spickach (3 ATR) sa cena castejsie
ciastocne vrati (median -0.27 ATR), okamzity vstup by bol vstup na vrchole. Pri
extremoch (4/7 ATR, >= 8 % za 4 h) cena pokracovala v 54-63 % pripadov.

POISTKY PROTI PLATENEJ SLUCKE (kazdy alarm = platene Claude volanie):
- zaznam do alarm_triggers PRED spustenim cyklu (cooldown prezije aj restart),
- cooldown EXTREME_ALARM_COOLDOWN_HOURS na ticker,
- globalny strop EXTREME_ALARM_MAX_PER_HOUR (crash = vsetky tickery naraz),
- prebiehajuci mimoriadny beh pre ticker = alarm sa nespusti.

Cena pred 1 h / 4 h: z minutovych vzoriek v pamati; po restarte (ked este nie su)
z hodinovych sviecok price_bars (otvaracia cena hodiny, ktora zacala pred 1-2 h,
resp. 4-5 h - okno je o chvilu sirsie, alarm teda nie je slepy ani hned po
nasadeni).
"""
from datetime import datetime, timedelta, timezone

import assets
import config
import discord_client
import price_buffer
import strike_client
import trade_cycle
from db import AlarmTrigger, PriceBar, Trade, get_session

# Minutove vzorky su v price_buffer.py (zdiela ich aj tp_runner) - tu len aliasy.
_buffer = price_buffer._buffer
record_price = price_buffer.record_price
_price_at = price_buffer.price_at
_atr_cache: dict[str, tuple] = {}


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _hour_start(now: datetime, hours_back: int) -> datetime:
    return _naive(now).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours_back)


def _bar_open(session, symbol: str, now: datetime, hours_back: int) -> float | None:
    row = session.query(PriceBar.open).filter(PriceBar.symbol == symbol,
                                               PriceBar.hour_start == _hour_start(now, hours_back)).first()
    return float(row[0]) if row and row[0] else None


def atr14(session, symbol: str, now: datetime) -> float | None:
    """Wilder ATR14 z UZAVRETYCH hodinovych sviecok price_bars (kes na hodinu)."""
    key = _hour_start(now, 0)
    hit = _atr_cache.get(symbol)
    if hit and hit[0] == key:
        return hit[1]
    rows = (session.query(PriceBar.high, PriceBar.low, PriceBar.close)
            .filter(PriceBar.symbol == symbol, PriceBar.hour_start < key)
            .order_by(PriceBar.hour_start.desc()).limit(60).all())[::-1]
    if len(rows) < 20:
        return None
    atr, prev_c = None, None
    for h, l, c in rows:
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        atr = tr if atr is None else atr + (tr - atr) / 14
        prev_c = c
    _atr_cache[symbol] = (key, atr)
    return atr


def evaluate(price: float, ref_1h: float | None, ref_4h: float | None, atr: float | None) -> dict | None:
    """Cista funkcia (testuje sa priamo): vrati popis alarmu alebo None."""
    if not atr or atr <= 0 or not price:
        return None
    out = {"price": price, "atr14": atr, "move_1h_atr": None, "move_4h_atr": None,
           "move_1h_pct": None, "move_4h_pct": None}
    hit = None
    if ref_1h:
        out["move_1h_atr"] = (price - ref_1h) / atr
        out["move_1h_pct"] = (price / ref_1h - 1) * 100
        if (abs(out["move_1h_atr"]) >= config.EXTREME_ALARM_1H_ATR
                and abs(out["move_1h_pct"]) >= config.EXTREME_ALARM_MIN_1H_PCT):
            hit = out["move_1h_atr"]
    if ref_4h:
        out["move_4h_atr"] = (price - ref_4h) / atr
        out["move_4h_pct"] = (price / ref_4h - 1) * 100
        if hit is None and (abs(out["move_4h_atr"]) >= config.EXTREME_ALARM_4H_ATR
                            and abs(out["move_4h_pct"]) >= config.EXTREME_ALARM_MIN_4H_PCT):
            hit = out["move_4h_atr"]
    if hit is None:
        return None
    out["direction"] = "up" if hit > 0 else "down"
    return out


def check_extreme_moves() -> None:
    """Scheduler job (main.py, kazdu minutu)."""
    if not config.EXTREME_ALARM_ENABLED:
        return
    now = datetime.now(timezone.utc)
    try:
        markets = {m.get("symbol"): m for m in strike_client.get_markets()}
    except Exception as e:
        print(f"[extreme_alarm] /v2/markets zlyhalo, preskakujem tik: {e}")
        return
    session = get_session()
    try:
        for asset in assets.enabled_assets():
            symbol = asset["strike_symbol"]
            m = markets.get(symbol) or {}
            try:
                price = float(m.get("mark_price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            record_price(symbol, now, price)
            try:
                _check_asset(session, asset, symbol, price, now)
            except Exception as e:
                session.rollback()
                print(f"[extreme_alarm] [{asset['name']}] kontrola zlyhala (neblokujuce): {e}")
    finally:
        session.close()


def _check_asset(session, asset: dict, symbol: str, price: float, now: datetime) -> None:
    if session.query(Trade.id).filter(Trade.symbol == symbol, Trade.status == "open").first():
        return      # otvorenu poziciu chrani SL + health check, nie alarm
    atr = atr14(session, symbol, now)
    ref_1h = _price_at(symbol, now - timedelta(minutes=60)) or _bar_open(session, symbol, now, 1)
    ref_4h = _price_at(symbol, now - timedelta(minutes=240)) or _bar_open(session, symbol, now, 4)
    alarm = evaluate(price, ref_1h, ref_4h, atr)
    if alarm is None:
        return
    since = _naive(now) - timedelta(hours=config.EXTREME_ALARM_COOLDOWN_HOURS)
    if session.query(AlarmTrigger.id).filter(AlarmTrigger.symbol == symbol,
                                             AlarmTrigger.triggered_at >= since).first():
        return
    if trade_cycle.is_triggered_check_in_flight(symbol):
        return      # uz bezi mimoriadny beh - skusi sa na dalsom tiku
    hour_ago = _naive(now) - timedelta(hours=1)
    fired = session.query(AlarmTrigger).filter(AlarmTrigger.triggered_at >= hour_ago,
                                               AlarmTrigger.dispatched.is_(True)).count()
    dispatch = fired < config.EXTREME_ALARM_MAX_PER_HOUR
    session.add(AlarmTrigger(
        symbol=symbol, triggered_at=_naive(now), direction=alarm["direction"], price=price,
        move_1h_atr=alarm["move_1h_atr"], move_4h_atr=alarm["move_4h_atr"],
        move_1h_pct=alarm["move_1h_pct"], move_4h_pct=alarm["move_4h_pct"],
        atr14=atr, dispatched=dispatch,
        note=None if dispatch else f"globalny strop {config.EXTREME_ALARM_MAX_PER_HOUR}/h vycerpany",
    ))
    session.commit()
    print(f"[extreme_alarm] [{asset['name']}] EXTREMNY POHYB {alarm['direction']}: "
          f"1h {alarm['move_1h_atr']} ATR, 4h {alarm['move_4h_atr']} ATR - "
          + ("spustam cyklus" if dispatch else "strop vycerpany, len zaznamenane"))
    if not dispatch:
        return
    discord_client.notify_extreme_alarm(symbol, alarm["direction"], alarm["move_1h_atr"],
                                        alarm["move_4h_atr"], alarm["move_4h_pct"])
    trade_cycle.dispatch_triggered_check(asset, alarm=alarm)
