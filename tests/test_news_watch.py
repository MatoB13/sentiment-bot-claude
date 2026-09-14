"""Tienove meranie rychlej vrstvy sprav (news_watch.py, 2026-09-14).
Zdroje su nahradene - test nevola Alpaca, Google, Clauda ani burzu."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/newswatch.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import alpaca_news_client as an  # noqa: E402
import assets  # noqa: E402
import config  # noqa: E402
import google_news_client as gn  # noqa: E402
import news_watch as nw  # noqa: E402
from db import CycleLog, NewsEvent, PriceMinute, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:28]!r} (ocakavane {want!r})")


A = {a["name"]: a for a in assets.ALL_ASSETS}
ZH, ADA = A["ZHIPU"], A["ADA"]
T0 = datetime(2026, 9, 14, 12, 0)                  # naive UTC
aware = lambda d: d.replace(tzinfo=timezone.utc)   # noqa: E731

src = {"benzinga": {}, "google": {}, "fail": set()}


def fake_bz(asset):
    if ("benzinga", asset["name"]) in src["fail"]:
        raise RuntimeError("alpaca down")
    return [{"title": t, "created": c, "routine": r} for t, c, r in src["benzinga"].get(asset["name"], [])]


def fake_gn(asset):
    if ("google", asset["name"]) in src["fail"]:
        raise RuntimeError("google down")
    return [{"title": t, "created": c, "source": m} for t, c, m in src["google"].get(asset["name"], [])]


nw.alpaca_news_client.items_for_asset = fake_bz
nw.google_news_client.items_for_asset = fake_gn

s = get_session()
sym = ZH["strike_symbol"]
# minutove ceny: 100 do T0, potom rast po 12 h na 112, s knotom 115 v case +6 h
for m in range(-300, 13 * 60 + 1, 1):
    ts = T0 + timedelta(minutes=m)
    price = 100.0 if m <= 0 else 100.0 + 12.0 * m / 720
    if m == 360:
        price = 115.0
    s.add(PriceMinute(symbol=sym, ts=ts, price=price))
s.add(PriceMinute(symbol=sym, ts=T0 - timedelta(hours=4), price=95.0))
s.add(CycleLog(symbol=sym, outcome="rejected", ta={"atr14": 1.5}, created_at=T0 - timedelta(hours=2)))
s.commit()

print("1) Prvy beh po starte = baseline (spravy uz boli v zasobniku)")
src["benzinga"]["ZHIPU"] = [("Zhipu old story", aware(T0 - timedelta(hours=10)), False)]
src["google"]["ZHIPU"] = [("Z.ai earlier headline", aware(T0 - timedelta(hours=8)), "CNBC")]
n = nw._collect(ZH, s, T0)
rows = s.query(NewsEvent).filter_by(symbol=sym).all()
check("zapisane 2 spravy", n, 2)
check("obe baseline", [r.baseline for r in rows], [True, True])

print("\n2) Nova sprava = udalost s cenou a ATR")
src["google"]["ZHIPU"].insert(0, ("Z.ai shares tumble over 10% after $5 billion fundraising",
                                   aware(T0 - timedelta(minutes=20)), "CNBC"))
src["benzinga"]["ZHIPU"].insert(0, ("Zhipu files for placement - routine price target", aware(T0 - timedelta(minutes=5)), True))
n = nw._collect(ZH, s, T0)
ev = s.query(NewsEvent).filter_by(symbol=sym, source="google", baseline=False).one()
check("pribudli 2 nove", n, 2)
check("  nie baseline", ev.baseline, False)
check("  vydane / videne", (ev.published_at, ev.seen_at), (T0 - timedelta(minutes=20), T0))
check("  cena v case videnia (price_minutes)", ev.price_at_seen, 100.0)
check("  cena 4 h pred (bola sprava az po pohybe?)", ev.price_pre_4h, 95.0)
check("  ATR z posledneho cyklu", ev.atr, 1.5)
check("  medium", ev.media, "CNBC")
bz = s.query(NewsEvent).filter_by(symbol=sym, source="benzinga", baseline=False).one()
check("  Benzinga: priznak rutinneho titulku ulozeny", bz.routine, True)

print("\n3) Duplicity")
check("tie iste spravy znova -> nic", nw._collect(ZH, s, T0 + timedelta(minutes=5)), 0)
src["benzinga"]["ZHIPU"].insert(0, ("Z.ai Shares Tumble Over 10% After $5 Billion Fundraising!",
                                     aware(T0 - timedelta(minutes=19)), False))
check("rovnaky titulok z Benzingy (ina velkost pismen) -> nic", nw._collect(ZH, s, T0 + timedelta(minutes=10)), 0)

print("\n4) Vypadok zdroja pri starte: baseline az po prvom neprazdnom vysledku")
src["fail"].add(("google", "ADA"))
src["benzinga"]["ADA"] = [("Cardano governance vote", aware(T0 - timedelta(hours=3)), False)]
nw._collect(ADA, s, T0)
src["fail"].clear()
src["google"]["ADA"] = [("Cardano DEX exploit older", aware(T0 - timedelta(hours=6)), "The Crypto Basic")]
nw._collect(ADA, s, T0 + timedelta(minutes=5))
g = s.query(NewsEvent).filter_by(symbol=ADA["strike_symbol"], source="google").one()
check("prvy neprazdny Google po vypadku = baseline (nie falosna latencia)", g.baseline, True)
check("zlyhanie zdroja nezhodi zber ostatnych", s.query(NewsEvent).filter_by(symbol=ADA["strike_symbol"], source="benzinga").count(), 1)

print("\n5) Dopinanie cien po sprave")
nw._fill_outcomes(s, T0 + timedelta(minutes=90))
ev = s.query(NewsEvent).filter_by(symbol=sym, source="google", baseline=False).one()
check("po 1.5 h: cena +1 h doplnena", round(ev.price_1h, 2), round(100 + 12 * 60 / 720, 2))
check("  +4 h este nie", ev.price_4h, None)
nw._fill_outcomes(s, T0 + timedelta(hours=13))
ev = s.query(NewsEvent).filter_by(symbol=sym, source="google", baseline=False).one()
check("po 13 h: +4 h", round(ev.price_4h, 2), round(100 + 12 * 240 / 720, 2))
check("  +12 h", round(ev.price_12h, 2), 112.0)
check("  max za 12 h (knot 115)", ev.high_12h, 115.0)
check("  min za 12 h", round(ev.low_12h, 3), round(100 + 12 / 720, 3))
base = s.query(NewsEvent).filter_by(symbol=sym, baseline=True).all()
check("baseline sa nedopina", [b.price_1h for b in base], [None, None])

print("\n6) poll_news: vypinac, izolacia chyb")
config.NEWS_WATCH_ENABLED = False
before = s.query(NewsEvent).count()
src["google"]["ZHIPU"].insert(0, ("Zhipu brand new", aware(datetime.now(timezone.utc)), "WSJ"))
nw.poll_news()
check("NEWS_WATCH_ENABLED=false -> nic", s.query(NewsEvent).count(), before)
config.NEWS_WATCH_ENABLED = True
nw.assets.enabled_assets = lambda: [ADA, ZH]
real_collect = nw._collect


def flaky(asset, session, now):
    if asset["name"] == "ADA":
        raise RuntimeError("DB hiccup")
    return real_collect(asset, session, now)


nw._collect = flaky
nw.poll_news()
nw._collect = real_collect
s.expire_all()
check("chyba pri ADA nezastavi ZHIPU", s.query(NewsEvent).filter(NewsEvent.title == "Zhipu brand new").count(), 1)

print("\n7) Klienti: items_for_asset nemenia stav pre cykly")
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY, config.ALPACA_NEWS_ENABLED = "k", "s", True
import importlib  # noqa: E402
real_an = importlib.reload(an)          # vratit povodnu funkciu (test ju vyssie nahradil)
real_an._pool_fetched_at = 9e18         # zasobnik "cerstvy" - ziadna siet
real_an._pool.update({1: {"id": 1, "title": "Nvidia price target raised", "created": aware(T0), "symbols": ["NVDA"], "text": ""},
                      2: {"id": 2, "title": "Nvidia wins deal", "created": aware(T0 + timedelta(minutes=1)), "symbols": ["NVDA"], "text": ""}})
real_an._pool_fetched_at = 9e18
items = real_an.items_for_asset(A["NVDA"])
check("Benzinga: vsetky titulky tickera od najnovsieho", [i["title"] for i in items], ["Nvidia wins deal", "Nvidia price target raised"])
check("  s priznakom rutinneho", [i["routine"] for i in items], [False, True])
check("  last_status nezmeneny", real_an._last_status, {})
real_gn = importlib.reload(gn)
real_gn._cache["ZHIPU"] = (9e18, [{"title": "Zhipu Price Prediction 2026", "source": "X", "created": aware(T0)},
                                  {"title": "Z.ai raises funds", "source": "WSJ", "created": aware(T0)}], [], 2)
check("Google: prefiltrovane (predpoved vyradena)", [i["title"] for i in real_gn.items_for_asset(ZH)], ["Z.ai raises funds"])
check("  last_status nezmeneny", real_gn._last_status, {})

print("\n8) Scheduler")
main_src = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
check("job news_watch je v scheduleri", 'id="news_watch"' in main_src and "news_watch.poll_news" in main_src, True)
s.close()

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
