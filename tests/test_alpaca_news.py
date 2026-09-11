"""Titulky Benzinga cez Alpaca do skenu (2026-09-11, na ziadost pouzivatela).
Druha verzia: zdielany zasobnik celeho feedu, ticker si vyberie svoje titulky
podla symbolu ALEBO nazvu - aj ticker, o ktorom dnes nikto nepise.
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
_next_id = [0]


def n(title, hours, symbols=()):
    _next_id[0] += 1
    return {"id": _next_id[0], "headline": title, "created_at": iso(hours), "symbols": list(symbols)}


class R:
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._b


calls = []
# pages: zoznam stran; kazda strana je zoznam sprav. Posledna strana bez tokenu.
state = {"pages": [], "error": None}


def fake_get(url, **kw):
    calls.append((url, kw))
    if state["error"] is not None:
        if isinstance(state["error"], Exception):
            raise state["error"]
        return R({}, code=state["error"])
    token = kw["params"].get("page_token")
    i = int(token) if token else 0
    body = {"news": state["pages"][i] if i < len(state["pages"]) else []}
    if i + 1 < len(state["pages"]):
        body["next_page_token"] = str(i + 1)
    return R(body)


def reset():
    an._pool.clear()
    an._pool_fetched_at = 0.0
    an._last_status.clear()


an.requests.get = fake_get
A = {a["name"]: a for a in assets.ALL_ASSETS}

print("1) Bez klucov je zdroj vypnuty a nic nevola")
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY = "", ""
check("enabled() bez klucov", an.enabled(), False)
check("get_headlines_for_asset vrati []", an.get_headlines_for_asset(A["NVDA"]), [])
check("ziadne volanie siete", len(calls), 0)

print("\n2) Zasobnik: strankovanie, vek, vyber podla symbolu aj nazvu")
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY = "PKtest", "secret"
config.ALPACA_NEWS_MAX_AGE_HOURS, config.ALPACA_NEWS_MAX_ITEMS = 12.0, 3
config.ALPACA_NEWS_CACHE_MINUTES = 5.0
reset()
state["pages"] = [
    [n("Nvidia beats estimates", 0.5, ["NVDA"]),
     n("Why Is SK Hynix Stock Surging on Wednesday?", 1.0, ["AAPL", "EWY"]),
     n("nvidia beats estimates", 0.6, ["NVDA", "AMD"])],
    [n("Second", 2, ["NVDA"]), n("Third", 3, ["NVDA"]), n("Fourth", 4, ["NVDA"]),
     n("Stocks rally past midnight deadline", 1.5, ["SPY"]),
     n("Midnight Network mainnet date set by Cardano founder", 2.5, []),
     n("Apptronik Soars on Humanoid Robot Hype", 1.2, ["APPT"])],
    [n("Old story", 20, ["NVDA"]), n("", 1, ["NVDA"])],
]
h = an.get_headlines_for_asset(A["NVDA"])
check("3 strany stiahnute (strankovanie)", len(calls), 3)
check("starsie ako 12 h vyradene, dedup, strop 3", [x["title"] for x in h],
      ["Nvidia beats estimates", "Second", "Third"])
check("vek v hodinach", round(h[0]["age_hours"], 1), 0.5)
check("priradene podla symbolu", h[0]["by"], "symbol")
url, kw = calls[-1]
check("REST news endpoint", url, "https://data.alpaca.markets/v1beta1/news")
check("stahuje CELY feed (bez filtra symbolov)", "symbols" in kw["params"], False)
check("kluce v hlavickach, nie v URL", "PKtest" in str(kw.get("params")), False)
check("  hlavicka key", kw["headers"]["APCA-API-KEY-ID"], "PKtest")

sk = an.get_headlines_for_asset(A["SKHYNIX"])
check("SK Hynix bez symbolu najdeny podla nazvu", [(x["title"][:18], x["by"]) for x in sk],
      [("Why Is SK Hynix St", "nazov")])
check("dalsi ticker v tom istom tiku = ziadna siet", len(calls), 3)
night = [x["title"] for x in an.get_headlines_for_asset(A["NIGHT"])]
check("NIGHT: Midnight Network ANO, hole 'midnight' NIE", night,
      ["Midnight Network mainnet date set by Cardano founder"])
check("ADA chyti 'Cardano' v tom istom titulku", len(an.get_headlines_for_asset(A["ADA"])), 1)
check("HYPE: hole slovo 'hype' sa nechyta", an.get_headlines_for_asset(A["HYPE"]), [])
check("ticker bez sprav: prazdne, ale stav zapisany",
      (an.get_headlines_for_asset(A["UNITREE"]), an.last_status("UNITREE")["count"]), ([], 0))
st = an.last_status("NVDA")
check("stav: ok, pocet, velkost zasobnika", (st["ok"], st["count"], st["pool_size"]), (True, 3, 9))

print("\n3) Po vyprsani cache sa dopina len nove (prekryv 30 min)")
an._pool_fetched_at -= 6 * 60
state["pages"] = [[n("Nvidia fresh news", 0.1, ["NVDA"])]]
before = len(calls)
h = an.get_headlines_for_asset(A["NVDA"])
check("jedno volanie", len(calls) - before, 1)
start = datetime.fromisoformat(calls[-1][1]["params"]["start"].replace("Z", "+00:00"))
check("start = najnovsi v zasobniku - 30 min", round((now - start).total_seconds() / 3600, 2), 1.0)
check("novy titulok prvy, stare zostali", [x["title"] for x in h][:2], ["Nvidia fresh news", "Nvidia beats estimates"])

print("\n4) Vypadok nezhodi nic, stary zasobnik ostava, stav zapisany")
an._pool_fetched_at -= 6 * 60
state["error"] = 403
h = an.get_headlines_for_asset(A["NVDA"])
check("pri 403 vrati co ma (stary zasobnik)", len(h), 3)
st = an.last_status("NVDA")
check("stav ok=False s chybou", (st["ok"], "403" in (st["error"] or "")), (False, True))
before = len(calls)
an.get_headlines_for_asset(A["TSLA"])
check("po chybe dalsi ticker neklope znova (cache)", len(calls), before)
reset()
state["error"] = ConnectionError("dns")
check("pri sietovej chybe a prazdnom zasobniku vrati []", an.get_headlines_for_asset(A["NVDA"]), [])
state["error"] = None

print("\n5) Mapovanie tickerov")
bez = [a["name"] for a in assets.ALL_ASSETS if not an.covers(a)]
check("KAZDY ticker ma symbol alebo nazov (aj buduce)", bez, [])
check("NAS100 -> QQQ", A["NAS100"]["alpaca_news_symbols"], ["QQQ"])
check("zlato -> GLD", A["GOLD"]["alpaca_news_symbols"], ["GLD"])
check("ropa -> USO", A["WTI"]["alpaca_news_symbols"], ["USO"])
check("BTC -> BTCUSD", A["BTC"]["alpaca_news_symbols"], ["BTCUSD"])
check("ADA -> ADAUSD", A["ADA"]["alpaca_news_symbols"], ["ADAUSD"])

print("\n6) Prompt skenu")
news = [{"title": "Nvidia beats {estimates}", "age_hours": 0.25, "by": "symbol"},
        {"title": "Second", "age_hours": 2.0, "by": "nazov"}]
p = claude_analyst._build_triage_prompt(A["NVDA"], {"last_price": 1}, {}, {}, None, None, None, None,
                                        None, None, None, None, news)
check("blok Benzinga je v prompte", "Cerstve titulky Benzinga" in p, True)
check("vek v minutach pod 1 h", "[pred 15 min] Nvidia beats {estimates}" in p, True)
check("vek v hodinach nad 1 h", "[pred 2.0 h] Second" in p, True)
check("upozornenie na rutinne titulky", "NIE SU dovod na ANO" in p, True)
p2 = claude_analyst._build_triage_prompt(A["NVDA"], {"last_price": 1}, {}, {}, None, None, None, None,
                                         None, None, None, None, None)
check("bez titulkov ziadny blok", "Cerstve titulky Benzinga" in p2, False)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
