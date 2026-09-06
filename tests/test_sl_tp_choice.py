"""Volba SL/TP pri otvoreni + strukturovane odporucanie pri zatvoreni.

POVOD (obe na ziadost pouzivatela, 2026-09-06):

1. Pri otvarani pozicie nebolo NIKDE vidiet, ci Claude pouzil kalibrovany
   default alebo vlastnu hodnotu v pasme 0.5x-5x (risk_manager.SAFETY_FLOOR/
   CAP_MULTIPLE), ani preco - takze sa spatne nedalo posudit, ci su jeho
   odchylky opodstatnene. Novy sl_tp_choice to zapisuje do historie cyklu.

2. Kalibracny formular na dashboarde lovil odporucane cisla REGEXOM z prozy
   verdiktu a mylil sa: naivne oddelene hladanie "SL...%" a "TP...%" priradilo
   OBOM to iste prve cislo v texte. Claude ich teraz vracia aj strukturovane
   (optimal_sl_pct/optimal_tp_pct) a formular berie prednostne tie.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/sltpchoice.db"
sys.path.insert(0, _ROOT)

import claude_analyst  # noqa: E402
import risk_manager  # noqa: E402
import trade_cycle  # noqa: E402
from db import CycleLog  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


PROPS = claude_analyst.DECISION_TOOL["input_schema"]["properties"]

print("1) Nove polia su v schéme rozhodovacieho nastroja")
for field, typ in [("sl_tp_choice", "string"), ("optimal_sl_pct", "number"),
                   ("optimal_tp_pct", "number")]:
    check(f"{field} existuje", field in PROPS, True)
    check(f"{field} je {typ}", PROPS.get(field, {}).get("type"), typ)

print("\n2) Ziadne z nich nie je POVINNE")
# sl_tp_choice sa pri direction='none' vynechava (ziadna pozicia nevznika),
# optimal_* len pri post-close review - povinnost by vynutila vypln vzdy.
req = claude_analyst.DECISION_TOOL["input_schema"]["required"]
for field in ("sl_tp_choice", "optimal_sl_pct", "optimal_tp_pct"):
    check(f"{field} nie je v required", field in req, False)

print("\n3) Popisy SL/TP hovoria o SKUTOCNOM pasme (0.5x-5x), nie 'mierne'")
# Do 6.9. schema pasmo nespominala vobec a prompt tvrdil "nie vyrazne mimo",
# hoci risk_manager realne pripusta 0.5x-5x. Claude tak nevedel, kolko slobody
# ma - a pouzivatel nevedel, ze ju ma.
band = f"{risk_manager.SAFETY_FLOOR_MULTIPLE}x az {risk_manager.SAFETY_CAP_MULTIPLE}x"
check("pasmo v kode je stale 0.5x-5x", band, "0.5x az 5.0x")
for field in ("stop_loss_price", "take_profit_price"):
    d = PROPS[field]["description"]
    check(f"{field} spomina 0.5x", "0.5x" in d, True)
    check(f"{field} spomina 5x", "5x" in d, True)
check("popis TP spomina podlahu pomeru TP/SL",
      "TP/SL" in PROPS["take_profit_price"]["description"], True)

print("\n4) Verdikt si vynucuje zaverecnu vetu s konkretnymi cislami")
vd = PROPS["sl_tp_calibration_verdict"]["description"]
check("ziada POSLEDNU VETU", "POSLEDNÁ VETA" in vd, True)
check("uvadza presny tvar vety", "Optimálne SL/TP pre tento ticker" in vd, True)
check("ziada vyplnit aj strukturovane polia", "optimal_sl_pct" in vd, True)

print("\n5) _positive_pct - percento, nie absolutna cena a nie odpad")
f = trade_cycle._positive_pct
check("bezne percento prejde", f(2.14), 2.14)
check("retazec z modelu sa skonvertuje", f("3.5"), 3.5)
check("None -> None", f(None), None)
check("nezmysel -> None", f("dve percenta"), None)
check("nula -> None", f(0), None)
check("zaporne -> None", f(-1.5), None)
# Hlavny cakany omyl: model vrati ABSOLUTNU CENU namiesto percenta.
check("NAS100 cena (28000) -> None", f(28000), None)
check("GOLD cena (3400) -> None", f(3400), None)
check("hranica 50 este prejde", f(50.0), 50.0)
check("tesne nad hranicou -> None", f(50.01), None)

print("\n6) sl_tp_choice sa zapisuje LEN ked z cyklu vzisiel smer")
# Poistka v trade_cycle: pri direction='none' ziadna pozicia nevznika, takze
# "ake SL/TP som zvolil" nema co popisovat. Claude to ma vynechat sam, ale
# nespoliehame sa na to - overujeme presne ten vyraz, ktory je v kode.
def stored(direction, value):
    return (value if str(direction or "").lower() in ("long", "short") else None)


check("long -> zapise sa", stored("long", "DEFAULT: ..."), "DEFAULT: ...")
check("short -> zapise sa", stored("Short", "VLASTNE: ..."), "VLASTNE: ...")
check("none -> zahodi sa", stored("none", "DEFAULT: ..."), None)
check("chybajuci smer -> zahodi sa", stored(None, "DEFAULT: ..."), None)

print("\n7) CycleLog ma stlpce, do ktorych sa to zapisuje")
for col in ("sl_tp_choice", "optimal_sl_pct", "optimal_tp_pct"):
    check(f"cycle_logs.{col}", col in CycleLog.__table__.columns, True)

print("\n8) Zachrana poskodenej odpovede pozna nove polia")
# Realny opakovany jav: Claude niekedy vrati pole ako obycajny <tag> v texte
# namiesto tool-call parametra (viz _recover_malformed_fields). Nove polia
# musia byt v zoznamoch, inak by sa taka hodnota ticho stratila.
check("sl_tp_choice v _PLAIN_TAG_FIELDS",
      "sl_tp_choice" in claude_analyst._PLAIN_TAG_FIELDS, True)
check("optimal_sl_pct sa parsuje ako CISLO",
      "optimal_sl_pct" in claude_analyst._NUMERIC_FIELDS, True)
check("optimal_tp_pct sa parsuje ako CISLO",
      "optimal_tp_pct" in claude_analyst._NUMERIC_FIELDS, True)
check("sl_tp_choice moze byt hostitelom zvysku",
      "sl_tp_choice" in claude_analyst._HOST_FIELDS, True)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
