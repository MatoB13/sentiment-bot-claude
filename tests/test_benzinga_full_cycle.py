"""Benzinga aj do plneho cyklu, znacka NOVE a siroky dotaz (2026-09-14, po NVDA).

POVOD: vyzvu Altmana/Amodeia na spomalenie AI mala Benzinga v piatok 17:05 UTC.
Sken ju dvakrat odbil ako "nesuvisiacu s tezou o DOJ/Groq", plne cykly Benzingu
vobec nedostavali a cez vikend hladali len "Nvidia Groq DOJ" - NVDA sa to
dozvedelo az v pondelok.
  A) plny cyklus a health check dostanu Benzingu; NOVE (od posledneho plneho
     pohladu) su oznacene, najviac 3 z nich aj s textom,
  B) sken vidi tu istu znacku a pravidlo "novost, nie zhoda s tezou",
  C) kazdy plny cyklus polozi aspon jeden siroky dotaz na sektor/trh.
Siet je nahradena - test nikdy nevola Alpaca, Clauda ani burzu."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import ast
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/benzinga_full.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import alpaca_news_client as an  # noqa: E402
import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
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
    print(f"  {'OK ' if good else 'CHYBA'} {label:<72} {str(got)[:24]!r} (ocakavane {want!r})")


now = datetime.now(timezone.utc)
iso = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
_next_id = [0]


def n(title, hours, symbols=("NVDA",), content="", summary=""):
    _next_id[0] += 1
    return {"id": _next_id[0], "headline": title, "created_at": iso(hours),
            "symbols": list(symbols), "content": content, "summary": summary}


class R:
    def __init__(self, body):
        self._b, self.status_code = body, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


calls = []
state = {"news": []}


def fake_get(url, **kw):
    calls.append(kw)
    return R({"news": state["news"]})


def load(news):
    an._pool.clear()
    an._pool_fetched_at = 0.0
    an._last_status.clear()
    state["news"] = news


an.requests.get = fake_get
config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY = "PKtest", "secret"
config.ALPACA_NEWS_ENABLED = True
config.ALPACA_NEWS_MAX_AGE_HOURS, config.ALPACA_NEWS_MAX_ITEMS = 12.0, 8
config.ALPACA_NEWS_POOL_HOURS, config.ALPACA_NEWS_MAX_NEW_ITEMS = 36.0, 15
config.ALPACA_NEWS_FULL_TEXT_ITEMS, config.ALPACA_NEWS_FULL_TEXT_CHARS = 3, 3000
config.ALPACA_NEWS_CACHE_MINUTES = 5.0
A = {a["name"]: a for a in assets.ALL_ASSETS}
NVDA = A["NVDA"]

print("=" * 110)
print("1) Text clanku: HTML prec, entity, chvost, skratenie, zaloha na summary")
print("=" * 110)
t = an._plain_text("<p>Nvidia&#39;s shares <b>fell</b> 3%.</p><p>More text.</p>"
                   "<p>Read Next: Some other story</p>", None)
check("HTML a entity prec, chvost 'Read Next' odrezany", t, "Nvidia's shares fell 3%. More text.")
t = an._plain_text("<p>Photo: Shutterstock</p><p>Body sentence one. Body two.</p>", None)
check("'Photo:' na zaciatku NEZREZE cely clanok", t.startswith("Photo: Shutterstock Body sentence one."), True)
check("prazdny obsah -> summary", an._plain_text("", "One sentence summary."), "One sentence summary.")
long = " ".join(f"Sentence number {i} is here." for i in range(400))
t = an._plain_text(f"<p>{long}</p>", None)
check("dlhy clanok skrateny na <= limit + znacka", len(t) <= 3000 + 4 and t.endswith("[…]"), True)
check("  skratene na hranici vety", t[:-4].rstrip().endswith("."), True)

print()
print("=" * 110)
print("2) NOVE od posledneho plneho pohladu (regresia NVDA)")
print("=" * 110)
BODY = "<p>" + "Altman and Amodei urged a slowdown of frontier AI development. " * 20 + "</p>"
load([
    n("Nvidia in Focus as Musk, Altman and Amodei Call for AI Slowdown", 13.0, content=BODY),
    n("Nvidia price target raised by analyst", 1.0, content="<p>Routine rating story.</p>"),
    n("Nvidia unusual options activity spotted", 2.0, content="<p>Options story.</p>"),
    n("Nvidia wins sovereign AI deal in Saudi Arabia", 3.0, content="<p>Deal story.</p>"),
    n("Nvidia shares slip premarket", 4.0, content=""),
    n("Old Nvidia story from before last look", 16.0, content="<p>Old.</p>"),
    n("Very old Nvidia story", 30.0, content="<p>Very old.</p>"),
    n("Beyond pool horizon", 40.0, content="<p>x</p>"),
])
since = now - timedelta(hours=14)
items = an.get_headlines_for_asset(NVDA, since=since)
titles = [i["title"] for i in items]
check("stiahnute s plnym textom (include_content=true)", calls[-1]["params"]["include_content"], "true")
check("zasobnik drzi 36 h (40 h stary vyhodeny)", len(an._pool), 7)
check("sprava 13 h stara, ale PO poslednom pohlade -> v zozname",
      "Nvidia in Focus as Musk, Altman and Amodei Call for AI Slowdown" in titles, True)
altman = next(i for i in items if "Altman" in i["title"])
check("  ...oznacena ako NOVA", altman["new"], True)
check("  ...a dostane plny text", altman["full_text"], True)
check("stara (16 h, pred pohladom, nad 12 h) -> vynechana",
      "Old Nvidia story from before last look" in titles, False)
check("pocet NOVYCH", sum(1 for i in items if i["new"]), 5)
with_text = [i["title"] for i in items if i["full_text"]]
check("text dostanu 3 clanky", len(with_text), 3)
check("  prednost maju nerutinne (dohoda, AI spomalenie)",
      {"Nvidia wins sovereign AI deal in Saudi Arabia",
       "Nvidia in Focus as Musk, Altman and Amodei Call for AI Slowdown"} <= set(with_text), True)
check("  clanok bez obsahu text nedostane", "Nvidia shares slip premarket" in with_text, False)
check("  tretie miesto: najnovsi rutinny (price target 1 h)",
      "Nvidia price target raised by analyst" in with_text, True)
st = an.last_status("NVDA")
check("stav: new_count", st["new_count"], 5)
check("stav: polozky BEZ textu (do DB nejde)", any("text" in i for i in st["items"]), False)
check("stav: polozky nesu new/full_text", all("new" in i and "full_text" in i for i in st["items"]), True)
check("stav: since zapisany", st["since"][:16], since.isoformat()[:16])

print()
print("   bez `since` (plny pohlad este nebol) = nove je vsetko do 12 h")
items = an.get_headlines_for_asset(NVDA)
check("  13 h stara uz nie je v zozname", any("Altman" in i["title"] for i in items), False)
check("  vsetky zobrazene su nove", all(i["new"] for i in items), True)

print()
print("   cerstvy pohlad pred 2.5 h: nove len mladsie, stare dopĺňaju do MAX_ITEMS")
items = an.get_headlines_for_asset(NVDA, since=(now - timedelta(hours=2.5)).replace(tzinfo=None))
check("  naivny since funguje (UTC)", [i["new"] for i in items], [True, True, False, False])
check("  stare do 12 h zostali ako kontext bez textu",
      [i["full_text"] for i in items if not i["new"]], [False, False])

print()
print("   strop na nove titulky")
config.ALPACA_NEWS_MAX_NEW_ITEMS, config.ALPACA_NEWS_MAX_ITEMS = 1, 3
items = an.get_headlines_for_asset(NVDA, since=now - timedelta(hours=2.5))
check("  MAX_NEW_ITEMS=1, MAX_ITEMS=3 -> 1 nova + 2 stare", [i["new"] for i in items], [True, False, False])
config.ALPACA_NEWS_MAX_NEW_ITEMS, config.ALPACA_NEWS_MAX_ITEMS = 15, 8

print()
print("   titulky s HTML entitami, prednost clankov s nazvom tickera v titulku (ZEC)")
load([
    n("Hunter Biden memecoin &amp; politics", 1.0, symbols=("BTCUSD", "ZECUSD"), content="<p>a</p>"),
    n("Senate crypto roundup", 2.0, symbols=("BTCUSD", "ZECUSD"), content="<p>b</p>"),
    n("Warren on meme tokens", 3.0, symbols=("BTCUSD", "ZECUSD"), content="<p>c</p>"),
    n("Zcash privacy upgrade ships", 5.0, symbols=("ZECUSD",), content="<p>d</p>"),
])
config.ALPACA_NEWS_FULL_TEXT_ITEMS = 2
zi = an.get_headlines_for_asset(A["ZEC"], since=now - timedelta(hours=14))
check("entita v titulku dekodovana", zi[0]["title"], "Hunter Biden memecoin & politics")
check("text: najprv clanok so 'Zcash' v titulku, potom najnovsi",
      [i["title"] for i in zi if i["full_text"]],
      ["Hunter Biden memecoin & politics", "Zcash privacy upgrade ships"])
config.ALPACA_NEWS_FULL_TEXT_ITEMS = 3

print()
print("=" * 110)
print("3) Blok v prompte plneho cyklu a health checku")
print("=" * 110)
load([
    n("Nvidia in Focus as Musk, Altman and Amodei Call for AI Slowdown", 13.0, content=BODY),
    n("Nvidia price target raised by analyst", 1.0, content="<p>Routine rating story.</p>"),
    n("Nvidia unusual options activity spotted", 2.0, content="<p>Options story.</p>"),
    n("Nvidia wins sovereign AI deal in Saudi Arabia", 3.0, content="<p>Deal story.</p>"),
    n("Nvidia shares slip premarket", 4.0, content=""),
])
items = an.get_headlines_for_asset(NVDA, since=since)
blk = ca._benzinga_block("NVDA", items, since)
check("hlavicka bloku", "## Správy Benzinga o NVDA" in blk, True)
check("NOVE s vekom", "[NOVÉ, pred 13.0 h] Nvidia in Focus as Musk" in blk, True)
check("text pod novym clankom", "\n  Text: Altman and Amodei urged" in blk, True)
check("pocet textov v bloku = 3", blk.count("\n  Text: "), 3)
check("cas posledneho pohladu v hlavicke", since.strftime("%H:%M") in blk, True)
check("pravidlo: aj ked NESÚVISÍ s tézou", "NESÚVISÍ s doterajšou tézou" in blk, True)
check("prazdny zoznam -> ziadny blok", ca._benzinga_block("NVDA", [], since), "")
check("None -> ziadny blok", ca._benzinga_block("NVDA", None, None), "")
old_only = [{"title": "Old {braces} story", "age_hours": 5.0, "new": False, "full_text": False, "text": "x"}]
b2 = ca._benzinga_block("NVDA", old_only, None)
check("stara polozka bez znacky NOVE a bez textu", ("[pred 5.0 h] Old {braces} story" in b2, "Text:" in b2),
      (True, False))

TA = {"last_price": 180.0, "atr14": 3.0, "recent_candles": [], "trend": "mild_uptrend"}
p = ca._build_user_prompt(NVDA, TA, {}, {}, [], None, "DOJ vysetruje Groq", now - timedelta(hours=14),
                          benzinga_news=items, benzinga_since=since)
check("rozhodovaci prompt obsahuje blok Benzinga", "## Správy Benzinga o NVDA" in p, True)
check("  ...s textom noveho clanku", "Altman and Amodei urged" in p, True)
check("  prev_block pyta aj siroky dotaz", "ŠIROKÝ dotaz na sektor/trh" in p, True)
op = {"direction": "Long", "entry_price": 180.0, "live_price": 178.0, "stop_loss_price": 175.0,
      "take_profit_price": 190.0, "leverage": 10, "opened_at_str": "x", "hours_held": 2.0,
      "unrealized_pnl_usd": -10.0, "unrealized_pnl_pct": -1.0}
ph = ca._build_user_prompt(NVDA, TA, {}, {}, [], None, None, open_position=op,
                           benzinga_news=items, benzinga_since=since)
check("health prompt obsahuje blok Benzinga", "## Správy Benzinga o NVDA" in ph, True)
p0 = ca._build_user_prompt(NVDA, TA, {}, {}, [], None, None)
check("bez Benzingy sa prompt nemeni (ziadny blok)", "Benzinga" in p0, False)
check("v bloku nie je ziadny prah", "prah" in blk.lower(), False)
chars = sum(len(i["text"]) for i in items if i["full_text"])
print(f"       texty v prompte: {chars} znakov (~{chars / 4:.0f} tokenov)")
check("texty spolu <= 3 x limit", chars <= 3 * (config.ALPACA_NEWS_FULL_TEXT_CHARS + 4), True)

print()
print("=" * 110)
print("4) Sken (B): znacka NOVE a pravidlo 'novost, nie zhoda s tezou'")
print("=" * 110)
tp = ca._build_triage_prompt(NVDA, {"last_price": 1}, {}, {}, None, "DOJ vysetruje Groq", None, None,
                             14.0, None, None, None, items)
check("NOVY titulok ma znacku", "[NOVE, pred 13.0 h] Nvidia in Focus as Musk" in tp, True)
check("sken NEDOSTAVA texty clankov", "Altman and Amodei urged" in tp, False)
check("pravidlo v bloku: aj ked NESUVISI s predpokladmi", "NESUVISI s predpokladmi" in tp, True)
check("rutinne stale nie su dovod", "NIE SU dovod na ANO" in tp, True)
check("system prompt skenu: posudzuj novost, nie zhodu s predpokladmi",
      "NIE podla toho, ci suvisi s predpokladmi" in ca.TRIAGE_SYSTEM_PROMPT, True)
check("system prompt skenu stale bez slova 'prah'", "prah" in ca.TRIAGE_SYSTEM_PROMPT.lower(), False)

print()
print("=" * 110)
print("5) Web search (C): siroky dotaz v systemovom prompte")
print("=" * 110)
sp = ca.SYSTEM_PROMPT_SHARED
check("pravidlo o ŠIROKOM dotaze", "musí byť\nŠIROKÝ" in sp, True)
check("priklad sektoroveho dotazu", '"AI chip stocks news' in sp, True)
check("povodne pravidlo o konkretnej entite zostalo", "TVOJ DOTAZ MUSÍ OBSAHOVAŤ konkrétne meno/entitu" in sp, True)
sys_text = " ".join(b["text"] for b in ca._system_prompt_blocks(NVDA))
check("pravidlo je aj v skladanom system prompte NVDA", "ŠIROKÝ - na sektor alebo trh" in sys_text, True)

print()
print("=" * 110)
print("6) Staticke: CycleLog kwargy existuju, oba plne volania dostanu Benzingu")
print("=" * 110)
cols = {c.name for c in db.CycleLog.__table__.columns}
check("stlpec cycle_logs.benzinga_news existuje", "benzinga_news" in cols, True)
tree = ast.parse(open(os.path.join(_ROOT, "trade_cycle.py"), encoding="utf-8").read())
unknown = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CycleLog":
        unknown += [f"{kw.arg}@{node.lineno}" for kw in node.keywords
                    if kw.arg is not None and kw.arg not in cols]
check("ziadny CycleLog(...) s neexistujucim stlpcom", unknown, [])
for fn in ("analyze", "analyze_position_health"):
    hits = [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == fn]
    kws = {kw.arg for h in hits for kw in h.keywords}
    check(f"claude_analyst.{fn}(...) dostava benzinga_news aj benzinga_since",
          (len(hits), {"benzinga_news", "benzinga_since"} <= kws), (1, True))

print()
print("=" * 110)
print("7) Integracne: run_cycle_for_asset (mock) - sken aj plny cyklus dostanu to iste")
print("=" * 110)
ADA = assets.ADA
SYM = ADA["strike_symbol"]
TA_ADA = {"last_price": 0.22, "atr14": 0.004, "rsi14": 55.0, "trend": "mild_uptrend",
          "adx14": 18.0, "recent_candles": [[0.21, 0.22, 0.21, 0.22, 100.0]] * 48,
          "book_imbalance": 0.3, "price_range": {"in_range": True, "at_edge": None,
                                                  "failed_conditions": [], "efficiency_ratio": 0.4}}
MARKET = {"mark_price": 0.22, "order_tick_price": 0.0001, "order_market_step_size": 1.0,
          "order_market_min_size": 1.0, "order_market_max_size": 1e9, "order_min_notional": 1.0,
          "bid1_price": 0.2199, "ask1_price": 0.2201, "bid1_size": 100, "ask1_size": 100,
          "index_price": 0.22, "margin_tiers": [{"max_notional": 1e9, "max_leverage": 10,
                                                  "maintenance_margin_rate": 0.01}]}
strike_client.get_market = lambda sym: MARKET
market_data.get_market_snapshot = lambda a, sess: dict(TA_ADA)
social_sentiment.fetch_recent_posts = lambda n: []
marketaux_client.get_news_sentiment = lambda q: []
seen = {}


def fake_triage(*a, **kw):
    seen["triage"] = kw.get("alpaca_news")
    return ({"worth_full_look": True, "attention": 70, "reason": "nova sprava"},
            {"input_tokens": 3000, "cache_write_tokens": 0, "cache_read_tokens": 0,
             "output_tokens": 200, "model": "m", "effort": "low"})


def fake_analyze(*a, **kw):
    seen["analyze"] = kw.get("benzinga_news")
    seen["since"] = kw.get("benzinga_since")
    return ({"direction": "none", "confidence": 40, "stop_loss_price": 0.21,
             "take_profit_price": 0.23, "reasoning": "plny cyklus", "key_assumptions": "k"},
            [], {"input_tokens": 9000, "cache_write_tokens": 0, "cache_read_tokens": 0,
                 "output_tokens": 800, "effort": "high"})


ca.triage, ca.analyze = fake_triage, fake_analyze
load([n("Cardano founder announces new governance era", 3.0, symbols=("ADAUSD",),
        content="<p>Governance story body.</p>"),
      n("Cardano ETF filing rumor", 0.5, symbols=("ADAUSD",), content="<p>ETF body.</p>")])
s = get_session()
s.add(CycleLog(symbol=SYM, outcome="rejected", usage_output_tokens=500,
               created_at=(now - timedelta(hours=2)).replace(tzinfo=None)))
s.add(CycleLog(symbol=SYM, outcome="triage_skip", usage_output_tokens=200,
               created_at=(now - timedelta(minutes=20)).replace(tzinfo=None)))
s.commit()
s.close()
config.TRIAGE_MODE = "shadow"
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
s = get_session()
log = s.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
s.close()
check("sken dostal Benzingu", [i["title"] for i in seen.get("triage") or []],
      ["Cardano ETF filing rumor", "Cardano founder announces new governance era"])
check("plny cyklus dostal TIE ISTE polozky", seen.get("analyze") is seen.get("triage"), True)
check("NOVE = po plnom cykle pred 2 h (sken pred 20 min sa nerata)",
      [i["new"] for i in seen.get("analyze") or []], [True, False])
check("since = cas posledneho PLNEHO pohladu",
      round((now - seen["since"]).total_seconds() / 3600, 1) if seen.get("since") else None, 2.0)
check("CycleLog.benzinga_news zapisany", (log.benzinga_news or {}).get("new_count"), 1)
check("  ...bez textov", any("text" in i for i in (log.benzinga_news or {}).get("items", [])), False)
check("triage.alpaca_news stale zapisany", (log.triage or {}).get("alpaca_news", {}).get("count"), 2)

config.ALPACA_NEWS_FULL_CYCLE = False
seen.clear()
an._pool_fetched_at = 0.0
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
check("ALPACA_NEWS_FULL_CYCLE=false: sken Benzingu ma", bool(seen.get("triage")), True)
check("  ...plny cyklus nie", seen.get("analyze"), None)
config.ALPACA_NEWS_FULL_CYCLE = True
config.TRIAGE_MODE = "off"

print()
print("=" * 110)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 110)
sys.exit(0 if ok else 1)
