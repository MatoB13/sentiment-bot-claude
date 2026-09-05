"""_save_flagged_macro_event nesmie zhodit cyklus ZIADNYM tvarom vstupu.

POVOD (produkcny pad 5.9.2026, AAOI): schema deklaruje `upcoming_macro_event`
ako "object", ale Claude vratil obycajny RETAZEC. `.get()` na nom vyhodilo
AttributeError, ktory zhodil CELY cyklus - az PO zaplatenej analyze (6329 output
tokenov zahodenych). Docstring tej funkcie pritom vyslovne slubuje, ze cyklus
nikdy nezhodi; povodna poistka ale strazila len zly DATUM, nie zly TYP.

Chytila to poistka z 272d96b (zapisala 'error' riadok, takze sa beh neopakoval)
a pruh zdravia na dashboarde ju ukazal - inak by to bol dalsi tichy vypadok.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

DB = os.environ["TEMP"].replace("\\", "/") + "/macroev.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import trade_cycle  # noqa: E402
from db import FlaggedMacroEvent, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<56} {got!r:>9} (ocakavane {want!r})")


s = get_session()


def call(event):
    """True ak funkcia prezila (nezhodila cyklus)."""
    try:
        trade_cycle._save_flagged_macro_event(event, "TEST-USD", s)
        return True
    except Exception as e:
        print(f"       vynimka: {type(e).__name__}: {e}")
        return False


print("1) ZLY TYP nesmie zhodit cyklus (jadro incidentu)")
check("retazec (presne to, co poslal Claude)", call("FOMC 16.9.2026"), True)
check("prazdny retazec", call(""), True)
check("cislo", call(12345), True)
check("zoznam", call([{"name": "FOMC"}]), True)
check("boolean", call(True), True)

print("\n2) Chybajuce/prazdne hodnoty prejdu ako doteraz")
check("None", call(None), True)
check("prazdny dict", call({}), True)
check("dict bez datumu", call({"name": "FOMC"}), True)
check("dict bez nazvu", call({"datetime_utc": "2026-09-16T18:00:00Z"}), True)

print("\n3) Zly DATUM (povodna poistka) stale funguje")
check("nezmyselny datum", call({"name": "X", "datetime_utc": "zajtra vecer"}), True)

print("\n4) Ziadny z tychto vstupov nesmel nic zapisat")
check("pocet ulozenych udalosti", s.query(FlaggedMacroEvent).count(), 0)

print("\n5) PLATNY vstup sa nadalej ulozi (poistka nesmie zablokovat funkciu)")
check("platna udalost prejde",
      call({"name": "FOMC", "datetime_utc": "2026-09-16T18:00:00Z"}), True)
s.commit()
saved = s.query(FlaggedMacroEvent).all()
check("ulozena prave jedna", len(saved), 1)
check("s nazvom", saved[0].name if saved else None, "FOMC")

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
