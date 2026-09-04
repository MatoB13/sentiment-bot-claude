"""Reprodukcia MINIMAX slucky z 3.9. + overenie oboch oprav.

Slucka: nespracovana retrospektiva je samostatny dovod na plne Claude volanie
v _run_position_health_check, ale NEPOSUVA last_health_escalation_at. Kym
Claude nevrati summary_reflection, podmienka o minutu plati znova -> minutovy
poller (position_monitor._fast_health_triggers) spusta plateny cyklus dookola.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/retroloop.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import config  # noqa: E402
import position_monitor  # noqa: E402
import trade_cycle  # noqa: E402
from db import DailyRetrospective, PriceBar, RollingRetrospective, Trade, get_session  # noqa: E402

now = datetime.now(timezone.utc)
yesterday = (now - timedelta(days=1)).date().isoformat()
# MINIMAX nemusi byt zapnuty v lokalnom ENV - vezmeme prvy aktivny ticker,
# logika je na tickeri nezavisla.
asset = assets.enabled_assets()[0]
SYM = asset["strike_symbol"]
s = get_session()

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r:>10} (ocakavane {want!r})")


print("=" * 100)
print("1) OPRAVA 1 - den sa oznaci za spracovany aj bez summary_reflection")
print("=" * 100)
stats = {"for_date": yesterday, "total_signals": 3}

# a) Claude vrati obe polia - povodne spravanie musi ostat
trade_cycle._save_pending_retrospective(
    asset["name"], SYM, stats,
    {"daily_reflection": "vcera to bolo choppy", "summary_reflection": "drz sa trendu"}, s)
r = s.query(RollingRetrospective).filter_by(symbol=SYM).first()
check("s summary_reflection: based_through_date sa posunul", r.based_through_date, yesterday)
check("s summary_reflection: text sa UZ NEUKLADA (bod 4, 4.9.)", r.summary, None)
check("s summary_reflection: denna retrospektiva ulozena",
      s.query(DailyRetrospective).filter_by(symbol=SYM).count(), 1)

# b) Claude summary_reflection VYNECHA (jadro incidentu) - na inom symbole
SYM2 = "ZEC-USD"
trade_cycle._save_pending_retrospective(
    "ZEC", SYM2, stats, {"daily_reflection": "len denna poznamka"}, s)
r2 = s.query(RollingRetrospective).filter_by(symbol=SYM2).first()
check("BEZ summary_reflection: den je aj tak oznaceny za spracovany",
      r2.based_through_date, yesterday)
check("BEZ summary_reflection: zhrnutie NEPREPISANE na None", r2.summary, None)

# c) a preto uz _get_retrospective_context nevrati new_stats_text znova
new_stats, pending = trade_cycle._get_retrospective_context(
    next(a for a in assets.enabled_assets() if a["strike_symbol"] == SYM2), s)
check("po prvom pokuse uz new_stats_text nie je dovod na dalsie volanie",
      new_stats, None)
check("...a pending_stats tiez nie", pending, None)

print()
print("=" * 100)
print("2) STARE SPRAVANIE - bez opravy by sa podmienka opakovala donekonecna")
print("=" * 100)
SYM3 = "NEAR-USD"
a3 = next(a for a in assets.enabled_assets() if a["strike_symbol"] == SYM3)
# simulacia povodneho kodu: uloz len ked summary_reflection existuje
decision_bez = {"daily_reflection": "poznamka"}
if decision_bez.get("summary_reflection"):
    trade_cycle._upsert_rolling(s, SYM3, decision_bez["summary_reflection"], yesterday)
    s.commit()
r3 = s.query(RollingRetrospective).filter_by(symbol=SYM3).first()
check("stary kod: RollingRetrospective vobec nevznikol", r3, None)
print("       -> _get_retrospective_context by vratil new_stats_text ZNOVA pri kazdom")
print("          dalsom cykle, cize plateny health check kazdu minutu. Presne to sa stalo.")

print()
print("=" * 100)
print("3) MINUTOVY HEALTH POLLER JE PREC (4.9., bod 5c) - zvysok pollera zostava")
print("=" * 100)
import inspect  # noqa: E402
for fn in (trade_cycle._run_position_health_check, trade_cycle.run_cycle_for_asset,
           trade_cycle.run_triggered_check):
    check(f"{fn.__name__} uz NEMA parameter fast_poll",
          "fast_poll" in inspect.signature(fn).parameters, False)
check("position_monitor nema _fast_health_triggers", hasattr(position_monitor, "_fast_health_triggers"), False)
check("position_monitor nema _fire_fast_health_checks", hasattr(position_monitor, "_fire_fast_health_checks"), False)
check("reheal noh ZOSTAVA", hasattr(position_monitor, "_check_and_reheal_bracket_legs"), True)
check("dust sweep ZOSTAVA", hasattr(position_monitor, "_maybe_sweep_dust_position"), True)
check("config bez FAST_POLL/SL_PROXIMITY_MIN_INTERVAL",
      hasattr(config, "HEALTH_CHECK_FAST_POLL_MIN_INTERVAL_MINUTES") or hasattr(config, "HEALTH_CHECK_SL_PROXIMITY_MIN_INTERVAL_MINUTES"), False)
check("_trigger_source bez fast_health", trade_cycle._trigger_source(None, False, None), "scheduled")
check("Trade model bez last_fast_health_at", hasattr(Trade, "last_fast_health_at"), False)

s.close()
print()
print("=" * 100)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 100)
sys.exit(0 if ok else 1)
