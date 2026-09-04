"""Testy opravy zhluku na prechode obchodnych hodin (_interval_regime_start)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/rb.db"
sys.path.insert(0, _ROOT)

import trade_cycle  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r:>10} (ocakavane {want!r})")


# Ticker s obchodnymi hodinami 10-21 UTC, off-hours dlhsi interval
A = {"trade_interval_hours": 2.0, "off_hours_interval_hours": 4.0,
     "weekend_interval_hours": 6.0,
     "trading_hours_start_utc": 10, "trading_hours_end_utc": 21,
     "run_slot": 3, "run_slot_hour_offset": 0}
# 24/7 krypto - interval sa nikdy nemeni
C = {"trade_interval_hours": 2.0, "off_hours_interval_hours": 2.0,
     "weekend_interval_hours": 2.0,
     "trading_hours_start_utc": 0, "trading_hours_end_utc": 24,
     "run_slot": 3, "run_slot_hour_offset": 0}

WED = datetime(2026, 9, 9, tzinfo=timezone.utc)   # streda

print("=" * 96)
print("1) _interval_regime_start NAJDE HRANICU")
print("=" * 96)
t = WED.replace(hour=10, minute=1)
check("o 10:01 zacal rezim o 10:00", trade_cycle._interval_regime_start(A, t),
      WED.replace(hour=10))
t = WED.replace(hour=20, minute=59)
check("o 20:59 stale ten isty rezim od 10:00",
      trade_cycle._interval_regime_start(A, t), WED.replace(hour=10))
t = WED.replace(hour=21, minute=1)
check("o 21:01 novy rezim od 21:00", trade_cycle._interval_regime_start(A, t),
      WED.replace(hour=21))
t = WED.replace(hour=3)
check("o 03:00 rezim bezi od 21:00 predosleho dna",
      trade_cycle._interval_regime_start(A, t),
      (WED - timedelta(days=1)).replace(hour=21))

print()
print("=" * 96)
print("2) 24/7 ASSET - ziadna zmena intervalu, obmedzenie sa neuplatni")
print("=" * 96)
t = WED.replace(hour=10, minute=1)
rs = trade_cycle._interval_regime_start(C, t)
check("regime start je 72h dozadu (teda bezzuby)", (t - rs) >= timedelta(hours=71), True)

print()
print("=" * 96)
print("3) JADRO CHYBY - 'zmeskany' bod spred zmeny intervalu sa neuzna")
print("=" * 96)
# ADA-like: slot 3, offset (3-1)*65 = 130
#   off-hours 4h: 130 mod 240 = 130 -> 02:10, 06:10, 10:10
#   trading   2h: 130 mod 120 =  10 -> 00:10, 02:10 ... 08:10, 10:10
now = WED.replace(hour=10, minute=1)
due = trade_cycle._slot_due_point(now, 2.0, 3, 0)
print(f"  o {now:%H:%M} pri 2h mriezke je posledny bod {due:%H:%M}")
check("je to 08:10 (bod, ktory pocas off-hours neexistoval)", due.strftime("%H:%M"), "08:10")
rs = trade_cycle._interval_regime_start(A, now)
check("rezim zacal az 10:00, takze 08:10 sa NEUZNA", due < rs, True)

later = WED.replace(hour=10, minute=11)
due2 = trade_cycle._slot_due_point(later, 2.0, 3, 0)
check("o 10:11 uz je bod 10:10", due2.strftime("%H:%M"), "10:10")
check("a ten uz v rezime lezi", due2 >= trade_cycle._interval_regime_start(A, later), True)

print()
print("=" * 96)
print("4) BEZNA PREVADZKA VNUTRI REZIMU SA NEMENI")
print("=" * 96)
for hh, mm in ((12, 11), (14, 11), (16, 11), (18, 11)):
    t = WED.replace(hour=hh, minute=mm)
    d = trade_cycle._slot_due_point(t, 2.0, 3, 0)
    inside = d >= trade_cycle._interval_regime_start(A, t)
    print(f"    {t:%H:%M} -> bod {d:%H:%M}, v rezime: {inside}")
    if not inside:
        ok = False
check("vsetky bezne body vnutri obchodnych hodin platia", True, True)

print()
print("=" * 96)
print("5) PYTHON <-> JS PARITA (regime start)")
print("=" * 96)
import json  # noqa: E402
import subprocess  # noqa: E402

cases = []
for h in range(0, 24):
    for m in (1, 31):
        t = WED.replace(hour=h, minute=m)
        cases.append({
            "iso": t.isoformat(),
            "py": trade_cycle._interval_regime_start(A, t).isoformat(),
        })
js = r"""
const cases = JSON.parse(process.argv[1]);
const cfg = {trade_interval_hours:2, off_hours_interval_hours:4, weekend_interval_hours:6,
             trading_hours_start_utc:10, trading_hours_end_utc:21};
function requiredIntervalHours(cfg, d) {
  const day = d.getUTCDay();
  if (day === 0 || day === 6) return cfg.weekend_interval_hours;
  const h = d.getUTCHours();
  const s = cfg.trading_hours_start_utc, e = cfg.trading_hours_end_utc;
  if (s != null && e != null && h >= s && h < e) return cfg.trade_interval_hours;
  return cfg.off_hours_interval_hours;
}
function intervalRegimeStartMs(cfg, ms) {
  const cur = requiredIntervalHours(cfg, new Date(ms));
  let probe = new Date(ms); probe.setUTCMinutes(0,0,0);
  for (let i=0;i<72;i++){
    const prev = probe.getTime() - 3600000;
    if (requiredIntervalHours(cfg, new Date(prev)) !== cur) return probe.getTime();
    probe = new Date(prev);
  }
  return probe.getTime();
}
let bad = 0;
for (const c of cases) {
  const got = new Date(intervalRegimeStartMs(cfg, Date.parse(c.iso))).toISOString();
  const want = new Date(Date.parse(c.py)).toISOString();
  if (got !== want) { bad++; console.log('  NESEDI', c.iso, got, want); }
}
console.log(bad);
"""
out = subprocess.run(["node", "-e", js, json.dumps(cases)],
                     capture_output=True, text=True)
mismatches = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "?"
check(f"{len(cases)} kombinacii, nezhod", mismatches, "0")

print()
print("=" * 96)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 96)
sys.exit(0 if ok else 1)
