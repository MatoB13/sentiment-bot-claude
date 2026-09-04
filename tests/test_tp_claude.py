"""Testy: TP je Claudov, pasmo 0.5x-5x kalibrovaneho TP, podlaha TP/SL >= 1.0."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

DB = os.environ["TEMP"].replace("\\", "/") + "/tpc.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import retrospective  # noqa: E402
import risk_manager as rm  # noqa: E402
from db import Trade  # noqa: E402

ok = True


def check(label, got, want, tol=1e-6):
    global ok
    good = (abs(got - want) <= tol) if isinstance(want, (int, float)) and not isinstance(want, bool) else (got == want)
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:22]:>22} (ocakavane {want})")


LIVE, SL_PCT, TP_PCT = 100.0, 2.0, 3.0      # kalibracia: SL 2 % (=2.0), TP 3 % (=3.0), pomer 1.5
META = {"order_tick_price": 0.01, "order_market_step_size": 0.001, "order_market_min_size": 0.001,
        "order_market_max_size": 1e9, "order_min_notional": 1.0,
        "margin_tiers": [{"max_notional": 1e9, "max_leverage": 20, "maintenance_margin_rate": 0.01}]}


def size(direction, sl, tp, conf=80):
    return rm.validate_and_size({"direction": direction, "confidence": conf, "stop_loss_price": sl,
                                 "take_profit_price": tp, "reasoning": "t"},
                                has_open_position=False, live_price=LIVE, market_meta=META,
                                min_confidence=50, sl_pct=SL_PCT, tp_pct=TP_PCT, cushion_multiple=1.5, margin_usd=100)


print("=" * 100)
print("1) resolve_sl_tp_distances")
print("=" * 100)
sl, tp, mech = rm.resolve_sl_tp_distances(LIVE, 98.0, 104.0, SL_PCT, TP_PCT)
check("SL 2.0, TP 4.0 (v pasme) -> pouzije sa Claudov TP 4.0", tp, 4.0)
check("   mechanicky TP = SL x 1.5 = 3.0", mech, 3.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, 100.5, SL_PCT, TP_PCT)
check("TP 0.5 (pod 0.5x kalib. 1.5) -> podlaha 1.5, potom pomer -> 2.0 (=SL)", tp, 2.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 99.0, 100.5, SL_PCT, TP_PCT)
check("SL 1.0 (na podlahe), TP 0.5 -> TP podlaha 0.5x kalib = 1.5", tp, 1.5)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, 130.0, SL_PCT, TP_PCT)
check("TP 30 (nad 5x kalib. 15) -> orezane na 15", tp, 15.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 96.0, 102.0, SL_PCT, TP_PCT)
check("SL 4.0, TP 2.0 -> pomer 0.5 < 1.0 -> TP zdvihnuty na 4.0", tp, 4.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, None, SL_PCT, TP_PCT)
check("TP chyba -> mechanicky 3.0", tp, 3.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, "abc", SL_PCT, TP_PCT)
check("TP neplatny -> mechanicky 3.0", tp, 3.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, 100.0, SL_PCT, TP_PCT)
check("TP == live (vzdialenost 0) -> mechanicky 3.0", tp, 3.0)
sl, tp, _ = rm.resolve_sl_tp_distances(LIVE, 98.0, 96.0, SL_PCT, TP_PCT)
check("TP na zlej strane (96 pri longu) -> berie sa vzdialenost 4.0", tp, 4.0)
sl, _, _ = rm.resolve_sl_tp_distances(LIVE, 85.0, 104.0, SL_PCT, TP_PCT)
check("SL 15 (nad 5x=10) -> orezane na 10 (bez zmeny spravania)", sl, 10.0)

print()
print("=" * 100)
print("2) validate_and_size - umiestnenie podla smeru + mechanicky TP v odpovedi")
print("=" * 100)
s = size("long", 98.0, 104.0)
check("LONG: SL 98", s["stop_loss_price"], 98.0)
check("LONG: TP 104 (Claudov)", s["take_profit_price"], 104.0)
check("LONG: mechanicky TP 103", s["take_profit_price_mechanical"], 103.0)
check("LONG: risk_reward 2.0", s["risk_reward"], 2.0)
s = size("short", 102.0, 97.5)
check("SHORT: SL 102", s["stop_loss_price"], 102.0)
check("SHORT: TP 97.5 (Claudov)", s["take_profit_price"], 97.5)
check("SHORT: mechanicky TP 97", s["take_profit_price_mechanical"], 97.0)
s = size("short", 104.0, 98.0)
check("SHORT: SL 4, TP 2 -> pomer <1 -> TP na 96", s["take_profit_price"], 96.0)
s = size("long", 98.0, 103.0)
check("Claudov TP = kalibrovany -> identicke so starym spravanim (103)", s["take_profit_price"], 103.0)

print()
print("=" * 100)
print("3) retrospective zrkadli to iste + DB stlpec")
print("=" * 100)
sl, tp = retrospective._hypothetical_sl_tp(LIVE, "long", 98.0, SL_PCT, TP_PCT, decision_tp=104.0)
check("retro: long SL 98 / TP 104", (sl, tp), (98.0, 104.0))
sl, tp = retrospective._hypothetical_sl_tp(LIVE, "short", 102.0, SL_PCT, TP_PCT, decision_tp=None)
check("retro: short bez TP -> mechanicky 97", (sl, tp), (102.0, 97.0))
check("Trade ma take_profit_price_mechanical", hasattr(Trade, "take_profit_price_mechanical"), True)

print()
print("=" * 100)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 100)
sys.exit(0 if ok else 1)
