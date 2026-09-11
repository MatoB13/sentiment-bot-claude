"""Titulky Benzinga cez Alpaca do skenu (2026-09-11, na ziadost pouzivatela).
Siet je nahradena - test nikdy nevola Alpaca ani Clauda."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/alpacanews.db"
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
import alpaca_news_client as an  # noqa: E402
import assets  # noqa: E402
import claude_analyst  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<60} {got!r} (ocakavane {want!r})")


now = datetime.now(timezone.utc)
iso = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


class R:
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._b


calls = []
state = {"resp": None}


def fake_get(url, **kw):
    calls.append((url, kw))
    if isinstance(state["resp"], Exception):
        raise state["resp"]
    return state["resp"]


an.requests.get = fake_get

print("1) Bez klucov je zdroj vypnuty a nic nevola")
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY = "", ""
check("enabled() bez klucov", an.enabled(), False)
check("get_headlines vrati []", an.get_headlines(["QQQ"]), [])
check("ziadne volanie siete", len(calls), 0)

print("\n2) S klucmi: filter veku, dedup, poradie, strop poctu")
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY = "PKtest", "secret"
config.ALPACA_NEWS_MAX_AGE_HOURS, config.ALPACA_NEWS_MAX_ITEMS = 12.0, 3
state["resp"] = R({"news": [
    {"headline": "Nvidia beats estimates", "created_at": iso(0.5), "symbols": ["NVDA"], "source": "benzinga"},
    {"headline": "Old story", "created_at": iso(20), "symbols": ["NVDA"]},
    {"headline": "nvidia beats estimates", "created_at": iso(0.6), "symbols": ["NVDA", "AMD"]},
    {"headline": "Second", "created_at": iso(2), "symbols": ["NVDA"]},
    {"headline": "Third", "created_at": iso(3), "symbols": ["NVDA"]},
    {"headline": "Fourth", "created_at": iso(4), "symbols": ["NVDA"]},
    {"headline": "", "created_at": iso(1)},
]})
h = an.get_headlines(["NVDA"])
check("starsie ako 12 h vyradene, dedup, strop 3", [x["title"] for x in h],
      ["Nvidia beats estimates", "Second", "Third"])
check("vek v hodinach", round(h[0]["age_hours"], 1), 0.5)
url, kw = calls[-1]
check("REST news endpoint", url, "https://data.alpaca.markets/v1beta1/news")
check("kluce v hlavickach, nie v URL", "PKtest" in str(kw.get("params")), False)
check("  hlavicka key", kw["headers"]["APCA-API-KEY-ID"], "PKtest")
st = an.last_status(["NVDA"])
check("stav: ok a pocet", (st["ok"], st["count"]), (True, 3))
n_before = len(calls)
an.get_headlines(["NVDA"])
check("druhe volanie z cache (ziadna siet)", len(calls), n_before)

print("\n3) Vypadok nezhodi nic a je zapisany")
an._cache.clear()
state["resp"] = R({}, code=403)
check("pri 403 vrati []", an.get_headlines(["TSLA"]), [])
st = an.last_status(["TSLA"])
check("stav ok=False s chybou", (st["ok"], "403" in (st["error"] or "")), (False, True))
an._cache.clear()
state["resp"] = ConnectionError("dns")
check("pri sietovej chybe vrati []", an.get_headlines(["TSLA"]), [])

print("\n4) Mapovanie tickerov")
m = {a["name"]: a.get("alpaca_news_symbols") for a in assets.ALL_ASSETS}
check("NAS100 -> QQQ", m["NAS100"], ["QQQ"])
check("zlato -> GLD", m["GOLD"], ["GLD"])
check("ropa -> USO", m["WTI"], ["USO"])
check("BTC -> BTCUSD", m["BTC"], ["BTCUSD"])
check("ADA nema (Benzinga 0 sprav za 7 dni)", m["ADA"], None)

print("\n5) Prompt skenu")
A = next(a for a in assets.ALL_ASSETS if a["name"] == "NVDA")
news = [{"title": "Nvidia beats {estimates}", "age_hours": 0.25}, {"title": "Second", "age_hours": 2.0}]
p = claude_analyst._build_triage_prompt(A, {"last_price": 1}, {}, {}, None, None, None, None,
                                        None, None, None, None, news)
check("blok Benzinga je v prompte", "Cerstve titulky Benzinga" in p, True)
check("vek v minutach pod 1 h", "[pred 15 min] Nvidia beats {estimates}" in p, True)
check("vek v hodinach nad 1 h", "[pred 2.0 h] Second" in p, True)
check("upozornenie na rutinne titulky", "NIE SU dovod na ANO" in p, True)
p2 = claude_analyst._build_triage_prompt(A, {"last_price": 1}, {}, {}, None, None, None, None,
                                         None, None, None, None, None)
check("bez titulkov ziadny blok", "Cerstve titulky Benzinga" in p2, False)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
