"""Brana na MRTVE PASMO dennej zmeny pri watch vstupoch.

NAMERANE na 101 uzavretych watch obchodoch (delene podla |change_24h_pct| v case
vstupu): v pasme 3.2-5.8 % bol JEDEN ziskovy obchod z 23 (-1217 $, median -30 $).
Drzi to aj bez ZEC a ADA (18 obchodov, 6 % uspesnost), takze to nie je o dvoch
tickeroch. Susedne pasma su pritom v poriadku az dobre: pod 2 % priemer -5 $,
nad 8 % priemer +30 $ a median +24 $.

ZAMER POUZIVATELA, ktory brana NESMIE porusit: bot ma zarabat na crashoch a
prudkych narastoch, takze velke pohyby musia prejst. Brana blokuje VYHRADNE to
stredne pasmo - "chase po slusnom, ale nie vynimocnom pohybe".
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/deadband.db"
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import trade_cycle  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r:>7} (ocakavane {want!r})")


def blocked(move, direction="long", watch=True):
    """True ak brana vstup zamietne."""
    r = trade_cycle._watch_in_dead_band(
        {"direction": direction}, watch, {"change_24h_pct": move})
    return r is not None


print(f"pasmo z configu: {config.WATCH_DEAD_BAND_MIN_24H_PCT} - "
      f"{config.WATCH_DEAD_BAND_MAX_24H_PCT} %\n")

print("1) ZAMER POUZIVATELA: crashe a narasty MUSIA prejst")
check("pad -12 % (crash)", blocked(-12.0), False)
check("narast +9.5 %", blocked(9.5), False)
check("extrem +42 %", blocked(42.2), False)
check("presne na hornej hranici 6 %", blocked(6.0), False)

print("\n2) Pokojny trh prejde tiez (namerane pasmo pod 2 % je len mierne zaporne)")
check("+0.4 %", blocked(0.4), False)
check("-1.8 %", blocked(-1.8), False)
check("+2.9 %", blocked(2.9), False)

print("\n3) MRTVE PASMO sa zamietne, v oboch smeroch")
check("+3.0 % (dolna hranica, vratane)", blocked(3.0), True)
check("+4.5 %", blocked(4.5), True)
check("-5.2 % (pad rovnako ako rast)", blocked(-5.2), True)
check("+5.99 %", blocked(5.99), True)
check("short v mrtvom pasme", blocked(4.5, direction="short"), True)

print("\n4) Brana sa tyka LEN watch vstupov, ktore chcu otvorit")
check("planovany cyklus v mrtvom pasme", blocked(4.5, watch=False), False)
check("watch cyklus s direction=none", blocked(4.5, direction="none"), False)

print("\n5) Chybajuce data = fail-open (nikdy neblokuj naslepo)")
check("change_24h_pct chyba",
      trade_cycle._watch_in_dead_band({"direction": "long"}, True, {}) is not None, False)
check("ta je None",
      trade_cycle._watch_in_dead_band({"direction": "long"}, True, None) is not None, False)
check("change_24h_pct je None",
      trade_cycle._watch_in_dead_band({"direction": "long"}, True,
                                       {"change_24h_pct": None}) is not None, False)
check("change_24h_pct je nezmysel",
      trade_cycle._watch_in_dead_band({"direction": "long"}, True,
                                       {"change_24h_pct": "n/a"}) is not None, False)

print("\n6) Brana sa da vypnut cez ENV")
orig = config.WATCH_DEAD_BAND_MIN_24H_PCT
config.WATCH_DEAD_BAND_MIN_24H_PCT = 0
check("min=0 branu vypne", blocked(4.5), False)
config.WATCH_DEAD_BAND_MIN_24H_PCT = orig
check("po obnoveni zase blokuje", blocked(4.5), True)

print("\n7) Dovod zamietnutia nesie cislo, nech je na dashboarde citatelny")
reason = trade_cycle._watch_in_dead_band({"direction": "long"}, True,
                                          {"change_24h_pct": -4.5})
check("prefix", str(reason).startswith("watch_dead_band:"), True)
check("obsahuje nameranu hodnotu", "4.5 %" in str(reason), True)
print(f"       {reason}")

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
