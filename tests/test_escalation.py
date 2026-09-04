"""Test novej eskalacnej logiky: makro trigger + cooldown per druh triggeru."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os, sys
sys.path.insert(0, _ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(os.environ.get("TEMP","."), "esc.db")
import config, assets, trade_cycle

ZEC = next(a for a in assets.ALL_ASSETS if a["name"] == "ZEC")
SL = ZEC["sl_pct"]

def pos(direction, pnl_pct):
    return {"direction": direction, "unrealized_pnl_pct": pnl_pct}

print("=" * 96)
print("1) _mechanical_health_escalation - vracia (dovod, druh)?")
print("=" * 96)
cases = [
    ("v zisku, neutralny trend, ziadne makro", {"trend": "mild_uptrend"}, pos("Long", 1.0), None, None),
    ("trend sa obratil proti LONG",            {"trend": "strong_downtrend"}, pos("Long", 0.5), None, "trend"),
    ("strata nad 60% SL",                      {"trend": "mild_uptrend"}, pos("Long", -SL*0.7), None, "loss"),
    ("MAKRO udalost, pozicia v zisku",         {"trend": "mild_uptrend"}, pos("Long", 2.0), "CPI (August)", "macro"),
    ("MAKRO ma prednost pred trendom",         {"trend": "strong_downtrend"}, pos("Long", -1.0), "FOMC", "macro"),
]
ok = fail = 0
for label, ta, p, macro, expected_kind in cases:
    r = trade_cycle._mechanical_health_escalation(ZEC, ta, p, macro)
    kind = r[1] if r else None
    mark = "OK" if kind == expected_kind else "CHYBA"
    ok += (kind == expected_kind); fail += (kind != expected_kind)
    print(f"  {label:<42} ocakavane={str(expected_kind):<7} vysledok={str(kind):<7} {mark}")
print(f"\n  OK={ok}  CHYBA={fail}")

print()
print("=" * 96)
print("2) COOLDOWN per druh triggeru - simulacia rozhodovacej logiky")
print("=" * 96)
COOLDOWN_H = config.HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS

def would_escalate(last_kind, last_hours_ago, new_kind, pnl_pct, sl_pct):
    """Replika logiky z _run_position_health_check (bez DB)."""
    if new_kind is None:
        return False, "ziadny trigger"
    if last_hours_ago is None:
        return True, "prva eskalacia"
    cooldown = last_hours_ago < COOLDOWN_H
    if not cooldown:
        return True, "cooldown uplynul"
    if last_kind and new_kind and last_kind != new_kind:
        return True, f"iny druh triggeru ({last_kind} -> {new_kind})"
    if pnl_pct < 0 and sl_pct > 0:
        if (-pnl_pct) / sl_pct >= config.HEALTH_CHECK_COOLDOWN_BYPASS_SL_PROXIMITY_FRACTION:
            return True, "SL-proximity bypass"
    return False, "COOLDOWN zablokoval"

tests = [
    ("strata -> strata o 1h (opakovanie)",        "loss",  1.0, "loss",  -SL*0.7, True,  "bypass SL-prox"),
    ("strata -> TREND o 1h (novy fakt)",          "loss",  1.0, "trend", -0.2,    True,  "iny druh"),
    ("trend -> trend o 1h (opakovanie)",          "trend", 1.0, "trend",  0.5,    False, "ma blokovat"),
    ("trend -> MAKRO o 1h (novy fakt)",           "trend", 1.0, "macro",  0.5,    True,  "iny druh"),
    ("makro -> makro o 1h (opakovanie)",          "macro", 1.0, "macro",  0.5,    False, "ma blokovat"),
    ("trend -> trend o 4h (cooldown uplynul)",    "trend", 4.0, "trend",  0.5,    True,  "cooldown pryc"),
]
print(f"  {'scenar':<40} {'ocakavane':>10} {'vysledok':>10}  dovod")
for label, lk, ago, nk, pnl, expected, note in tests:
    got, why = would_escalate(lk, ago, nk, pnl, SL)
    mark = "OK" if got == expected else "CHYBA"
    ok += (got == expected); fail += (got != expected)
    print(f"  {label:<40} {str(expected):>10} {str(got):>10}  {why}  {mark}")

print("VSETKY TESTY PRESLI" if not fail else "NIEKTORE TESTY ZLYHALI")
print(f"  SPOLU OK={ok}  CHYBA={fail}")
import sys as _s; _s.exit(0 if not fail else 1)
