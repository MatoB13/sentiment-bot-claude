"""Google News pre slabo pokryte tickery (2026-09-14, google_news_client.py).
Siet je nahradena - test nikdy nevola Google, Clauda ani burzu."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import ast
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape as xml_escape

DB = os.environ["TEMP"].replace("\\", "/") + "/gnews.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import config  # noqa: E402
import google_news_client as gn  # noqa: E402
import market_data  # noqa: E402
import marketaux_client  # noqa: E402
import social_sentiment  # noqa: E402
import strike_client  # noqa: E402
import trade_cycle as tc  # noqa: E402
from db import CycleLog, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<70} {str(got)[:26]!r} (ocakavane {want!r})")


NOW = datetime.now(timezone.utc)


def item(title, hours, source="Reuters"):
    ts = format_datetime(NOW - timedelta(hours=hours))
    title = xml_escape(title)       # ako skutocne RSS ("Oil &amp; Gas", "S&amp;P")
    return (f"<item><title>{title} - {source}</title><link>https://news.google.com/x</link>"
            f"<pubDate>{ts}</pubDate><source url=\"https://x\">{source}</source></item>")


def rss(*items):
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><title>t</title>"
            + "".join(items) + "</channel></rss>").encode("utf-8")


class R:
    def __init__(self, body, code=200):
        self.content, self.status_code = body, code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


calls = []
feeds = {}          # (hl, dotaz bez when) -> bytes
state = {"error": None}


def fake_get(url, **kw):
    calls.append((url, kw))
    if state["error"]:
        raise state["error"]
    qs = parse_qs(urlparse(url).query)
    q = qs["q"][0].rsplit(" when:", 1)[0]
    return R(feeds.get((qs["hl"][0], q), rss()))


gn.requests.get = fake_get


def reset():
    gn._cache.clear()
    gn._last_status.clear()
    calls.clear()


A = {a["name"]: a for a in assets.ALL_ASSETS}
config.GOOGLE_NEWS_ENABLED = True
config.GOOGLE_NEWS_MAX_AGE_HOURS, config.GOOGLE_NEWS_MAX_ITEMS, config.GOOGLE_NEWS_MAX_NEW_ITEMS = 24.0, 8, 10
config.GOOGLE_NEWS_CACHE_MINUTES, config.GOOGLE_NEWS_QUERY_DAYS = 30.0, 2

print("1) Ktore tickery maju Google News")
covered = sorted(a["name"] for a in assets.ALL_ASSETS if gn.covers(a))
check("slabo pokryte tickery + WTI/GOLD (15.9.)", covered,
      sorted(["ADA", "NEAR", "NIGHT", "PUMP", "HYPE", "ZEC", "CRCL", "AAOI", "MINIMAX", "ZHIPU", "UNITREE",
              "WTI", "GOLD"]))
check("dobre pokryte (NVDA/GOOGL/TSLA/BTC/NAS100) nie",
      [n for n in ("NVDA", "GOOGL", "TSLA", "BTC", "NAS100") if gn.covers(A[n])], [])
check("WTI/GOLD len Reuters a Investing.com (bez site: lavina SEO)",
      [q for n in ("WTI", "GOLD") for _, q in A[n]["google_news"]["queries"]
       if not (q.endswith("site:reuters.com") or q.endswith("site:investing.com"))], [])
check("kazdy ma regex 'titulok je o tickeri'", [n for n in covered if gn._match_re(A[n]) is None], [])
check("cinske firmy maju aj cinsky dotaz",
      [n for n in ("MINIMAX", "ZHIPU", "UNITREE") if not any(l == "zh" for l, _ in A[n]["google_news"]["queries"])], [])

print("\n2) Stiahnutie, URL, parsovanie")
reset()
Z = A["ZHIPU"]
feeds[("en-US", Z["google_news"]["queries"][0][1])] = rss(
    item("Z.ai shares tumble over 10% after $5 billion fundraising", 2, "CNBC"),
    item("Alibaba raises stake in Zhipu ahead of listing", 3, "Bloomberg"),
    item("Z.ai shares tumble over 10% after $5 billion fundraising", 2.5, "Yahoo"),   # duplicita
    item("Zhipu Price Prediction 2026: can GLM stock double?", 1, "CoinCodex"),
    item("China AI stocks slide as investors reassess valuations", 1.5, "FT"),        # nespomina ticker
    item("Zhipu old story", 30, "Reuters"),
    item("Zhipu presale spam: Pepeto Presale Pulls $10.9 Million", 1, "openPR.com"),
)
feeds[("zh-CN", "智谱")] = rss(item("智谱完成约50亿美元融资，投向模型研发", 4, "新京报"),
                              item("智谱(02513)股票股价_股价行情_讨论", 5, "雪球"))
since = NOW - timedelta(hours=3.5)
h = gn.get_headlines_for_asset(Z, since=since)
check("2 dotazy (en + zh)", len(calls), 2)
url, kw = calls[0]
qs = parse_qs(urlparse(url).query)
check("okno vyhladavania when:2d", qs["q"][0].endswith(" when:2d"), True)
check("anglicky locale", (qs["hl"][0], qs["gl"][0], qs["ceid"][0]), ("en-US", "US", "US:en"))
check("cinsky locale", parse_qs(urlparse(calls[1][0]).query)["ceid"][0], "CN:zh-Hans")
check("prehliadacovy User-Agent", "Mozilla" in kw["headers"]["User-Agent"], True)
titles = [i["title"] for i in h]
check("' - Zdroj' odrezane z titulku", "Z.ai shares tumble over 10% after $5 billion fundraising" in titles, True)
check("zdroj ako samostatne pole", h[0]["source"], "CNBC")
check("udalost so slovom 'stake' PRESLA", "Alibaba raises stake in Zhipu ahead of listing" in titles, True)
check("cenova predpoved vyradena", any("Prediction" in t for t in titles), False)
check("titulok bez tickera vyradeny", any("China AI stocks" in t for t in titles), False)
check("duplicita len raz", titles.count("Z.ai shares tumble over 10% after $5 billion fundraising"), 1)
check("stara (30 h) vyradena", any("old story" in t for t in titles), False)
check("openPR (platene tlacove spravy) vyradene", any("presale" in t.lower() for t in titles), False)
check("cinsky titulok presiel", "智谱完成约50亿美元融资，投向模型研发" in titles, True)
check("cinska kotacna stranka (股价行情) vyradena", any("股价行情" in t for t in titles), False)
check("NOVE podla posledneho plneho pohladu", [(i["title"][:12], i["new"]) for i in h],
      [("Z.ai shares ", True), ("Alibaba rais", True), ("智谱完成约50亿美元融资", False)])
st = gn.last_status("ZHIPU")
check("stav: ok, dotazy, pocty", (st["ok"], st["queries_ok"], st["queries_total"], st["count"], st["new_count"]),
      (True, 2, 2, 3, 2))

print("\n3) Rutinny filter (vzorky z 14.9.)")
ROUT = ["Cardano, HYPE, SHIB and XLM Price Analysis for September 14: Key Levels to Watch",
        "Tidal Investments LLC Purchases Shares of 671,514 Hyperliquid Strategies Inc $PURR",
        "Circle Internet Group, Inc. $CRCL Shares Bought by California State Teachers Retirement System",
        "42,583 Shares in Circle Internet Group, Inc. $CRCL Bought by Arizona State Retirement System",
        "Circle Internet Group, Inc. Class A (CRCL) Live Share Price, Invest From India",
        "Hyperliquid(HYPE) Stock Price Today | Quotes & News",
        "NEAR Protocol Price (NEAR INR)", "ZEC Price ZCASH TA ZEC Technical Analysis",
        "Zcash Volatility Explained: 4.5% Swing Due to Leverage Flush",
        "Hyperliquid (HYPE) Price Movement Explained: Catalysts and Dynamics"]
KEEP = ["Z.ai shares tumble over 10% after $5 billion fundraising",
        "Applied Optoelectronics (NASDAQ:AAOI) Shares Gap Down - What's Next?",
        "Cardano DeFi Hit as Splash DEX Loses Over 2.4M ADA and 1.98M OADA in Exploit",
        "Pump.fun Drops Cashback for New Launches, Adds Holder Rewards",
        "Alibaba raises stake in Zhipu ahead of listing",
        "Zcash (ZEC), Litecoin (LTC) Achieve New Listing in Europe Despite 2027 Ban Looming"]
check("rutinne vyradene (10)", [t[:40] for t in ROUT if not gn.is_routine(t)], [])
check("udalosti ponechane (6)", [t[:40] for t in KEEP if gn.is_routine(t)], [])

print("\n3b) WTI a GOLD - titulky z Reuters/Investing.com (vzorky z 15.9.)")
gn._cache.pop("WTI", None)      # kes ZHIPU zo sekcie 2 ostava - sekcia 4 ho testuje
gn._cache.pop("GOLD", None)
W, G = A["WTI"], A["GOLD"]
INV = "Investing.com"
feeds[("en-US", W["google_news"]["queries"][0][1])] = rss(
    item("EXCLUSIVE: ADNOC Trading buys millions of barrels of Iraqi crude, sources say", 1.0, "Reuters"),
    item("Poland to revive windfall tax on oil firms, use proceeds for fuel price relief", 3.3, "Reuters"))
feeds[("en-US", W["google_news"]["queries"][1][1])] = rss(
    item("Oil prices edge higher amid Saudi Arabia pipeline outage, Houthi attacks By Reuters", 5.3, INV),
    item("Oil prices edge higher amid Saudi Arabia pipeline outage, Houthi attacks", 5.4, INV),
    item("Soaring Oil Prices Put Fed on Track for September Rate Hike By Investing.com", 1.5, INV),
    item("Crude Oil WTI Futures Live Chart", 2.2, INV),
    item("Crude Oil WTI consolidates near $105 resistance: Live levels", 5.9, INV),
    item("AgEagle director Brent Klavon buys $4,999 in company stock", 2.1, INV),
    item("India’s Edible Oil Imports Surge as Refiners Build Festival Stocks By Kedia Advisory", 3.4, INV),
    item("4 Oil & Gas Stocks Stifel Is Constructive On as Energy Prices Stay High", 1.2, INV),
    item("Brent Oil Perpetual Futures News Today", 8.0, INV),
    item("Fed seen hiking rates on Wednesday", 1.0, INV))                   # nespomina ropu
wt = [i["title"] for i in gn.get_headlines_for_asset(W, since=NOW - timedelta(hours=6))]
check("WTI: udalosti presli (ADNOC, Poland, Saudi, Fed/oil)",
      [t[:20] for t in wt], ["EXCLUSIVE: ADNOC Tra", "Soaring Oil Prices P", "Poland to revive win", "Oil prices edge high"])
check("WTI: ' By Reuters' odrezane a duplicita len raz",
      wt.count("Oil prices edge higher amid Saudi Arabia pipeline outage, Houthi attacks"), 1)
check("WTI: ' By Investing.com' odrezane", "Soaring Oil Prices Put Fed on Track for September Rate Hike" in wt, True)
check("WTI: live chart/levels, obchod manazera, jedly olej, zoznam akcii, stranka nastroja vyradene",
      [t for t in wt if re.search(r"Live|AgEagle|Edible|4 Oil|Perpetual", t)], [])
check("' By X' sa reze LEN pri Investing.com",
      gn._clean_title("Deal Approved By Federal Regulators - Reuters", "Reuters"), "Deal Approved By Federal Regulators")
feeds[("en-US", G["google_news"]["queries"][0][1])] = rss(
    item("India's August goods trade deficit narrows as gold imports plunge", 4.5, "Reuters"))
feeds[("en-US", G["google_news"]["queries"][1][1])] = rss(
    item("Gold Falls as Energy Prices Strengthen Rate Hike Expectations", 0.4, INV),
    item("Citi cuts gold exposure on hawkish Fed outlook By Investing.com", 4.5, INV),
    item("Gold slips below $4,300 as firmer dollar, Fed hike bets pressure bullion", 7.2, INV),
    item("Solstice Gold completes Leckie Gold Zone acquisition in Ontario", 3.3, INV),
    item("Big Ridge Gold appoints Garett Macdonald to board of directors", 3.3, INV),
    item("Why is Wesdome Gold stock sliding today?", 5.0, INV),
    item("Gold.com CEO Gregory Roberts sells $458k in shares", 6.0, INV),
    item("Dakota Gold at H.C. Wainwright conference: richmond hill gains traction", 7.0, INV),
    item("Galantas Gold warrants exercised for 25,000 shares at C$0.12", 8.0, INV),
    item("Mako Mining enters gold stream deal with Sailfish Royalty", 9.0, INV),
    item("Meridian Mining added to VanEck junior gold miners ETF", 10.0, INV),
    item("China Southern Shanghai Gold ETF Analysis", 11.0, INV),
    item("626A Stock Price | iFreeETF Gold plus Income ETF", 12.0, INV),
    item("Goldman Sachs raises S&P 500 target", 2.0, INV))                  # "Goldman" nie je zlato
gt = [i["title"] for i in gn.get_headlines_for_asset(G, since=NOW - timedelta(hours=8))]
check("GOLD: len titulky o cene zlata (4)", [t[:20] for t in gt],
      ["Gold Falls as Energy", "India's August goods", "Citi cuts gold expos", "Gold slips below $4,"])
check("stare tickery: nove pravidla nezasiahli vzorky udalosti", [t[:40] for t in KEEP if gn.is_routine(t)], [])

print("\n4) Kesovanie a vypadok")
before = len(calls)
gn.get_headlines_for_asset(Z, since=since)
check("druhe volanie do 30 min = ziadna siet", len(calls), before)
gn._cache["ZHIPU"] = (gn._cache["ZHIPU"][0] - 31 * 60,) + gn._cache["ZHIPU"][1:]
gn.get_headlines_for_asset(Z, since=since)
check("po 31 min znova stiahne", len(calls), before + 2)
reset()
state["error"] = ConnectionError("dns")
check("pri chybe vrati []", gn.get_headlines_for_asset(Z, since=since), [])
st = gn.last_status("ZHIPU")
check("stav ok=False s chybami", (st["ok"], len(st["errors"])), (False, 2))
n = len(calls)
gn.get_headlines_for_asset(Z, since=since)
check("neuspech sa kesuje (neklope znova)", len(calls), n)
state["error"] = None
config.GOOGLE_NEWS_ENABLED = False
reset()
check("vypnute cez ENV -> [] bez siete", (gn.get_headlines_for_asset(Z), len(calls)), ([], 0))
config.GOOGLE_NEWS_ENABLED = True
check("ticker bez dotazov -> []", gn.get_headlines_for_asset(A["NVDA"]), [])

print("\n5) Stropy")
reset()
feeds[("en-US", Z["google_news"]["queries"][0][1])] = rss(*[item(f"Zhipu news number {k}", 0.5 + k * 0.1) for k in range(15)])
feeds[("zh-CN", "智谱")] = rss()
config.GOOGLE_NEWS_MAX_NEW_ITEMS = 5
check("MAX_NEW_ITEMS=5", len(gn.get_headlines_for_asset(Z, since=NOW - timedelta(hours=10))), 5)
config.GOOGLE_NEWS_MAX_NEW_ITEMS = 10
reset()
check("bez since: nove do 24 h, strop 10", len(gn.get_headlines_for_asset(Z)), 10)

print("\n6) Prompty")
items = [{"title": "Z.ai shares tumble {10%}", "age_hours": 2.0, "source": "CNBC", "new": True},
         {"title": "智谱完成融资", "age_hours": 5.0, "source": "新京报", "new": False}]
tp = ca._build_triage_prompt(Z, {"last_price": 1}, {}, {}, None, None, None, None, 6.0, None, None,
                             None, None, None, items)
check("sken: blok Google News", "## Google News: titulky o tomto nastroji" in tp, True)
check("sken: NOVE so zdrojom", "[NOVE, pred 2.0 h] Z.ai shares tumble {10%} (CNBC)" in tp, True)
check("sken: stara bez znacky", "[pred 5.0 h] 智谱完成融资 (新京报)" in tp, True)
check("sken: bez titulkov ziadny blok",
      "Google News" in ca._build_triage_prompt(Z, {"last_price": 1}, {}, {}, None, None, None, None, 6.0,
                                               None, None), False)
TA = {"last_price": 10.0, "atr14": 0.3, "recent_candles": [], "trend": "mild_uptrend"}
p = ca._build_user_prompt(Z, TA, {}, {}, [], None, None, google_news=items, google_since=NOW - timedelta(hours=3))
check("plny cyklus: blok Google News", "## Google News o ZHIPU" in p, True)
check("plny cyklus: NOVÉ so zdrojom", "[NOVÉ, pred 2.0 h] Z.ai shares tumble {10%} (CNBC)" in p, True)
check("plny cyklus: bez titulkov nic", "Google News" in ca._build_user_prompt(Z, TA, {}, {}, [], None, None), False)
check("v bloku nie je prah", "prah" in ca._google_news_block("ZHIPU", items, None).lower(), False)

print("\n7) Staticke: oba plne cykly dostanu Google News, stlpec existuje")
tree = ast.parse(open(os.path.join(_ROOT, "trade_cycle.py"), encoding="utf-8").read())
for fn in ("analyze", "analyze_position_health", "triage"):
    hits = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == fn]
    kws = {kw.arg for hh in hits for kw in hh.keywords}
    check(f"claude_analyst.{fn}(...) dostava google_news", "google_news" in kws, True)
check("stlpec cycle_logs.google_news", "google_news" in {c.name for c in CycleLog.__table__.columns}, True)

print("\n8) Integracne: run_cycle_for_asset (ADA, mock)")
ADA = A["ADA"]
SYM = ADA["strike_symbol"]
TA_ADA = {"last_price": 0.58, "atr14": 0.01, "rsi14": 50.0, "trend": "mild_downtrend", "adx14": 18.0,
          "recent_candles": [[0.57, 0.58, 0.57, 0.58, 100.0]] * 48, "book_imbalance": 0.1,
          "price_range": {"in_range": True, "at_edge": None, "failed_conditions": [], "efficiency_ratio": 0.4}}
MARKET = {"mark_price": 0.58, "order_tick_price": 0.0001, "order_market_step_size": 1.0,
          "order_market_min_size": 1.0, "order_market_max_size": 1e9, "order_min_notional": 1.0,
          "bid1_price": 0.5799, "ask1_price": 0.5801, "bid1_size": 100, "ask1_size": 100, "index_price": 0.58,
          "margin_tiers": [{"max_notional": 1e9, "max_leverage": 10, "maintenance_margin_rate": 0.01}]}
strike_client.get_market = lambda sym: MARKET
market_data.get_market_snapshot = lambda a, sess: dict(TA_ADA)
social_sentiment.fetch_recent_posts = lambda n: []
marketaux_client.get_news_sentiment = lambda q: []
config.ALPACA_API_KEY_ID = config.ALPACA_API_SECRET_KEY = ""   # Benzinga mimo hry
seen = {}


def fake_triage(*a, **kw):
    seen["triage"] = kw.get("google_news")
    return ({"worth_full_look": True, "attention": 70, "reason": "exploit na Splash DEX"},
            {"input_tokens": 3000, "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 200})


def fake_analyze(*a, **kw):
    seen["analyze"] = kw.get("google_news")
    seen["since"] = kw.get("google_since")
    return ({"direction": "none", "confidence": 40, "stop_loss_price": 0.56, "take_profit_price": 0.6,
             "reasoning": "r", "key_assumptions": "k"}, [],
            {"input_tokens": 9000, "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 800})


ca.triage, ca.analyze = fake_triage, fake_analyze
reset()
feeds[("en-US", ADA["google_news"]["queries"][0][1])] = rss(
    item("Cardano DeFi Hit as Splash DEX Loses Over 2.4M ADA in Exploit", 1.0, "The Crypto Basic"),
    item("Cardano founder comments on governance", 5.0, "CoinDesk"))
s = get_session()
s.add(CycleLog(symbol=SYM, outcome="rejected", usage_output_tokens=500,
               created_at=(NOW - timedelta(hours=3)).replace(tzinfo=None)))
s.commit()
s.close()
config.TRIAGE_MODE = "shadow"
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
s = get_session()
log = s.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
s.close()
check("sken dostal Google News", [i["title"][:20] for i in seen.get("triage") or []],
      ["Cardano DeFi Hit as ", "Cardano founder comm"])
check("plny cyklus dostal to iste", seen.get("analyze") is seen.get("triage"), True)
check("NOVE voci plnemu pohladu pred 3 h", [i["new"] for i in seen.get("analyze") or []], [True, False])
check("CycleLog.google_news zapisany", (log.google_news or {}).get("new_count"), 1)
check("triage.google_news zapisany", ((log.triage or {}).get("google_news") or {}).get("count"), 2)
config.GOOGLE_NEWS_FULL_CYCLE = False
seen.clear()
gn._cache.clear()
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
check("GOOGLE_NEWS_FULL_CYCLE=false: sken ano, plny cyklus nie",
      (bool(seen.get("triage")), seen.get("analyze")), (True, None))
config.GOOGLE_NEWS_FULL_CYCLE = True
config.TRIAGE_MODE = "off"

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
