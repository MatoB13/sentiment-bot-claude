"""Slotova mriezka po zmene 12 -> 20 slotov a intervalov 4/6/12 -> 6/9/12 (2026-09-14).
Overuje: default 20 slotov po 3 min a tick = sirka slotu; kazdy aktivny ticker ma
vlastny slot; body 20 slotov su pri kazdom pouzivanom intervale rozne; dashboard
(slotDuePointMs v index.html) pocita tie iste body ako bot. Bez siete a burzy."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/slotgrid.db"
for k in ("RUN_SLOT_COUNT", "SCHEDULER_TICK_MINUTES"):
    os.environ.pop(k, None)
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import config  # noqa: E402
import trade_cycle as tc  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:30]!r} (ocakavane {want!r})")


print("1) Defaulty")
check("RUN_SLOT_COUNT", config.RUN_SLOT_COUNT, 20)
check("sirka slotu 3 min", config.RUN_SLOT_WIDTH_MINUTES, 3.0)
check("tick scheduleru = sirka slotu", config.SCHEDULER_TICK_MINUTES, config.RUN_SLOT_WIDTH_MINUTES)
check("TSLA default 6/9/12", (config.TSLA_TRADE_INTERVAL_HOURS, config.TSLA_OFF_HOURS_INTERVAL_HOURS,
                               config.TSLA_WEEKEND_INTERVAL_HOURS), (6.0, 9.0, 12.0))

print("\n2) Pridelenie slotov aktivnym tickerom")
act = assets.enabled_assets()
slots = [a["run_slot"] for a in act]
if len(act) <= config.RUN_SLOT_COUNT:
    check(f"{len(act)} aktivnych -> kazdy vlastny slot", len(set(slots)), len(act))
    check("  ziadny hodinovy posun", {a["run_slot_hour_offset"] for a in act}, {0})
check("vsetky sloty v 1..20", all(1 <= s <= 20 for s in slots), True)

print("\n3) Body mriezky 20 slotov su pri kazdom intervale rozne")
now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
for iv in (2, 3, 4, 6, 9, 12):
    pts = {tc._slot_due_point(now, iv, s, 0) for s in range(1, 21)}
    check(f"interval {iv} h: 20 roznych bodov", len(pts), 20)
p1, p2 = (tc._slot_due_point(now, 9, s, 0) for s in (1, 2))
check("krok medzi slotom 1 a 2 je 63 min (mod 9 h)",
      round(((p2 - p1).total_seconds() / 60) % 540), 63)

print("\n4) Parita s dashboardom (slotDuePointMs v index.html)")
web = os.path.join(_ROOT, "..", "nas100-monitor-web", "index.html")
if not os.path.exists(web):
    print("  (index.html dashboardu nie je vedla repozitara - preskakujem)")
else:
    src = open(web, encoding="utf-8").read()
    m = re.search(r"function slotDuePointMs\(.*?\n}\n", src, re.S)
    ep = re.search(r"const SLOT_EPOCH_MS\s*=\s*([^;]+);", src)
    check("funkcia aj SLOT_EPOCH_MS najdene", bool(m and ep), True)
    cases = []
    t = datetime(2026, 9, 14, 0, 1, tzinfo=timezone.utc)
    while t < datetime(2026, 9, 16, tzinfo=timezone.utc):
        for iv in (2, 6, 9, 12):
            for s in (1, 7, 13, 20):
                cases.append({"ms": int(t.timestamp() * 1000), "iv": iv, "s": s,
                              "py": int(tc._slot_due_point(t, iv, s, 0).timestamp() * 1000)})
        t += timedelta(minutes=47)
    # Pripady idu cez subor - vo Windows je prikazovy riadok obmedzeny (WinError 206).
    tmp = os.path.join(os.environ["TEMP"], "slotgrid_cases.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cases, f)
    js = (f"const SLOT_EPOCH_MS = {ep.group(1)};\n" + m.group(0) +
          "const cs = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8')); let bad = 0;"
          "for (const c of cs) { const g = slotDuePointMs(c.ms, c.iv, c.s, 20, 0);"
          " if (Math.abs(g - c.py) > 1) { bad++; if (bad < 4) console.log('NESEDI', JSON.stringify(c), g); } }"
          "console.log(bad);")
    out = subprocess.run(["node", "-e", js, tmp], capture_output=True, text=True)
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr.strip()[:200]
    check(f"{len(cases)} kombinacii, nezhod", last, "0")

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
