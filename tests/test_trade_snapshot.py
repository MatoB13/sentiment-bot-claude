"""Snimka ceny v momente zatvorenia (trade_snapshot.py, 2026-09-15). Bez siete a burzy."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta

DB = os.environ["TEMP"].replace("\\", "/") + "/tradesnap.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import trade_snapshot as ts  # noqa: E402
from db import PriceBar, PriceMinute, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:30]!r} (ocakavane {want!r})")


NOW = datetime(2026, 9, 15, 12, 0)
CLOSE = datetime(2026, 9, 15, 10, 37)
SYM = "ZHIPU-USD"
s = get_session()
for m in range(-200, 60):                     # minuty okolo zatvorenia, aj PO nom
    s.add(PriceMinute(symbol=SYM, ts=CLOSE + timedelta(minutes=m), price=90.0 - m * 0.01))
for h in range(-30, 3):                       # hodinove sviecky vratane hodiny zatvorenia a po nej
    hs = CLOSE.replace(minute=0) + timedelta(hours=h)
    s.add(PriceBar(symbol=SYM, hour_start=hs, open=90, high=91, low=89, close=90.0 + h * 0.1))


def trade(tid, closed_at, **kw):
    t = Trade(id=tid, symbol=SYM, direction="Short", status="closed_by_exchange", entry_price=93.0,
              stop_loss_price=96.5, take_profit_price=88.7, size=6.0, notional_usd=560, margin_usd=56,
              leverage=10, opened_at=CLOSE - timedelta(hours=30), closed_at=closed_at, **kw)
    s.add(t)
    return t


t1 = trade(1, CLOSE, close_fill_price=89.63, close_reason="tp_runner_stop")
s.commit()

print("1) Obsah snimky")
snap = ts.build_snapshot(s, t1)
mins = snap["minutes"]
check("minuty: 65 min pred zatvorenim az po zatvorenie", (len(mins), mins[0][0], mins[-1][0]),
      (66, (CLOSE - timedelta(minutes=65)).isoformat(), CLOSE.isoformat()))
check("  ziadna minuta PO zatvoreni", all(m[0] <= CLOSE.isoformat() for m in mins), True)
bars = snap["bars"]
check("hodinove: 25 celych hodin pred hodinou zatvorenia", len(bars), 25)
check("  hodina zatvorenia (dalej sa menila) NIE JE v snimke", bars[-1][0], (CLOSE.replace(minute=0) - timedelta(hours=1)).isoformat())
check("posledny bod = presna cena zatvorenia z burzy", snap["close"], [CLOSE.isoformat(), 89.63])

t2 = trade(2, CLOSE)                          # bez close_fill_price
s.commit()
check("bez presnej ceny: posledna minutova cena", ts.build_snapshot(s, t2)["close"], [CLOSE.isoformat(), 90.0])

print("\n2) Hodinove doplnanie (snapshot_recent_closes)")
t3 = trade(3, NOW - timedelta(days=4))                       # starsi nez 3 dni - minuty uz nie su
t4 = trade(4, NOW - timedelta(minutes=1))                    # prave zatvoreny - pocka na dalsi beh
t5 = Trade(id=5, symbol=SYM, direction="Long", status="open", entry_price=1, stop_loss_price=0.9,
           take_profit_price=1.1, size=1, notional_usd=1, margin_usd=1, leverage=1, opened_at=NOW)
s.add(t5)
s.commit()
n = ts.snapshot_recent_closes(s, NOW)
s.expire_all()
got = {t.id: s.get(Trade, t.id).close_snapshot is not None for t in (t1, t2, t3, t4, t5)}
check("doplnene len zatvorene za 3 dni a starsie nez 2 min", got, {1: True, 2: True, 3: False, 4: False, 5: False})
check("pocet", n, 2)
check("druhy beh nic nerobi (idempotentne)", ts.snapshot_recent_closes(s, NOW), 0)

print("\n3) Chyba snimky nesmie zhodit price_poller")
real = ts.build_snapshot
ts.build_snapshot = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
t6 = trade(6, NOW - timedelta(hours=1))
s.commit()
check("vynimka -> 0, bez vynimky von", ts.snapshot_recent_closes(s, NOW), 0)
ts.build_snapshot = real

print("\n4) Poradie v price_poller: najprv snimka, potom mazanie minut")
src = open(os.path.join(_ROOT, "price_poller.py"), encoding="utf-8").read()
i_snap, i_del = src.find("snapshot_recent_closes"), src.find("PriceMinute.ts < cutoff")
check("snimka pred mazanim", 0 < i_snap < i_del, True)
s.close()

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
