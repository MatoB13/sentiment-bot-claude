"""Tyzdenna kontrola, ci su slugy CoinMarketCal v bezplatnom plane (2026-09-11,
na ziadost pouzivatela). Neexistujuci/nepokryty slug vracia HTTP 200 a 0
udalosti, takze bez tejto kontroly by vypadok bol tichy.
Siet je nahradena - test nikdy nevola CoinMarketCal."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

_DB = os.environ["TEMP"].replace("\\", "/") + "/cmcslug.db"
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
import assets  # noqa: E402
import coinmarketcal_client as cmc  # noqa: E402
from db import CoinMarketCalSlugStatus, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<60} {got!r} (ocakavane {want!r})")


class R:
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._b


calls = []
state = {"fail": False}
# Strana 1 bez Midnightu, strana 2 s nim - overuje strankovanie cez cursor.
PAGES = [
    {"data": [{"slug": "bitcoin", "rank": 1}, {"slug": "zcash", "rank": 7},
              {"slug": "hyperliquid", "rank": 8}, {"slug": "cardano", "rank": 15},
              {"slug": "near", "rank": 22}, {"slug": "pump-fun", "rank": 39}],
     "meta": {"cursor": "p2"}},
    {"data": [{"slug": "midnight-3", "rank": 91}], "meta": {"cursor": None}},
]


def fake_get(url, **kw):
    calls.append((url, kw))
    if state["fail"]:
        return R({}, code=403)
    return R(PAGES[1] if (kw.get("params") or {}).get("cursor") == "p2" else PAGES[0])


cmc.requests.get = fake_get
config.COINMARKETCAL_API_KEY = "test"
s = get_session()


def status():
    return {r.symbol: r for r in s.query(CoinMarketCalSlugStatus).all()}


configured = {a["strike_symbol"]: a["coinmarketcal_slug"]
              for a in assets.ALL_ASSETS if a.get("coinmarketcal_slug")}

print("1) Prva kontrola: vsetky slugy overene, strankovanie")
cmc.check_slugs(s)
check("2 strany (cursor)", len(calls), 2)
st = status()
check("riadok pre kazdy ticker so slugom", sorted(st), sorted(configured))
check("vsetky pokryte", all(r.covered for r in st.values()), True)
night = next(r for r in st.values() if r.slug == "midnight-3")
check("Midnight z 2. strany, rank 91", (night.covered, night.rank), (True, 91))

print("\n2) Do tyzdna uz neklope (ani po redeployi)")
n = len(calls)
cmc.check_slugs(s)
check("ziadne volanie", len(calls), n)

print("\n3) Coin vypadne z planu -> covered=False")
for r in st.values():
    r.checked_at = datetime.now(timezone.utc) - timedelta(days=8)
s.commit()
PAGES[1]["data"] = []
cmc.check_slugs(s)
night = next(r for r in status().values() if r.slug == "midnight-3")
check("po 8 dnoch znova overene", len(calls), n + 2)
check("Midnight covered=False, bez ranku", (night.covered, night.rank), (False, None))

print("\n4) Zlyhanie API sa zapise a skusi sa pri dalsom polle")
for r in status().values():
    r.checked_at = datetime.now(timezone.utc) - timedelta(days=8)
s.commit()
state["fail"] = True
cmc.check_slugs(s)
st = status()
check("chyba zapisana", all("403" in (r.error or "") for r in st.values()), True)
check("posledny znamy stav ostal", next(r for r in st.values() if r.slug == "midnight-3").covered, False)
state["fail"] = False
PAGES[1]["data"] = [{"slug": "midnight-3", "rank": 91}]
n = len(calls)
cmc.check_slugs(s)
check("po chybe sa hned skusa znova", len(calls), n + 2)
check("chyba zmazana", any(r.error for r in status().values()), False)

print("\n5) Ticker, ktory slug stratil, z tabulky zmizne")
s.add(CoinMarketCalSlugStatus(symbol="STARY-USD", slug="stary", covered=True,
                              checked_at=datetime.now(timezone.utc)))
s.commit()
cmc.check_slugs(s)
check("STARY-USD zmazany", "STARY-USD" in status(), False)

print("\n6) Nove slugy BTC a NEAR")
by = {a["name"]: a for a in assets.ALL_ASSETS}
check("BTC -> bitcoin", by["BTC"].get("coinmarketcal_slug"), "bitcoin")
check("NEAR -> near (nie near-protocol)", by["NEAR"].get("coinmarketcal_slug"), "near")

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
