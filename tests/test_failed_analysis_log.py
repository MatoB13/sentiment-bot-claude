"""Zaplatena, ale zahodena analyza sa musi dat spocitat aj diagnostikovat (2026-09-14).

POVOD: WTI 14.9. 16:17 UTC - Claude vratil rozhodnutie bez `direction` (ostatne
polia prisli, stop_reason=tool_use, 10155 vystupnych tokenov). Vetva
"Claude analyza zlyhala" zapisala len text chyby: usage NULL (~0.25 $ zmizlo z
nakladov), bez stop_reason, bez uvahy - preco smer chybal, sa zistit nedalo.
Rovnako health check (ValueError bez usage).

Mockovane - test nevola Clauda, siet ani burzu."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/failed_analysis.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import config  # noqa: E402
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
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {str(got)[:28]!r} (ocakavane {want!r})")


USAGE = {"input_tokens": 13, "cache_write_tokens": 24580, "cache_write_1h_tokens": 3172,
         "cache_read_tokens": 213740, "output_tokens": 10155, "effort": "high", "stop_reason": "tool_use"}
PARTIAL = {"confidence": 40, "stop_loss_price": 97.0, "take_profit_price": 101.0,
           "reasoning": "Saudi pipeline hit, but price back below 99.", "key_assumptions": "k",
           "watch_price": 100.5, "watch_direction": "above", "watch_rationale": "r"}
WS = [{"query": "Saudi pipeline attack September 14 2026", "sources": []}]

print("1) analyze() a analyze_position_health() balia zlu odpoved do MalformedDecision")
real_call = ca._call_claude
# 15.9.: chyba smer AJ istota - samotny chybajuci smer sa uz doplna na 'none' (sekcia 4)
BROKEN = {k: v for k, v in PARTIAL.items() if k != "confidence"}
ca._call_claude = lambda *a, **k: (dict(BROKEN), list(WS), dict(USAGE))
A = assets.WTI
TA = {"last_price": 98.4, "atr14": 0.79, "recent_candles": [], "trend": "mild_uptrend"}
try:
    ca.analyze(A, TA, {}, {}, [], None, None)
    check("analyze vyhodil vynimku", False, True)
except ca.MalformedDecision as e:
    check("analyze: chyba hovori o direction", "direction" in str(e), True)
    check("  nesie neuplne rozhodnutie (uvahu)", e.decision.get("reasoning", "")[:11], "Saudi pipel")
    check("  nesie usage", e.usage["output_tokens"], 10155)
    ex_analyze = e
op = {"direction": "Long", "entry_price": 98.0, "live_price": 98.4, "stop_loss_price": 97.0,
      "take_profit_price": 101.0, "leverage": 10, "opened_at_str": "x", "hours_held": 2.0,
      "unrealized_pnl_usd": 1.0, "unrealized_pnl_pct": 0.4}
try:
    ca.analyze_position_health(A, op, TA, {}, {}, [])
    check("health vyhodil vynimku", False, True)
except ca.MalformedDecision as e:
    check("health: MalformedDecision (predtym holy ValueError bez usage)", "recommendation" in str(e), True)
    check("  nesie usage", e.usage["cache_read_tokens"], 213740)
ca._call_claude = real_call

print("\n2) Polia pre CycleLog")
f = tc._failed_analysis_fields(ex_analyze)
check("usage do vsetkych stlpcov", (f["usage_input_tokens"], f["usage_cache_write_tokens"],
                                     f["usage_cache_write_1h_tokens"], f["usage_cache_read_tokens"],
                                     f["usage_output_tokens"]), (13, 24580, 3172, 213740, 10155))
check("effort", f["effort"], "high")
check("web_search_log", f["web_search_log"], WS)
check("uvaha s oznacenim ZAHODENE", f["reasoning"].startswith("[ZAHODENE"), True)
check("key_assumptions sa NEUKLADAJU", "key_assumptions" in f, False)
r = tc._failed_analysis_reason(ex_analyze)
check("dovod obsahuje stop_reason", "stop_reason=tool_use" in r, True)
check("dovod obsahuje prisle kluce", "prisle kluce=" in r and "'reasoning'" in r, True)
check("ina chyba (429) -> ziadne polia", tc._failed_analysis_fields(RuntimeError("429")), {})
check("ina chyba -> dovod bez doplnku", tc._failed_analysis_reason(RuntimeError("429")), "429")
check("health prefix", tc._failed_analysis_reason(RuntimeError("x"), "health_check_failed: "),
      "health_check_failed: x")

print("\n3) Integracne: run_cycle_for_asset s padnutou analyzou")
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
config.TRIAGE_MODE = "off"


def boom(*a, **k):
    raise ca.MalformedDecision("Chýbajúce polia v rozhodnutí: {'direction'}", usage=dict(USAGE),
                               web_search_log=list(WS), present_keys=sorted(PARTIAL), decision=dict(PARTIAL))


now = datetime.now(timezone.utc)
s = get_session()
s.add(CycleLog(symbol=SYM, outcome="rejected", usage_output_tokens=500, key_assumptions="stare predpoklady",
               created_at=(now - timedelta(hours=3)).replace(tzinfo=None)))
s.commit()
s.close()
ca.analyze = boom
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
s = get_session()
log = s.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
check("zapisany riadok s outcome=error", log.outcome, "error")
check("  usage_output_tokens ulozene", log.usage_output_tokens, 10155)
check("  cache_read ulozene", log.usage_cache_read_tokens, 213740)
check("  web_search_log ulozeny", (log.web_search_log or [{}])[0].get("query"), WS[0]["query"])
check("  uvaha ulozena", (log.reasoning or "").startswith("[ZAHODENE"), True)
check("  dovod so stop_reason", "stop_reason=tool_use" in (log.reject_reason or ""), True)
check("  key_assumptions prazdne", log.key_assumptions, None)
last_full = tc._last_full_look_at(SYM, s)
check("zahodena analyza sa NEPOCITA ako plny pohlad (stale pred 3 h)",
      round((now - last_full).total_seconds() / 3600) if last_full else None, 3)
prev = (s.query(CycleLog).filter(CycleLog.symbol == SYM, CycleLog.key_assumptions.isnot(None))
        .order_by(CycleLog.created_at.desc()).first())
check("dalsi cyklus prevezme STARE predpoklady, nie z neuplneho", prev.key_assumptions, "stare predpoklady")
check("ticker uz nie je due (ziadna platena slucka)", tc._is_due(ADA, s), False)
s.close()

print("\n4) 15.9. - chyba LEN smer (WTI 14.9., AAOI 15.9.): doplni sa 'none', nic sa nezahodi")
import importlib  # noqa: E402
importlib.reload(ca)                     # povodne analyze (sekcia 3 ho nahradila) - ten isty objekt modulu
real_call = ca._call_claude
AAOI_LIKE = {"confidence": 38, "stop_loss_price": 94.1, "take_profit_price": 101.2, "key_assumptions": "95.5 plytko",
             "reasoning": "Ani short (chase po poklese), ani long bez reclaimu EMA.", "watch_price": 99.5,
             "watch_direction": "above", "watch_price_2": 94.8, "watch_direction_2": "below",
             "watch_rationale": "reclaim EMA20 / pokracovanie breakdownu", "daily_reflection": "x"}
d = dict(AAOI_LIKE)
check("doplnene (True) a smer none", (ca._recover_missing_direction(d, "AAOI"), d["direction"]), (True, "none"))
check("  poznamka v data_issue", d["data_issue"], ca._DIRECTION_RECOVERED_NOTE)
d2 = dict(AAOI_LIKE, data_issue="stara cena")
ca._recover_missing_direction(d2, "AAOI")
check("  existujuci data_issue sa zachova a doplni", d2["data_issue"], "stara cena | " + ca._DIRECTION_RECOVERED_NOTE)
d3 = {k: v for k, v in AAOI_LIKE.items() if k != "reasoning"}
check("chyba smer AJ uvaha -> nedopina (zahodi sa ako doteraz)", (ca._recover_missing_direction(d3, "X"), "direction" in d3),
      (False, False))
d4 = dict(AAOI_LIKE, direction="short")
check("smer prisiel -> netkne sa ho", (ca._recover_missing_direction(d4, "X"), d4["direction"]), (False, "short"))
check("uplne prazdna (orezana) odpoved -> nedopina", ca._recover_missing_direction({}, "X"), False)
ca._call_claude = lambda *a, **k: (dict(AAOI_LIKE), list(WS), dict(USAGE))
dec, _ws, _u = ca.analyze(assets.AAOI, {"last_price": 97.4, "atr14": 1.2, "recent_candles": [], "trend": "strong_downtrend"},
                          {}, {}, [], None, None)
check("analyze(): bez vynimky, smer none, watch urovne ostali",
      (dec["direction"], dec.get("watch_price"), dec.get("watch_price_2")), ("none", 99.5, 94.8))
ca._call_claude = real_call

print("\n5) Integracne: cyklus s odpovedou bez smeru sa zapise ako bezny 'none'")
ca_fresh = ca
# urovne v mierke ADA (cena 0.22) - inak by bot "pod 94.8" spravne zahodil ako uz splnenu
ADA_LIKE = dict(AAOI_LIKE, stop_loss_price=0.21, take_profit_price=0.23, watch_price=0.25,
                watch_price_2=0.20)
ca._call_claude = lambda *a, **k: (dict(ADA_LIKE), list(WS), dict(USAGE))
s = get_session()
s.query(CycleLog).delete()
s.commit()
s.close()
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
ca._call_claude = real_call
s = get_session()
log = s.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
check("outcome nie je error", log.outcome != "error", True)
check("  smer none, watch urovne v DB (poller ich uvidi)",
      (log.direction, log.watch_price, log.watch_price_2), ("none", 0.25, 0.20))
check("  predpoklady ulozene", log.key_assumptions, "95.5 plytko")
check("  poznamka o doplneni v data_issue", ca_fresh._DIRECTION_RECOVERED_NOTE in (log.data_issue or ""), True)
s.close()

print("\n6) 15.9. - poskodene cisla (ADA #8480: stop_loss_price = \"0.196},\" pri 'none')")
ADA_8480 = dict(AAOI_LIKE, direction="none", confidence=28, stop_loss_price="0.196},", take_profit_price=0.23,
                watch_price=0.25, watch_price_2=0.20)
d = dict(ADA_8480)
fx = ca._normalize_decision_numbers(d, "ADA")
check("'none': cislo s chvostom JSON-u sa opravi na 0.196", d["stop_loss_price"], 0.196)
check("  oprava v data_issue", "stop_loss_price '0.196},' -> 0.196" in d["data_issue"], True)
d = dict(ADA_8480, stop_loss_price="neviem}")
ca._normalize_decision_numbers(d, "ADA")
check("'none': nepouzitelne SL -> prazdne, rozhodnutie ostava", (d["stop_loss_price"], d["direction"]), (None, "none"))
d = dict(ADA_8480, direction="long", stop_loss_price="neviem}")
try:
    ca._normalize_decision_numbers(d, "ADA")
    check("long s nepouzitelnym SL -> ValueError", False, True)
except ValueError as e:
    check("long s nepouzitelnym SL -> ValueError (cenu pre obchod nehadame)", "stop_loss_price" in str(e), True)
d = dict(ADA_8480, direction="long", stop_loss_price=" 0.21 ", confidence="61")
ca._normalize_decision_numbers(d, "ADA")
check("long: cisty cislovy text sa prevedie (SL 0.21, istota 61)", (d["stop_loss_price"], d["confidence"]), (0.21, 61))
d = dict(ADA_8480, confidence=None)
try:
    ca._normalize_decision_numbers(d, "ADA")
    check("istota None -> ValueError", False, True)
except ValueError:
    check("istota None -> ValueError (predtym TypeError = pad cyklu)", True, True)
d = dict(ADA_8480, watch_price="0.25\"}", watch_price_2="nizsie")
ca._normalize_decision_numbers(d, "ADA")
check("watch: opravitelna uroven ostane, nepouzitelna sa vynecha aj so smerom",
      (d["watch_price"], d["watch_direction"], d["watch_price_2"], d["watch_direction_2"]), (0.25, "above", None, None))
d = dict(ADA_8480, stop_loss_price=0.21)
check("REGRESIA ciste cisla: ziadna oprava, data_issue netknute",
      (ca._normalize_decision_numbers(d, "ADA"), d.get("data_issue")), ([], None))
check("bool nie je cislo", ca._as_number(True), None)
ca._call_claude = lambda *a, **k: (dict(ADA_8480, direction="long", stop_loss_price="neviem}"), list(WS), dict(USAGE))
try:
    ca.analyze(ADA, TA_ADA, {}, {}, [], None, None)
    check("analyze long s nepouzitelnym SL -> MalformedDecision", False, True)
except ca.MalformedDecision as e:
    check("analyze long s nepouzitelnym SL -> MalformedDecision (uvaha sa ulozi)", "stop_loss_price" in str(e), True)
ca._call_claude = real_call

print("\n7) Integracne: cyklus ADA #8480 sa zapise ako bezny 'none' s watch urovnami")
ca._call_claude = lambda *a, **k: (dict(ADA_8480), list(WS), dict(USAGE))
s = get_session()
s.query(CycleLog).delete()
s.commit()
s.close()
tc.run_cycle_for_asset(ADA, {}, {}, None, None, skip_due_check=True)
ca._call_claude = real_call
s = get_session()
log = s.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
check("outcome rejected (none), nie error", (log.outcome, log.direction, log.confidence), ("rejected", "none", 28))
check("  SL ulozene ako cislo 0.196 (Postgres by text odmietol)", (log.stop_loss_price, type(log.stop_loss_price).__name__),
      (0.196, "float"))
check("  watch urovne v DB", (log.watch_price, log.watch_price_2), (0.25, 0.20))
s.close()

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
