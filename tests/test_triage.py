"""Testy dvojfazoveho cyklu (bod 6): prompt skenu, gating, rezimy off/shadow/active."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/triage.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import config  # noqa: E402
import market_data  # noqa: E402
import marketaux_client  # noqa: E402
import social_sentiment  # noqa: E402
import strike_client  # noqa: E402
import trade_cycle as tc  # noqa: E402
from db import CycleLog, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<70} {str(got)[:20]:>20} (ocakavane {want!r})")


A = assets.ADA
SYM = A["strike_symbol"]
TA = {"last_price": 0.22, "atr14": 0.004, "rsi14": 55.0, "trend": "mild_uptrend",
      "adx14": 18.0, "recent_candles": [[0.21, 0.22, 0.21, 0.22, 100.0]] * 48,
      "book_imbalance": 0.3, "price_range": {"in_range": True, "at_edge": None,
                                              "failed_conditions": [], "efficiency_ratio": 0.4}}
CM = {"vix": {"last": 14.2, "change_24h_pct": -7.8}}
SESS = {"nikkei_asia": {"last": 39000, "change_24h_pct": 0.5}}
NEWS = [{"title": "Cardano governance vote passes", "age_hours": 5.0, "sentiment_score": 0.3,
         "snippet": "DLHY SNIPPET " * 40, "source": "x"}]

print("=" * 108)
print("1) PROMPT SKENU - co obsahuje a co NIE")
print("=" * 108)
p = ca._build_triage_prompt(A, TA, CM, SESS, {"last": 80000}, "predpoklad z minula",
                            datetime(2026, 9, 4, 10, tzinfo=timezone.utc), NEWS,
                            hours_since_full=2.5,
                            active_watch={"watch_price": 0.23, "watch_direction": "above",
                                          "watch_price_2": None, "watch_direction_2": None},
                            schedule={"next_run": datetime.now(timezone.utc) + timedelta(minutes=90),
                                      "interval_hours": 2})
check("obsahuje TA", '"atr14"' in p, True)
check("obsahuje cas od posledneho plneho pohladu", "pred 2.5 h" in p, True)
check("obsahuje titulok spravy", "Cardano governance vote passes" in p, True)
check("NEOBSAHUJE snippet clanku (setri tokeny)", "DLHY SNIPPET" in p, False)
check("obsahuje aktivnu watch uroven", "above 0.23" in p, True)
check("obsahuje predpoklady z minula", "predpoklad z minula" in p, True)
check("obsahuje dalsi planovany cyklus", "Dalsi planovany cyklus" in p, True)
check("NEOBSAHUJE makro pravidla", "BTC beta" in p, False)
check("NEOBSAHUJE blok faktov o vykonnosti", "doterajšia výkonnosť" in p, False)
check("NEOBSAHUJE mikrostrukturu (filtrovana)", "book_imbalance" in p, False)
check("NEOBSAHUJE diagnostiku pasma", "failed_conditions" in p, False)
sys_tok = len(ca.TRIAGE_SYSTEM_PROMPT) / 3.3
usr_tok = len(p) / 3.3
print(f"       velkost: system ~{sys_tok:.0f} tok, user ~{usr_tok:.0f} tok, spolu ~{sys_tok+usr_tok:.0f}")
check("spolu pod 5000 tokenov", (sys_tok + usr_tok) < 5000, True)
check("system prompt hovori, ze spravy nevidi", "spravy NEVIDIS" in ca.TRIAGE_SYSTEM_PROMPT, True)
check("system prompt neuvadza ziadny prah na obchod", "prah" in ca.TRIAGE_SYSTEM_PROMPT.lower(), False)

print()
print("=" * 108)
print("2) _hours_since_full_cycle - riadky skenu sa NERATAJU")
print("=" * 108)
s = get_session()
now = datetime.now(timezone.utc)
check("bez cyklov -> None", tc._hours_since_full_cycle(SYM, s, now), None)
s.add(CycleLog(symbol=SYM, outcome="rejected", usage_output_tokens=500,
               created_at=(now - timedelta(hours=3)).replace(tzinfo=None)))
s.commit()
h = tc._hours_since_full_cycle(SYM, s, now)
check("plny cyklus pred 3 h", round(h), 3)
s.add(CycleLog(symbol=SYM, outcome="triage_skip", usage_output_tokens=300,
               created_at=(now - timedelta(minutes=5)).replace(tzinfo=None)))
s.commit()
h = tc._hours_since_full_cycle(SYM, s, now)
check("sken pred 5 min NEPOSUNUL cas (stale 3 h)", round(h), 3)
s.add(CycleLog(symbol=SYM, outcome="position_check", usage_output_tokens=0,
               created_at=(now - timedelta(minutes=2)).replace(tzinfo=None)))
s.commit()
check("mechanicky (neplateny) cyklus tiez NEPOSUNUL", round(tc._hours_since_full_cycle(SYM, s, now)), 3)

print()
print("=" * 108)
print("3) _active_watch_context")
print("=" * 108)
check("najnovsi riadok bez watchu -> None", tc._active_watch_context(SYM, s), None)
s.add(CycleLog(symbol=SYM, outcome="rejected", watch_price=0.25, watch_direction="above",
               created_at=now.replace(tzinfo=None)))
s.commit()
w = tc._active_watch_context(SYM, s)
check("watch z najnovsieho riadku", (w["watch_price"], w["watch_direction"]), (0.25, "above"))
s.close()

print()
print("=" * 108)
print("4) REZIMY - integracny beh run_cycle_for_asset (mockovane volania)")
print("=" * 108)
calls = {"triage": 0, "analyze": 0}
MARKET = {"mark_price": 0.22, "order_tick_price": 0.0001, "order_market_step_size": 1.0,
          "order_market_min_size": 1.0, "order_market_max_size": 1e9, "order_min_notional": 1.0,
          "bid1_price": 0.2199, "ask1_price": 0.2201, "bid1_size": 100, "ask1_size": 100,
          "index_price": 0.22, "margin_tiers": [{"max_notional": 1e9, "max_leverage": 10,
                                                  "maintenance_margin_rate": 0.01}]}
strike_client.get_market = lambda sym: MARKET
market_data.get_market_snapshot = lambda a, sess: dict(TA)
social_sentiment.fetch_recent_posts = lambda n: []
marketaux_client.get_news_sentiment = lambda q: NEWS


def fake_triage(worth):
    def _t(*a, **kw):
        calls["triage"] += 1
        return ({"worth_full_look": worth, "attention": 20 if not worth else 80,
                 "reason": "test", "watch_price": 0.24, "watch_direction": "above",
                 "watch_rationale": "nad 0.24 by to bolo ine"},
                {"input_tokens": 3000, "cache_write_tokens": 0, "cache_read_tokens": 0,
                 "output_tokens": 200, "model": "m", "effort": "low"})
    return _t


def fake_analyze(*a, **kw):
    calls["analyze"] += 1
    return ({"direction": "none", "confidence": 40, "stop_loss_price": 0.21,
             "take_profit_price": 0.23, "reasoning": "plny cyklus", "key_assumptions": "k"},
            [], {"input_tokens": 9000, "cache_write_tokens": 0, "cache_read_tokens": 0,
                 "output_tokens": 800, "effort": "high"})


ca.analyze = fake_analyze


def run(mode, worth=True):
    config.TRIAGE_MODE = mode
    ca.triage = fake_triage(worth)
    calls["triage"] = calls["analyze"] = 0
    sess = get_session()
    sess.query(CycleLog).delete()
    sess.commit()
    sess.close()
    tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True)
    sess = get_session()
    log = sess.query(CycleLog).order_by(CycleLog.created_at.desc()).first()
    sess.close()
    return log


log = run("off")
check("off: sken NEbezal", calls["triage"], 0)
check("off: plny cyklus bezal", calls["analyze"], 1)
check("off: triage v DB je None", log.triage, None)

log = run("shadow", worth=False)
check("shadow: sken bezal", calls["triage"], 1)
check("shadow: plny cyklus bezal AJ TAK", calls["analyze"], 1)
check("shadow: verdikt ulozeny na riadku plneho cyklu", log.triage["worth_full_look"], False)
check("shadow: outcome je normalny", log.outcome, "rejected")
check("shadow: usage je z PLNEHO cyklu", log.usage_output_tokens, 800)
check("shadow: verdikt nesie aj usage skenu", log.triage["usage"]["output_tokens"], 200)
check("shadow: watch je z PLNEHO cyklu (nie zo skenu)", log.watch_price, None)

log = run("active", worth=False)
check("active + nie: sken bezal", calls["triage"], 1)
check("active + nie: plny cyklus NEBEZAL", calls["analyze"], 0)
check("active + nie: outcome=triage_skip", log.outcome, "triage_skip")
check("active + nie: watch ZO SKENU je v DB (poller ho uvidi)", log.watch_price, 0.24)
check("active + nie: usage je zo skenu", log.usage_output_tokens, 200)
check("active + nie: direction none", log.direction, "none")

log = run("active", worth=True)
check("active + ano: plny cyklus bezal", calls["analyze"], 1)
check("active + ano: outcome normalny", log.outcome, "rejected")

print()
print("=" * 108)
print("5) GATING - kedy sa sken NEPYTA")
print("=" * 108)
config.TRIAGE_MODE = "active"
ca.triage = fake_triage(False)

calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True, watch_triggered=True)
check("watch trigger: sken sa nepyta", calls["triage"], 0)
check("watch trigger: plny cyklus bezal", calls["analyze"], 1)

calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True, macro_event="CPI")
check("makro: sken sa nepyta", calls["triage"], 0)

calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True,
                        closed_trade={"trade_id": 1, "direction": "Long", "entry_price": 0.2,
                                      "exit_price": 0.21, "hours_held": 2.0, "pnl_usd": 5.0,
                                      "close_reason": "take_profit", "evaluation_only": False,
                                      "hours_since_close": 0.1, "closed_at_str": "x"})
check("post-close: sken sa nepyta", calls["triage"], 0)

# vynuteny plny cyklus po TRIAGE_FORCE_FULL_HOURS
sess = get_session()
sess.query(CycleLog).delete()
sess.add(CycleLog(symbol=SYM, outcome="rejected", usage_output_tokens=500,
                  created_at=(datetime.now(timezone.utc)
                              - timedelta(hours=config.TRIAGE_FORCE_FULL_HOURS + 1)).replace(tzinfo=None)))
sess.commit()
sess.close()
calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True)
check(f"posledny plny pred >{config.TRIAGE_FORCE_FULL_HOURS} h: sken sa nepyta", calls["triage"], 0)
check("...a plny cyklus bezal", calls["analyze"], 1)

# otvorena pozicia -> health check, sken sa netyka
sess = get_session()
sess.query(CycleLog).delete()
sess.add(Trade(symbol=SYM, status="open", direction="Long", entry_price=0.22,
               stop_loss_price=0.21, take_profit_price=0.23, leverage=5, size=100,
               notional_usd=500, margin_usd=100,
               opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
               expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).replace(tzinfo=None)))
sess.commit()
sess.close()
calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True)
check("otvorena pozicia: sken sa nepyta (bezi health check)", calls["triage"], 0)

# zlyhanie skenu nesmie zhodit cyklus
sess = get_session()
sess.query(Trade).delete()
sess.query(CycleLog).delete()
sess.commit()
sess.close()


def boom(*a, **kw):
    calls["triage"] += 1
    raise RuntimeError("API down")


ca.triage = boom
calls["triage"] = calls["analyze"] = 0
tc.run_cycle_for_asset(A, CM, SESS, None, None, skip_due_check=True)
check("zlyhanie skenu -> plny cyklus aj tak bezal", calls["analyze"], 1)

config.TRIAGE_MODE = "off"
print()
print("=" * 108)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 108)
sys.exit(0 if ok else 1)
