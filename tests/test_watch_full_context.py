"""Plny kontext watch cyklu + natiahnutie od EMA20 (2026-09-11, na ziadost pouzivatela).

POVOD: WTI #217 (long 4.3 ATR nad EMA20, maximum 48h rozpatia, RSI 79/82/83)
a NIGHT #218 (short na minime 48h, RSI 17) - oba z watch cyklu, ktory napisal
"podmienka z minuleho cyklu je splnena". Tri zmeny:
  1. watch cyklus vidi CELU uvahu cyklu, ktory watch nastavil, a porovnanie
     vtedy vs teraz; konfrontacia je SYMETRICKA (zdovodnit aj vykonanie planu)
  2. TA pole extension (vzdialenost od EMA20 v ATR, poloha v 48h rozpati)
  3. performance_facts rozpis vysledkov podla vzdialenosti od EMA20
Test nevola siet ani Clauda.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/watchfull.db"
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import claude_analyst  # noqa: E402
import market_data  # noqa: E402
import performance_facts as pf  # noqa: E402
import trade_cycle as tc  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<64} {got!r} (ocakavane {want!r})")


print("1) Natiahnutie - presne cisla WTI #217 v case vstupu")
check("4.31 ATR nad EMA20", market_data.ema20_distance_atr(101.9318, 98.281274, 0.847251), 4.31)
check("pod EMA20 je zaporne", market_data.ema20_distance_atr(95.0, 98.0, 1.0), -3.0)
check("ATR 0 -> None (nie delenie nulou)", market_data.ema20_distance_atr(100, 99, 0), None)
check("chybajuce cislo -> None", market_data.ema20_distance_atr(None, 99, 1), None)
candles = [[0, 101.0, 92.972, 0], [0, 101.9318, 95.0, 0]]
check("na maxime 48h = 100 %", market_data.range48h_position_pct(candles, 101.9318), 100.0)
check("na minime = 0 %", market_data.range48h_position_pct(candles, 92.972), 0.0)
check("ploche svieky -> None", market_data.range48h_position_pct([[0, 5, 5, 0]], 5), None)

print("\n2) V smere obchodu (to iste cislo, ktore pocitaju fakty)")
ta_wti = {"last_price": 101.9318, "ema20": 98.281274, "atr14": 0.847251}
check("long nad EMA20 -> kladne", pf._extension_in_direction(ta_wti, "long"), 4.31)
check("short nad EMA20 -> zaporne (proti)", pf._extension_in_direction(ta_wti, "short"), -4.31)
check("NIGHT short 0.02062 pod EMA20 0.022105 -> natiahnute v smere",
      pf._extension_bucket(pf._extension_in_direction(
          {"last_price": 0.02062, "ema20": 0.022105, "atr14": 0.000297}, "short")), "far")
check("pole extension ma prednost pred dopoctom",
      pf._extension_in_direction({"extension": {"ema20_distance_atr": 1.0}, **ta_wti}, "long"), 1.0)
check("hranice kosov", [pf._extension_bucket(x) for x in (-0.1, 0, 1.49, 1.5, 2.99, 3.0)],
      ["against", "early", "early", "mid", "mid", "far"])

print("\n3) Snimok 'vtedy' aj zo stareho TA bez pola extension")
snap = tc._ta_snapshot_for_compare({**ta_wti, "rsi14": 78.8, "change_24h_pct": 6.65, "trend": "strong_uptrend"})
check("vzdialenost sa dopocita", snap["ema20_distance_atr"], 4.31)
check("RSI sa prenesie", snap["rsi14"], 78.8)
check("poloha v rozpati bez extension -> None", snap["range48h_position_pct"], None)
check("nie-dict TA -> None", tc._ta_snapshot_for_compare(None), None)

print("\n4) Watch blok v prompte")
import assets  # noqa: E402
# Skutocny asset (ako test_watch_break_gate) - umely dict by musel kopirovat
# vsetky kluce, ktore _build_user_prompt pouziva (sl_pct, tp_pct, ...).
A = next(a for a in assets.ALL_ASSETS if a["name"] == "WTI")
reasoning_then = ("Rally pokracuje na eskalacii v Hormuze. Vstup az po udrzatelnom close nad 102 "
                  "{nie len knot} - RSI uz je vysoko, takze plan je opatrny.")
wsc = {"created_at": None, "live_price": 100.9, "direction": "none", "confidence": 48,
       "watch_price": 102.0, "watch_direction": "above", "watch_rationale": "potvrdenie nad 102",
       "reasoning": reasoning_then, "key_assumptions": "Iran-US eskalacia pokracuje.",
       "ta_then": {"last_price": 100.9, "rsi14": 71.2, "change_24h_pct": 4.1,
                   "ema20_distance_atr": 3.1, "range48h_position_pct": 94.0, "trend": "strong_uptrend"}}
ta_now = {"last_price": 102.3, "atr14": 0.847, "rsi14": 78.8, "change_24h_pct": 6.65,
          "trend": "strong_uptrend", "recent_candles": [],
          "extension": {"ema20_distance_atr": 4.31, "range48h_position_pct": 100.0, "note": "x"}}
p = claude_analyst._build_user_prompt(A, ta_now, {}, {}, [], None, None, watch_set_context=wsc)
check("nadpis bloku ostal", "TVOJOU VLASTNOU watch podmienkou" in p, True)
check("CELA vtedajsia uvaha je v prompte", reasoning_then in p, True)
check("  vratane {zatvoriek} (f-string, nie .format - nespadne)", "{nie len knot}" in p, True)
check("vtedajsie predpoklady", "Iran-US eskalacia pokracuje." in p, True)
check("porovnanie vtedy vs teraz", "Vtedy vs. teraz:" in p, True)
check("  RSI 71.2 -> 78.8", "RSI14 (1h): vtedy 71.2 → teraz 78.8" in p, True)
check("  EMA20 3.1 -> 4.31 ATR", "vzdialenosť od EMA20 (ATR): vtedy 3.1 → teraz 4.31" in p, True)
check("  48h 94 -> 100 %", "poloha v 48h rozpätí %: vtedy 94.0 → teraz 100.0" in p, True)
check("symetricka konfrontacia (aj pri vykonani planu)",
      "ROVNAKO vtedy, keď plán vykonávaš, ako keď ho meníš" in p, True)
check("plan nie je zavazok", "Pôvodný plán NIE JE záväzok" in p, True)
check("stara jednostranna veta je prec", "Ak TERAZ voliš iný smer/confidence" in p, False)
check("varovanie pred fade ostalo", "OSOBITNE POZOR, ak chceš ísť PROTI smeru" in p, True)
wsc_old = {k: v for k, v in wsc.items() if k not in ("reasoning", "key_assumptions", "ta_then")}
p2 = claude_analyst._build_user_prompt(A, ta_now, {}, {}, [], None, None, watch_set_context=wsc_old)
check("stary kontext bez uvahy nespadne a blok je tam", "TVOJOU VLASTNOU watch podmienkou" in p2, True)
check("  bez uvahy ziadny prazdny odsek", "CELÁ úvaha" in p2, False)
p3 = claude_analyst._build_user_prompt(A, ta_now, {}, {}, [], None, None, watch_set_context=None)
check("beh bez watchu -> ziadny blok", "Vtedy vs. teraz" in p3, False)
check("TA pole extension ide do promptu (nie je v _TA_PROMPT_DROP)",
      "extension" in claude_analyst._ta_for_prompt(ta_now), True)

print("\n5) Fakty o vykonnosti - rozpis az od MIN_ROW")
row = lambda ext, R: {"symbol": "X", "dir": "long", "R": R, "pnl": R, "win": R > 0,  # noqa: E731
                      "adx": None, "src": "scheduled", "conf": None, "opened_at": pf.NEW_SCALE_FROM,
                      "closed_at": None, "h0": None, "mom": None, "ext": ext}
rows = [row(2.0, 0.5)] * 25 + [row(4.0, -0.5)] * 25 + [row(0.5, 0.1)] * 5
facts = {"window_days": 30, "portfolio": pf._stat(rows),
         "regime": {k: pf._stat([]) for k in ("trending", "developing", "weak_no_trend")},
         "direction": {k: pf._stat([]) for k in ("long", "short")},
         "momentum": {"with": pf._stat([]), "against": pf._stat([])},
         "source": {k: pf._stat([]) for k in ("watch", "scheduled")},
         "extension": {k: pf._stat([r for r in rows if pf._extension_bucket(r["ext"]) == k])
                       for k, _ in pf.EXTENSION_BUCKETS},
         "recent": pf._stat([]), "ticker": pf._stat([]), "calibration": {"n": 0, "buckets": {}}}
txt = pf.format_text(facts, "X")
check("nadpis rozpisu", "podľa vzdialenosti ceny od EMA20 pri vstupe" in txt, True)
check("1.5-3 ATR (n=25) zobrazene", "v smere, 1.5 až 3 ATR od EMA20: 25 obchodov" in txt, True)
check("3+ ATR (n=25) zobrazene", "v smere, 3 a viac ATR od EMA20: 25 obchodov" in txt, True)
check("pod 1.5 ATR (n=5) skryte - maly vzorok", "menej ako 1.5 ATR" in txt, False)
check("slovo chase sa nepouziva", "chase" in txt.lower(), False)
check("ziadny prah ani odporucanie", any(w in txt for w in ("nevstupuj", "neotváraj", "prah")), False)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
