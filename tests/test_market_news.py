"""Zdielane trhove titulky pre lacny sken (market_news_client.py).

POVOD: sken NEMA web_search, takze jeho jediny zdroj sprav su Marketaux titulky.
Namerane za 7 dni: ADA 0 %, NEAR 0 %, PUMP 0 %, NIGHT 0 %, ZEC 4 % - pre tie
tickery teda sken rozhodoval vylucne z TA. Iny dodavatel to nerieši (overene na
dvoch nezavislych RSS: najnovsi clanok ADA 70 h, ZEC 205 h, NEAR 2137 h), lebo
tie spravy v danom case NEEXISTUJU. Trhove feedy su naopak cerstve.

Test nechodi na siet - stahovanie je stubnute, aby bol rychly a stabilny.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/mnews.db"
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import market_news_client as mn  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>8} (ocakavane {want!r})")


def feed(items):
    """items: [(titulok, vek_v_hodinach)] -> RSS bajty."""
    now = datetime.now(timezone.utc)
    body = "".join(
        f"<item><title>{t}</title>"
        f"<pubDate>{format_datetime(now - timedelta(hours=h))}</pubDate></item>"
        for t, h in items)
    return f"<rss><channel>{body}</channel></rss>".encode()


class Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def stub(responses):
    """responses: zoznam Resp/Exception, jeden na kazdy feed v poradi."""
    seq = list(responses)

    def _get(url, **kw):
        r = seq.pop(0) if seq else Resp(feed([]))
        if isinstance(r, Exception):
            raise r
        return r
    mn.requests.get = _get


def reset():
    mn._cache = None


print("1) Cerstve titulky prejdu, stare sa zahodia")
reset()
stub([Resp(feed([("Cerstvy", 2), ("Uz stary", 40), ("Hranicny", 24)])), Resp(feed([]))])
got = mn.get_market_headlines()
check("poctu titulkov", len(got), 2)
check("najnovsi je prvy", got[0]["title"], "Cerstvy")
check("stary (40 h) vypadol", any(g["title"] == "Uz stary" for g in got), False)

print("\n2) Duplicity napriec feedmi sa zlucia")
reset()
stub([Resp(feed([("Ta ista sprava", 1)])), Resp(feed([("Ta ista sprava", 3), ("Ina", 2)]))])
got = mn.get_market_headlines()
check("pocet po dedupe", len(got), 2)
check("ostal novsi vyskyt", got[0]["age_hours"], 1.0)

print("\n3) Strop na pocet titulkov")
reset()
stub([Resp(feed([(f"T{i}", i) for i in range(1, 20)])), Resp(feed([]))])
check("orezane na MARKET_NEWS_MAX_ITEMS",
      len(mn.get_market_headlines()), config.MARKET_NEWS_MAX_ITEMS)

print("\n4) FAIL-OPEN: zlyhanie zdroja nesmie zhodit cyklus")
reset()
stub([RuntimeError("timeout"), RuntimeError("HTTP 500")])
check("oba feedy zlyhali -> prazdny zoznam", mn.get_market_headlines(), [])
reset()
stub([RuntimeError("timeout"), Resp(feed([("Druhy feed drzi sluzbu", 1)]))])
got = mn.get_market_headlines()
check("jeden feed zlyhal, druhy dodal", len(got), 1)
reset()
stub([Resp(b"toto nie je XML"), Resp(feed([("Platny", 1)]))])
check("rozbite XML neprerusi spracovanie", len(mn.get_market_headlines()), 1)

print("\n5) Cache - feed je ZDIELANY, nestahuje sa raz za ticker")
reset()
calls = [0]


def counting_get(url, **kw):
    calls[0] += 1
    return Resp(feed([("Sprava", 1)]))


mn.requests.get = counting_get
mn.get_market_headlines()
first = calls[0]
for _ in range(5):
    mn.get_market_headlines()
# Pocet feedov sa ODVODZUJE, nie natvrdo - inak test spadne pri kazdom
# pridani zdroja, hoci vlastnost (jedno stiahnutie zdielane vsetkymi)
# stale plati. Prave to sa stalo pri rozsireni na 4 feedy.
check("prve volanie stiahlo VSETKY feedy", first, len(mn._FEEDS))
check("dalsich 5 volani islo z cache", calls[0], first)

print("\n6) Globalny vypinac")
reset()
mn.requests.get = counting_get
before = calls[0]
config.MARKET_NEWS_ENABLED = False
check("vypnute -> prazdno", mn.get_market_headlines(), [])
check("a ziadne stahovanie", calls[0], before)
config.MARKET_NEWS_ENABLED = True

print("\n6b) Stav posledneho stiahnutia - aby sa vypadok dal ukazat")
reset()
stub([Resp(feed([("Prvy", 1)])), RuntimeError("timeout")]
     + [Resp(feed([])) for _ in mn._FEEDS])
mn.get_market_headlines()
st = mn.last_status()
check("ok=True, ked aspon jeden feed zije", st["ok"], True)
check("pocita zive feedy", st["feeds_ok"] >= 1, True)
check("chyba nesie nazov zdroja", len(st["errors"]) >= 1, True)
check("items nesu titulok", st["items"][0]["title"], "Prvy")
check("items nesu zdroj", bool(st["items"][0]["source"]), True)

reset()
stub([RuntimeError("x") for _ in mn._FEEDS])
mn.get_market_headlines()
check("ok=False az ked padnu VSETKY", mn.last_status()["ok"], False)

print("\n7) Zapnute LEN na dohodnutych tickeroch")
import assets  # noqa: E402
on = sorted(a["name"] for a in assets.ALL_ASSETS if a.get("market_news"))
check("zoznam tickerov", on, ["ADA", "NEAR", "ZEC"])

print("\n8) Prompt skenu: blok sa objavi len ked su titulky")
import claude_analyst  # noqa: E402
A = next(a for a in assets.ALL_ASSETS if a["name"] == "ADA")
TA = {"last_price": 1.0, "atr14": 0.05}
p_bez = claude_analyst._build_triage_prompt(A, TA, {}, {"session": "US"}, None, None,
                                             None, None, None, None, None)
p_s = claude_analyst._build_triage_prompt(A, TA, {}, {"session": "US"}, None, None,
                                           None, None, None, None, None,
                                           market_news=[{"title": "Zcash flaw", "age_hours": 3}])
check("bez titulkov sa blok nevlozi", "Trhove krypto titulky" in p_bez, False)
check("s titulkami sa vlozi", "Trhove krypto titulky" in p_s, True)
check("titulok je v prompte", "Zcash flaw" in p_s, True)
check("prompt varuje, ze titulky NIE su o tickeri", "NETYKA" in p_s, True)
check("a ze samotna pritomnost nie je dovod na ANO",
      "NIE JE dovod na ANO" in p_s, True)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
