"""Zber Binance long/short pomeru do vlastnych hodinovych barov.

POVOD (poziadavka pouzivatela 2026-09-06): vykreslit pomer ako ciaru v cenovom
grafe. `cycle_logs.ta` uz jednu hodnotu ma, ale zapise sa LEN pri cykle -
namerane 44-89 bodov za 10 dni proti 240 hodinovym svieckam, cize ciara by bola
deravá. Binance v jednom volani vracia az 500 hodinovych bodov (~21 dni).

DRUHA poziadavka bola vyslovna: zlyhanie musi byt tiche voci botu, ale NIE voci
pouzivatelovi - preto sa kazdy pokus (aj neuspesny) zapisuje do
LongShortPollStatus a dashboard nad grafom hlasi vypadok cervenym pismom.

Test nechodi na siet - Binance volanie je stubnute.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/lspoll.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import binance_client  # noqa: E402
import long_short_poller as lsp  # noqa: E402
from db import LongShortBar, LongShortPollStatus, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<56} {got!r:>9} (ocakavane {want!r})")


HOUR_MS = 3600_000
BASE = 1788670800000  # 6.9.2026 05:00 UTC


def series(n, start=BASE, ratio=2.0):
    return [{"timestamp": start + i * HOUR_MS, "long_pct": 66.7,
             "short_pct": 33.3, "long_short_ratio": ratio} for i in range(n)]


calls = []


def stub(fn):
    def _get(symbol, period="1h", limit=500):
        calls.append({"symbol": symbol, "period": period, "limit": limit})
        return fn(symbol, period, limit)
    binance_client.get_long_short_history = _get


s = get_session()
SYMS = sorted(a["strike_symbol"] for a in assets.ALL_ASSETS if a.get("binance_volume_symbol"))

print("1) Prvy beh: plny backfill pre kazdy ticker s Binance futures")
stub(lambda sym, period, limit: series(limit))
lsp.poll_all()
check("tickerov s Binance futures", len(SYMS), 6)
check("volanie na kazdy z nich", len(calls), len(SYMS))
check("prvy beh ziada plny limit", {c["limit"] for c in calls}, {lsp._BACKFILL_LIMIT})
check("a hodinovu granularitu", {c["period"] for c in calls}, {"1h"})
check("barov spolu", s.query(LongShortBar).count(), len(SYMS) * lsp._BACKFILL_LIMIT)
check("vsetky pokusy ok", s.query(LongShortPollStatus).filter(
    LongShortPollStatus.ok.is_(True)).count(), len(SYMS))

print("\n2) Symbol je NAS strike_symbol, nie Binance nazov (kvoli joinu s price_bars)")
stored = sorted({b.symbol for b in s.query(LongShortBar)})
check("ulozene symboly", stored, SYMS)
check("ziadny 'USDT' tvar", any("USDT" in x for x in stored), False)

print("\n3) Casy su zarovnane na cele hodiny")
b = s.query(LongShortBar).first()
check("minuty", b.hour_start.minute, 0)
check("sekundy", b.hour_start.second, 0)
check("naive UTC (ako PriceBar.hour_start)", b.hour_start.tzinfo, None)

print("\n4) Dalsi beh: uz len prirastok, ziadne duplicity")
calls.clear()
before = s.query(LongShortBar).count()
stub(lambda sym, period, limit: series(limit, start=BASE + 400 * HOUR_MS, ratio=1.5))
lsp.poll_all()
check("uz NEziada plny backfill", {c["limit"] for c in calls}, {lsp._INCREMENTAL_LIMIT})
# 6 novych bodov od BASE+400h; prvy backfill sial po BASE+499h, takze
# prekryvaju sa vsetky - nesmie pribudnut ani jeden riadok navyse.
check("ziadne duplicity", s.query(LongShortBar).count(), before)
s.expire_all()
overlapped = s.query(LongShortBar).filter(
    LongShortBar.hour_start == datetime.fromtimestamp(
        (BASE + 400 * HOUR_MS) / 1000, timezone.utc).replace(tzinfo=None)).first()
check("prekryv PREPISAL hodnotu", overlapped.ratio, 1.5)

print("\n5) ZLYHANIE: tiche voci botu, VIDITELNE v statuse")


def boom(sym, period, limit):
    raise RuntimeError("403 Client Error: Forbidden")


stub(boom)
crashed = False
try:
    lsp.poll_all()          # nesmie vyhodit von NIC
except Exception as e:
    crashed = True
    print(f"       vynimka unikla: {e}")
check("poll_all vynimku neprepusti", crashed, False)
s.expire_all()
check("vsetky pokusy oznacene ako zlyhane",
      s.query(LongShortPollStatus).filter(LongShortPollStatus.ok.is_(False)).count(), len(SYMS))
st = s.query(LongShortPollStatus).filter(LongShortPollStatus.ok.is_(False)).first()
check("status nesie dovod", "403" in (st.error or ""), True)
check("uz stiahnute bary zlyhanie NEZMAZE", s.query(LongShortBar).count(), before)

print("\n6) Zlyhanie JEDNEHO tickera nezhodi ostatne")


def one_bad(sym, period, limit):
    if sym == "ADAUSDT":
        raise RuntimeError("timeout")
    return series(limit, start=BASE + 500 * HOUR_MS)


stub(one_bad)
lsp.poll_all()
s.expire_all()
bad_rows = s.query(LongShortPollStatus).filter(LongShortPollStatus.ok.is_(False)).all()
check("zlyhal prave jeden", len(bad_rows), 1)
check("a je to ADA", bad_rows[0].symbol, "ADA-USD")
check("ostatne su znova ok",
      s.query(LongShortPollStatus).filter(LongShortPollStatus.ok.is_(True)).count(), len(SYMS) - 1)

print("\n7) Prazdna odpoved nie je chyba, len nic nezapise")
stub(lambda sym, period, limit: [])
n_before = s.query(LongShortBar).count()
lsp.poll_all()
s.expire_all()
check("ziadne nove bary", s.query(LongShortBar).count(), n_before)
check("a status je ok (nie je to zlyhanie)",
      s.query(LongShortPollStatus).filter(LongShortPollStatus.ok.is_(True)).count(), len(SYMS))

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
