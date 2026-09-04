"""Testy brany na plytke prerazenie watch urovne (WATCH_BREAK_MIN_ATR)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

DB = os.environ["TEMP"].replace("\\", "/") + "/wbg.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst  # noqa: E402
import config  # noqa: E402
import trade_cycle as tc  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:28]:>28} (ocakavane {want!r})")


MIN = config.WATCH_BREAK_MIN_ATR
ATR = 2.0

print("=" * 110)
print(f"1) _watch_break_context  (ATR={ATR}, prah={MIN})")
print("=" * 110)
wsc = {"watch_price": 100.0, "watch_direction": "above"}
b = tc._watch_break_context(wsc, 101.0, ATR)
check("above 100, cena 101 -> hlbka +0.5 ATR", b["depth_atr"], 0.5)
b = tc._watch_break_context(wsc, 99.5, ATR)
check("above 100, cena 99.5 (spat dnu) -> hlbka -0.25", b["depth_atr"], -0.25)
wsc = {"watch_price": 100.0, "watch_direction": "below"}
b = tc._watch_break_context(wsc, 99.0, ATR)
check("below 100, cena 99 -> +0.5", b["depth_atr"], 0.5)
wsc2 = {"watch_price": 105.0, "watch_direction": "above",
        "watch_price_2": 95.0, "watch_direction_2": "below"}
b = tc._watch_break_context(wsc2, 94.0, ATR)
check("obojstranny, cena 94 -> vybrana below 95", (b["direction"], b["level"], b["depth_atr"]), ("below", 95.0, 0.5))
b = tc._watch_break_context(wsc2, 106.0, ATR)
check("obojstranny, cena 106 -> vybrana above 105", (b["direction"], b["level"]), ("above", 105.0))
check("ATR chyba -> depth None", tc._watch_break_context(wsc, 99.0, None)["depth_atr"], None)
check("bez watchu -> None", tc._watch_break_context({}, 99.0, ATR), None)
check("bez ceny -> None", tc._watch_break_context(wsc, None, ATR), None)

print()
print("=" * 110)
print("2) _watch_break_too_shallow")
print("=" * 110)
deep = {"level": 100.0, "direction": "above", "beyond_price": 1.0, "depth_atr": MIN + 0.1}
shallow = {"level": 100.0, "direction": "above", "beyond_price": 0.1, "depth_atr": MIN - 0.1}
inside = {"level": 100.0, "direction": "above", "beyond_price": -0.5, "depth_atr": -0.25}
noatr = {"level": 100.0, "direction": "above", "beyond_price": 0.1, "depth_atr": None}
check("chase (above->long), hlboko -> pusti", tc._watch_break_too_shallow({"direction": "long"}, deep), None)
r = tc._watch_break_too_shallow({"direction": "long"}, shallow)
check("chase, plytko -> zamietne", r is not None and r.startswith("watch_break_too_shallow"), True)
r = tc._watch_break_too_shallow({"direction": "long"}, inside)
check("chase, cena spat dnu -> zamietne ('spat dnu')", r is not None and "spat dnu" in r, True)
check("PROTI prerazeniu (above->short), plytko -> pusti", tc._watch_break_too_shallow({"direction": "short"}, shallow), None)
check("none -> pusti", tc._watch_break_too_shallow({"direction": "none"}, shallow), None)
check("ATR chyba -> pusti (fail-open)", tc._watch_break_too_shallow({"direction": "long"}, noatr), None)
check("bez break kontextu -> pusti", tc._watch_break_too_shallow({"direction": "long"}, None), None)
below_sh = {"level": 100.0, "direction": "below", "beyond_price": 0.1, "depth_atr": MIN - 0.1}
r = tc._watch_break_too_shallow({"direction": "short"}, below_sh)
check("chase (below->short), plytko -> zamietne", r is not None, True)
check("presne na prahu -> pusti", tc._watch_break_too_shallow({"direction": "long"}, {**deep, "depth_atr": MIN}), None)

print()
print("=" * 110)
print("3) PROMPT - hlbka je vo watch bloku, cislo prahu NIE")
print("=" * 110)
A = assets.enabled_assets()[0]
ta = {"last_price": 101.0, "atr14": ATR, "recent_candles": []}
wsc = {"created_at": None, "live_price": 99.0, "direction": "none", "confidence": 60,
       "watch_price": 100.0, "watch_direction": "above", "watch_price_2": None, "watch_direction_2": None,
       "watch_rationale": "cakam na prielom", "break": {"level": 100.0, "direction": "above",
                                                        "beyond_price": 1.0, "depth_atr": 0.5}}
p = claude_analyst._build_user_prompt(A, ta, {}, {}, [], None, None, watch_set_context=wsc)
check("prompt obsahuje 'Hĺbka prerazenia: cena je 0.50 ATR'", "Hĺbka prerazenia: cena je 0.50 ATR" in p, True)
check("prompt NEOBSAHUJE cislo prahu", f"{MIN:.2f} ATR" in p or f"{MIN} ATR" in p, False)
check("prompt hovori o mechanickom zamietnuti", "mechanicky zamietne" in p, True)
wsc["break"] = {"level": 100.0, "direction": "above", "beyond_price": -0.5, "depth_atr": -0.25}
p = claude_analyst._build_user_prompt(A, ta, {}, {}, [], None, None, watch_set_context=wsc)
check("navrat dnu -> 'úroveň padla len knôtom'", "úroveň padla len knôtom" in p, True)
wsc["break"] = None
p = claude_analyst._build_user_prompt(A, ta, {}, {}, [], None, None, watch_set_context=wsc)
check("bez break kontextu -> ziadny riadok o hlbke", "Hĺbka prerazenia" in p, False)
p = claude_analyst._build_user_prompt(A, ta, {}, {}, [], None, None, watch_set_context=None)
check("beh bez watchu -> ziadny watch blok", "TVOJOU VLASTNOU watch podmienkou" in p, False)

print()
print("=" * 110)
print("4) config_snapshot nesie prah")
print("=" * 110)
check("watch_break_min_atr v snapshote", tc._config_snapshot(A).get("watch_break_min_atr"), MIN)

print()
print("=" * 110)
print("5) _auto_watch_after_shallow_break")
print("=" * 110)
a = tc._auto_watch_after_shallow_break({"level": 100.0, "direction": "above", "depth_atr": 0.12}, ATR)
check("above 100, ATR 2 -> watch above 100 + 0.3*2", (a["watch_direction"], a["watch_price"]), ("above", 100.0 + MIN * ATR))
check("auto uroven je NAD plytkou cenou (nie hned splnena)", a["watch_price"] > 100.0 + 0.12 * ATR, True)
a = tc._auto_watch_after_shallow_break({"level": 100.0, "direction": "below", "depth_atr": -0.25}, ATR)
check("below 100 -> watch below 100 - 0.3*2", (a["watch_direction"], a["watch_price"]), ("below", 100.0 - MIN * ATR))
check("rationale spomina navrat dnu", "vrátila dnu" in a["watch_rationale"], True)
check("rationale zacina 'auto:'", a["watch_rationale"].startswith("auto:"), True)

print()
print("=" * 110)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 110)
sys.exit(0 if ok else 1)
