"""Sken musi prezit poskodenu odpoved rovnako ako plny cyklus.

POVOD (produkcny nalez 2026-09-09, ZHIPU): `_call_triage` vracal
`block["input"]` UPLNE SUROVY - bez `_recover_malformed_fields` aj bez
`_strip_citation_tags`, ktore na rozhodovacej ceste bezia uz od augusta.

Namerane na 175 skutocnych verdiktov skenu: 8 (4.6 %) malo v texte uviaznuty
XML tag. Vo VSETKYCH 8 pripadoch prislo `watch_direction`, ale `watch_price`
bolo NULL - cena skoncila ako "</reason><parameter name=\"watch_price\">117.2"
v `reason`. watch_monitor potrebuje oboje, takze watch pre ten ticker ticho
nevznikol. Verdikt (worth_full_look/attention) prezil vzdy, takze na dashboarde
nebolo nic vidiet - stratil sa len watch.

Je to TEN ISTY bug ako #707/ADA, #901/XAU a #2832/NAS100 z augusta, len na
mieste, kam sa vtedajsia oprava nedostala.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/trmal.db"
sys.path.insert(0, _ROOT)

import claude_analyst  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


print("1) `reason` je hostitelske pole (inak zachrana v skene nema kde hladat)")
check("reason v _HOST_FIELDS", "reason" in claude_analyst._HOST_FIELDS, True)
check("watch_price je znamy tag", "watch_price" in claude_analyst._PLAIN_TAG_FIELDS, True)
check("watch_price sa parsuje ako cislo",
      "watch_price" in claude_analyst._NUMERIC_FIELDS, True)

print("\n2) PRESNY produkcny tvar (ZHIPU 8.9. 22:16) sa da zachranit")
# Skratena, ale znakovo verna kopia toho, co prislo z produkcie.
raw = {
    "worth_full_look": False,
    "attention": 30,
    "reason": ("Cena 119.08 je v konsolidacii tesne pod EMA20, pokracuje mierny "
               "downtrend ale bez noveho prerazenia oproti poslednemu pohladu.</reason>\n"
               '<parameter name="watch_price">117.2'),
    "watch_direction": "below",
}
fixed = claude_analyst._recover_malformed_fields(dict(raw), "ZHIPU triage")
check("watch_price sa zachranil", fixed.get("watch_price"), 117.2)
check("je to cislo, nie retazec", isinstance(fixed.get("watch_price"), float), True)
check("watch_direction ostal", fixed.get("watch_direction"), "below")
check("verdikt sa nezmenil", fixed.get("worth_full_look"), False)
check("attention sa nezmenil", fixed.get("attention"), 30)
check("z reason zmizol uniknuty tag", "<parameter" in fixed.get("reason", ""), False)
check("a zmizla aj zatvaracia znacka", "</reason>" in fixed.get("reason", ""), False)
check("zmysluplny text ostal zachovany",
      "konsolidacii tesne pod EMA20" in fixed.get("reason", ""), True)

print("\n3) Cista odpoved sa NESMIE zmenit")
clean = {"worth_full_look": True, "attention": 70,
         "reason": "Prerazenie nad 0.231 na 6x objeme, novy titulok o ETF.",
         "watch_price": 0.231, "watch_direction": "above"}
out = claude_analyst._recover_malformed_fields(dict(clean), "X triage")
check("prejde bez zmeny", out, clean)

print("\n4) Sken naozaj cisti (nie len ze funkcia existuje)")
src = open(os.path.join(_ROOT, "claude_analyst.py"), encoding="utf-8").read()
tail = src[src.index("def _call_triage"):]
tail = tail[:tail.index("\ndef ", 10)]
check("_call_triage vola _recover_malformed_fields",
      "_recover_malformed_fields(block[\"input\"]" in tail, True)
check("_call_triage vola aj _strip_citation_tags",
      "_strip_citation_tags(" in tail, True)
check("uz NEvracia surovy block['input']",
      "return block[\"input\"], usage_record" in tail, False)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
