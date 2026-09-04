"""Testy pre presun planovanych cyklov na pozadie (run_all_cycles).

Reprodukuje povod problemu: seriovy for-loop v tiku -> tick trva tolko, co
vsetky cykly dokopy -> APScheduler (max_instances=1) preskoci nasledujuce tiky
-> tickery sa nakopia a spustia naraz.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
import threading
import time

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/disp.db"
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import trade_cycle  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<64} {got!r:>8} (ocakavane {want!r})")


CYCLE_SECONDS = 0.4
act = assets.enabled_assets()[:8]

started, finished = [], []
peak = [0]
live = [0]
lk = threading.Lock()
snapshots = []


def fake_cycle(asset, cross_market, market_session, btc_proxy, fred_macro=None, **kw):
    with lk:
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        started.append((asset["name"], time.monotonic()))
        snapshots.append(id(cross_market))
    time.sleep(CYCLE_SECONDS)
    with lk:
        live[0] -= 1
        finished.append(asset["name"])


trade_cycle.run_cycle_for_asset = fake_cycle

print("=" * 100)
print("1) TICK SA VRATI HNED, NECAKA NA CYKLY")
print("=" * 100)
shared = {"sp500": 1}
t0 = time.monotonic()
for a in act:
    trade_cycle._dispatch_scheduled_cycle(a, shared, {"s": 1}, None, {"f": 1})
tick_ms = (time.monotonic() - t0) * 1000
print(f"  dispatch {len(act)} tickerov trval {tick_ms:.0f} ms "
      f"(seriovo by to bolo {len(act)*CYCLE_SECONDS*1000:.0f} ms)")
check("tick skoncil rychlejsie nez jeden cyklus", tick_ms < CYCLE_SECONDS * 1000, True)

print()
print("=" * 100)
print("2) DRUHY TICK POCAS BEHU NESPUSTI TICKER ZNOVA (in-flight poistka)")
print("=" * 100)
before = len(started)
for a in act:
    trade_cycle._dispatch_scheduled_cycle(a, shared, {"s": 1}, None, {"f": 1})
time.sleep(0.05)
check("druhy dispatch nespustil ziadny dalsi beh", len(started) - before, 0)

# dobehni
deadline = time.monotonic() + 10
while len(finished) < len(act) and time.monotonic() < deadline:
    time.sleep(0.05)
check("vsetky cykly dobehli", len(finished), len(act))

print()
print("=" * 100)
print("3) BEZALI SUBEZNE, ALE OHRANICENE SEMAFOROM")
print("=" * 100)
print(f"  spicka suběžnych cyklov: {peak[0]}, strop "
      f"_DISPATCH_CONCURRENCY_LIMIT={trade_cycle._DISPATCH_CONCURRENCY_LIMIT}")
check("bezali naozaj subezne (nie seriovo)", peak[0] > 1, True)
check("spicka neprekrocila strop",
      peak[0] <= trade_cycle._DISPATCH_CONCURRENCY_LIMIT, True)
span = max(t for _, t in started) - min(t for _, t in started)
print(f"  vsetkych {len(act)} sa rozbehlo v okne {span*1000:.0f} ms")

print()
print("=" * 100)
print("4) ZDIELANY SNAPSHOT - kazdy cyklus dostal TEN ISTY objekt (ziadny fetch navyse)")
print("=" * 100)
check("vsetky cykly dostali identicky cross_market objekt",
      len(set(snapshots)), 1)

print()
print("=" * 100)
print("5) PO DOBEHNUTI JE IN-FLIGHT MNOZINA CISTA (da sa spustit znova)")
print("=" * 100)
check("ziadny symbol neostal v in-flight",
      any(a["strike_symbol"] in trade_cycle._triggered_check_in_flight for a in act), False)
started.clear(); finished.clear()
trade_cycle._dispatch_scheduled_cycle(act[0], shared, {"s": 1}, None, {"f": 1})
time.sleep(0.05)
check("dalsi tick uz ticker spusti", len(started), 1)
deadline = time.monotonic() + 5
while len(finished) < 1 and time.monotonic() < deadline:
    time.sleep(0.05)

print()
print("=" * 100)
print("6) VYNIMKA V JEDNOM TICKERI NEZHODI OSTATNE ANI NEZASEKNE IN-FLIGHT")
print("=" * 100)
done = []


def boom(asset, *a, **kw):
    if asset["name"] == act[0]["name"]:
        raise RuntimeError("simulovana chyba")
    done.append(asset["name"])


trade_cycle.run_cycle_for_asset = boom
for a in act[:3]:
    trade_cycle._dispatch_scheduled_cycle(a, shared, {"s": 1}, None, {"f": 1})
deadline = time.monotonic() + 5
while len(done) < 2 and time.monotonic() < deadline:
    time.sleep(0.05)
check("ostatne tickery zbehli aj napriek vynimke", len(done), 2)
time.sleep(0.1)
check("padnuty ticker sa uvolnil z in-flight",
      act[0]["strike_symbol"] in trade_cycle._triggered_check_in_flight, False)

print()
print("=" * 100)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 100)
sys.exit(0 if ok else 1)
