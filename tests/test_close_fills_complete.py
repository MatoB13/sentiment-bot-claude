"""PnL sa smie zapisat, az ked zatvaracie fills pokryju celu poziciu.

POVOD (audit 2026-09-11): trade #214 (ADA short, timeout 10.9.) - zatvaracia
market objednavka mala 8 fills, Strike mal v case dotazu zaindexovane len 3.
Bot to vzal ako konecne: DB PnL $3.38 namiesto $35.27. Rovnako #134 (NEAR).

Fills nizsie su SKUTOCNE fills obchodu #214 zo Strike /v2/history/fill.
Test nevola siet - strike_client je nahradeny.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/closefills.db"
sys.path.insert(0, _ROOT)

import position_monitor as pm  # noqa: E402
from db import Trade  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r} (ocakavane {want!r})")


STRAT = "8ddaa5c1-12d"
ENTRY_ID, CLOSE_ID = 7107873635, 7216657744
orders = [
    {"id": ENTRY_ID, "side": "sell", "type": "market", "status": "filled", "is_primary": True,
     "reduce_only": False, "strategy_id": STRAT, "event_timestamp": 1},
    {"id": 7107873637, "side": "buy", "type": "stop", "status": "canceled", "is_primary": False,
     "reduce_only": True, "strategy_id": STRAT, "event_timestamp": 2},
    {"id": 7107873636, "side": "buy", "type": "take_profit_limit", "status": "canceled",
     "is_primary": False, "reduce_only": True, "strategy_id": STRAT, "event_timestamp": 2},
    {"id": CLOSE_ID, "side": "buy", "type": "market", "status": "filled", "is_primary": None,
     "reduce_only": True, "strategy_id": None, "event_timestamp": 3},
]
F = lambda oid, side, size, px, rpnl, fee, ts: {  # noqa: E731
    "order_id": oid, "side": side, "size": str(size), "price": str(px),
    "realized_pnl": str(rpnl), "fee": str(fee), "timestamp": ts}
entry_fills = [
    F(ENTRY_ID, "sell", 2789, 0.21117, 0, 0.21202313, 10),
    F(ENTRY_ID, "sell", 1894, 0.21116, 0, 0.14397733, 10),
    F(ENTRY_ID, "sell", 5578, 0.21114, 0, 0.42398601, 10),
    F(ENTRY_ID, "sell", 3552, 0.21113, 0, 0.26997615, 10),
    F(ENTRY_ID, "sell", 7045, 0.21112, 0, 0.53544254, 10),
]
close_fills = [
    F(CLOSE_ID, "buy", 477, 0.20921, 0.91935549, 0.03592554, 20),
    F(CLOSE_ID, "buy", 335, 0.20922, 0.64231895, 0.02523193, 20),
    F(CLOSE_ID, "buy", 1911, 0.20925, 3.60676407, 0.14395563, 20),
    F(CLOSE_ID, "buy", 670, 0.20926, 1.25783790, 0.05047351, 20),
    F(CLOSE_ID, "buy", 3583, 0.20929, 6.61912671, 0.26995899, 20),
    F(CLOSE_ID, "buy", 7166, 0.20930, 13.16659342, 0.53994377, 20),
    F(CLOSE_ID, "buy", 1341, 0.20931, 2.45050317, 0.10104650, 20),
    F(CLOSE_ID, "buy", 5375, 0.20932, 9.76836375, 0.40503420, 20),
]
state = {"fills": []}
pm.strike_client.get_order_history = lambda *a, **k: orders
pm.strike_client.get_fill_history = lambda *a, **k: state["fills"]


def trade(closed_min_ago):
    now = datetime.now(timezone.utc)
    return Trade(id=214, symbol="ADA-USD", direction="Short", strategy_id=STRAT,
                 opened_at=(now - timedelta(hours=24)).replace(tzinfo=None),
                 closed_at=(now - timedelta(minutes=closed_min_ago)).replace(tzinfo=None),
                 stop_loss_price=0.2200, take_profit_price=0.2000, close_reason="timeout")


print("1) Presne produkcna situacia: 1 min po zatvoreni, zaindexovane len 3 z 8 fills")
state["fills"] = entry_fills + close_fills[:3]
check("vysledok sa ZATIAL nezapise (None = skus znova)", pm._lookup_exact_close(trade(1)), None)

print("\n2) Po doindexovani vsetkych fills")
state["fills"] = entry_fills + close_fills
r = pm._lookup_exact_close(trade(2))
check("vysledok existuje", r is not None, True)
check("PnL = $35.27 (nie $3.38)", round(r["pnl_usd"], 2), 35.27)
check("poplatky vstup + vystup = $3.16", round(r["fees_usd"], 2), 3.16)
vwap_all = (sum(float(f["size"]) * float(f["price"]) for f in close_fills)
            / sum(float(f["size"]) for f in close_fills))
check("cena zatvorenia je vazeny priemer VSETKYCH 8 fills",
      round(r["close_fill_price"], 8), round(vwap_all, 8))
check("dovod: timeout zatvorenie botom", r["close_reason"], "force_closed_by_bot")

print("\n3) Poistka: ani po 45 min nie je vsetko - zapise sa, co je, neostane visiet")
state["fills"] = entry_fills + close_fills[:3]
r = pm._lookup_exact_close(trade(45))
check("po 30+ min sa vysledok zapise", r is not None, True)
check("  (z tych 3 fills, ako predtym)", round(r["pnl_usd"], 2), 3.38)

print("\n4) Viac zatvaracich fills nez vstup (incident #82/#83) sa neblokuje")
state["fills"] = entry_fills + close_fills + [F(CLOSE_ID, "buy", 5000, 0.2093, 9.0, 0.3, 20)]
check("vysledok existuje", pm._lookup_exact_close(trade(1)) is not None, True)

print("\n5) Tolerancia je na zaokruhlenie, nie na chybajuci fill")
check("prah 0.999", pm._CLOSE_SIZE_COMPLETE_FRACTION, 0.999)
check("najmensi fill #214 (477 ADA = 2.3 %) by prah zachytil",
      (20858 - 477) / 20858 < pm._CLOSE_SIZE_COMPLETE_FRACTION, True)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
