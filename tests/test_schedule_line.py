"""Testy bloku o najblizsom planovanom behu.

Rekonstruuje BTC pripad z 3.9.: watch trigger o 19:07 UTC, slot 7, interval 2h,
takze dalsi PLANOVANY beh bol o 20:30 - nie o 21:07, ako by vyplyvalo z
"cyklus bezi kazdych 2h".
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/sl.db"
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst  # noqa: E402
import trade_cycle  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<60} {got!r:>10} (ocakavane {want!r})")


TA = {"last_price": 100.0, "atr14": 2.0}


def build(sched):
    return claude_analyst._build_user_prompt(
        assets.enabled_assets()[0], TA, {}, {"session": "US"}, [], None, None,
        schedule=sched)


print("=" * 100)
print("1) BTC PRIPAD - mriezka je ukotvena, nie 'o interval od teraz'")
print("=" * 100)
# POZOR: lokalny ENV ma iny pocet aktivnych tickerov nez produkcia, takze BTC
# tu dostane iny slot nez v realnej prevadzke (kde vyslo 20:30). Testuje sa
# preto VLASTNOST mriezky, nie konkretne produkcne cislo.
btc = next(a for a in assets.enabled_assets() if a["strike_symbol"] == "BTC-USD")
now = datetime(2026, 9, 3, 19, 7, tzinfo=timezone.utc)
ctx = trade_cycle._schedule_context(btc, now)
nr = ctx["next_run"]
iv = ctx["interval_hours"]
print(f"  BTC slot {btc['run_slot']} (+{btc.get('run_slot_hour_offset') or 0}h), "
      f"platny interval {iv}h")
print(f"  cyklus o {now:%H:%M} -> najblizsi planovany beh {nr:%H:%M} UTC "
      f"(o {(nr-now).total_seconds()/60:.0f} min)")
check("je v buducnosti", nr > now, True)
check("nie je dalej nez jeden interval", (nr - now).total_seconds()/3600 <= iv + 0.01, True)
prev_pt = trade_cycle._slot_due_point(now, iv, btc["run_slot"],
                                      btc.get("run_slot_hour_offset") or 0)
check("lezi presne na mriezke (interval od predchadzajuceho bodu)",
      abs((nr - prev_pt).total_seconds()/3600 - iv) < 0.01, True)
check("NIE je to len 'teraz + interval'",
      nr != now + timedelta(hours=iv), True)

print()
print("=" * 100)
print("2) BLOK JE V PROMPTE A HOVORI KONKRETNY CAS")
print("=" * 100)
# blok sa vlozi len ked je next_run v BUDUCNOSTI voci skutocnemu teraz
REAL = datetime.now(timezone.utc)
ctx_now = {"next_run": REAL + timedelta(minutes=83), "interval_hours": ctx["interval_hours"]}
nr = ctx_now["next_run"]
p = build(ctx_now)
check("blok je v prompte", "Najbližší PLÁNOVANÝ beh" in p, True)
check(f"obsahuje konkretny cas {nr:%H:%M}", f"{nr:%H:%M} UTC" in p, True)
check("hovori o watchi ako jedinom sposobe", "watch je jediný spôsob" in p, True)
check("priznava, ze je to odhad", "Je to odhad" in p, True)
i = p.index("Najbližší PLÁNOVANÝ")
print("\n  --- vlozeny riadok ---")
for ln in p[i - 2:i + 420].splitlines()[:6]:
    print("   ", ln)

print()
print("=" * 100)
print("3) FORMAT ODSTUPU A REALNE PLATNY INTERVAL")
print("=" * 100)
p2 = build({"next_run": REAL + timedelta(minutes=83), "interval_hours": 2.0})
check("pod 2h sa uvadza v minutach", "o 83 min" in p2, True)
p3 = build({"next_run": REAL + timedelta(hours=5.5), "interval_hours": 6.0})
check("nad 2h sa uvadza v hodinach", "o 5.5 h" in p3, True)
check("hlavicka pouzije PLATNY interval (6h), nie trade_interval",
      "beží každých 6.0h" in p3, True)

print()
print("=" * 100)
print("4) HRANICNE PRIPADY - blok sa nesmie objavit s nezmyslom")
print("=" * 100)
check("bez schedule sa blok nevlozi",
      "Najbližší PLÁNOVANÝ beh" in build(None), False)
check("bez schedule prompt stale funguje", len(build(None)) > 200, True)
check("next_run=None -> ziadny blok",
      "Najbližší PLÁNOVANÝ beh" in build({"next_run": None, "interval_hours": 2.0}), False)
check("next_run v minulosti -> ziadny blok",
      "Najbližší PLÁNOVANÝ beh" in build(
          {"next_run": REAL - timedelta(minutes=5), "interval_hours": 2.0}), False)

print()
print("=" * 100)
print("5) PLATI TO AJ PRI OTVORENEJ POZICII (health check vetva)")
print("=" * 100)
op = {"direction": "Long", "entry_price": 100.0, "live_price": 101.0,
      "stop_loss_price": 97.0, "take_profit_price": 105.0, "leverage": 3,
      "opened_at_str": "2026-09-03 10:00", "hours_held": 2.0,
      "unrealized_pnl_usd": 5.0, "unrealized_pnl_pct": 10.0}
hp = claude_analyst._build_user_prompt(
    assets.enabled_assets()[0], TA, {}, {"session": "US"}, [], None, None,
    open_position=op, schedule=ctx_now)
check("blok je aj v health vetve", "Najbližší PLÁNOVANÝ beh" in hp, True)
check("health blok ostal", "OTVORENÁ POZÍCIA" in hp, True)

print()
print("=" * 100)
print("6) NEZAVISLOST OD MINUTY BEHU - mriezka drzi, nech volam kedykolvek")
print("=" * 100)
seen = set()
for m in (0, 7, 31, 59):
    t = datetime(2026, 9, 3, 19, m, tzinfo=timezone.utc)
    r = trade_cycle._schedule_context(btc, t)["next_run"]
    print(f"    cyklus 19:{m:02d} -> dalsi planovany {r:%H:%M}")
    seen.add(r)
check("vsetky styri daju TEN ISTY bod mriezky (nezavisle od minuty behu)",
      len(seen), 1)

print()
print("=" * 100)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 100)
sys.exit(0 if ok else 1)
