"""Nepouzitelna odpoved od Clauda sa musi dat DIAGNOSTIKOVAT, nie len zahodit.

POVOD: 11 vyskytov od 6.8. naprie 7 tickermi (naposledy NIGHT 8.9.) -
`_validate_decision` hlasi "Chýbajúce polia v rozhodnutí" so VSETKYMI povinnymi
polami naraz. Nastroj sa teda ZAVOLAL, ale s prazdnym vstupom; podpis orezania
na max_tokens (povinne polia su v schéme az za volitelnymi reflection polami).

Problem bol, ze sa o tom nedalo zistit NIC:
  - `usage` vyletelo z analyze() spolu s vynimkou EŠTE PREDTYM, nez si ho
    trade_cycle priradil -> v DB same NULL a cyklus vyzeral ako bezplatny,
    hoci sme za tokeny zaplatili (~$2 za 11 vyskytov, ale hlavne to skresluje
    porovnanie s Claude konzolou);
  - `stop_reason` sa nikam neukladal, takze "bolo to orezanie?" sa dalo len
    hadat. Zdvihnutie stropu 8192 -> 16000 (22.8.) znizilo frekvenciu z 8/16 dni
    na 3/17 dni, co strop implikuje, ale nedokazuje.

Test nechodi na siet ani do DB.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/mdec.db"
sys.path.insert(0, _ROOT)

import claude_analyst  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


print("1) MalformedDecision nesie vsetko potrebne na diagnostiku")
usage = {"input_tokens": 7, "output_tokens": 15998, "cache_read_tokens": 90000,
         "stop_reason": "max_tokens"}
e = claude_analyst.MalformedDecision("Chýbajúce polia", usage=usage,
                                      web_search_log=[{"q": "x"}],
                                      present_keys=["watch_price"])
check("je to ValueError (volajuci ju chytia ako doteraz)", isinstance(e, ValueError), True)
check("nesie usage", e.usage["output_tokens"], 15998)
check("nesie stop_reason", e.usage["stop_reason"], "max_tokens")
check("nesie, ktore kluce PRISLI", e.present_keys, ["watch_price"])
check("nesie web_search_log", len(e.web_search_log), 1)

print("\n2) Prazdne/chybajuce hodnoty nezhodia konstruktor")
e2 = claude_analyst.MalformedDecision("x")
check("usage default", e2.usage, None)
check("present_keys default je zoznam", e2.present_keys, [])

print("\n3) Poistka v trade_cycle vytiahne usage z vynimky")
# Presne ten vyraz, ktory je v poistke - ked lokalne `usage` este nie je
# priradene (pad nastal vnutri analyze()), musi sa vziat z vynimky.
def fuse_usage(local_usage, exc):
    return local_usage or getattr(exc, "usage", None) or {}


check("lokalne None -> berie z vynimky", fuse_usage(None, e)["output_tokens"], 15998)
check("lokalne uz je -> ma prednost", fuse_usage({"output_tokens": 42}, e)["output_tokens"], 42)
check("ina vynimka bez usage -> prazdny dict", fuse_usage(None, ValueError("iné")), {})

print("\n4) _call_claude vklada stop_reason do usage_record")
src = open(os.path.join(_ROOT, "claude_analyst.py"), encoding="utf-8").read()
check('usage_record obsahuje "stop_reason"',
      '"stop_reason": data.get("stop_reason")' in src, True)

print("\n5) Strop tokenov pre bezny effort je zdvihnuty na 24000")
# Vsetky tickery bezia na effort=high, cize na tejto vetve. Namerany max vystup
# je 20448 tokenov - ale to je SUCET cez az dve volania (pause_turn), takze
# o skutocnom jednom volani to nehovori. Zdvihnutie nic nestoji: max_tokens je
# strop, nie poplatok.
import re  # noqa: E402
block = re.search(r"effort = asset\.get\(\"effort\"\)(.*?)\n\n", src, re.S).group(1)
check("xhigh/max ostava 24000", "max_tokens = 24000" in block, True)
check("low/medium ostava 8192", "max_tokens = 8192" in block, True)
check("default vetva uz NIE JE 16000", "16000" in block, False)

print("\n6) analyze() balí zlyhanu validaciu do MalformedDecision")
check("analyze zachytava ValueError z _validate_decision",
      "raise MalformedDecision(str(e), usage=usage" in src, True)
check("a loguje stop_reason aj prisle kluce",
      "stop_reason={usage.get('stop_reason')}" in src, True)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
