"""Testy troch oprav po ADA incidente (#177 -> #186, 4.9.2026)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/adafix.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import trade_cycle as tc  # noqa: E402
from db import CycleLog, PriceBar, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r:>8} (ocakavane {want!r})")


A = assets.enabled_assets()[0]
SYM = A["strike_symbol"]
TA = {"last_price": 0.22507, "atr14": 0.002}


def build(**kw):
    return ca._build_user_prompt(A, TA, {}, {"session": "US"}, [], None, None, **kw)


print("=" * 100)
print("1) WATCH BLOK - obe urovne a ktora padla (jadro ADA chyby)")
print("=" * 100)
# presne ADA 02:18: above 0.22503 (padla) + below 0.217, rationale popisuje 0.217
wsc = {
    "created_at": datetime.now(timezone.utc) - timedelta(minutes=37),
    "live_price": 0.22245, "direction": "none", "confidence": 30,
    "watch_price": 0.22503, "watch_direction": "above",
    "watch_price_2": 0.217, "watch_direction_2": "below",
    "watch_rationale": "0.217 je EMA20 - jeho prerazenie nadol by podporilo short/fade od vrcholu.",
}
p = build(watch_set_context=wsc)
check("obe urovne su v prompte", "0.22503" in p and "0.217" in p, True)
check("oznaci, ktora padla", "TÁTO ÚROVEŇ PADLA" in p, True)
check("varuje, ze rationale je spolocne pre obe",
      "spoločné pre obe úrovne" in p, True)
check("varuje pred postavenim sa proti prielomu",
      "ísť PROTI smeru" in p, True)
i = p.index("## Toto rozhodnutie bolo vyvolané")
print("\n  --- ukazka ---")
for ln in p[i:i + 420].splitlines()[:9]:
    print("   ", ln)

print()
print("=" * 100)
print("2) KONFRONTACIA PRI OTOCENI SMERU")
print("=" * 100)
rc = {"trade_id": 177, "direction": "long", "entry_price": 0.21565,
      "exit_price": 0.2226, "hours_ago": 0.65,
      "close_reason": "ai_early_close", "pnl_usd": 107.25}
p2 = build(recent_close=rc)
check("blok je v prompte", "zatvoril pozíciu" in p2, True)
check("pomenuje opacny smer (short)", "**short**" in p2, True)
check("varuje pred horsim vstupom nez vystup", "HORŠÍ ako cena" in p2, True)
check("bez recent_close sa blok nevlozi", "zatvoril pozíciu" in build(), False)
rc_short = {**rc, "direction": "short"}
check("pri shorte pyta na long", "**long**" in build(recent_close=rc_short), True)

print()
print("=" * 100)
print("3) ODLOZENY VERDIKT O ZATVORENI")
print("=" * 100)
cv = {"trade_id": 177, "direction": "long", "entry_price": 0.21565,
      "exit_price": 0.2226, "close_reason": "ai_early_close", "pnl_usd": 107.25,
      "hours_ago": 5.2, "best_since": 0.22679, "worst_since": 0.2217,
      "missed_pct": 1.88, "price_now": 0.2235,
      "stop_loss_price": 0.20797, "take_profit_price": 0.22611}
p3 = build(close_verdict=cv)
check("blok je v prompte", "Spätné zhodnotenie staršieho zatvorenia" in p3, True)
check("obsahuje, co cena spravila po vystupe", "1.88%" in p3, True)
check("hovori, ze reflexia spred par minut sa nema drzat",
      "pár minút po zatvorení to vedieť nemôže" in p3, True)
check("bez close_verdict sa blok nevlozi",
      "Spätné zhodnotenie staršieho" in build(), False)
op = {"direction": "Long", "entry_price": 0.2, "live_price": 0.21,
      "stop_loss_price": 0.19, "take_profit_price": 0.23, "leverage": 3,
      "opened_at_str": "x", "hours_held": 1.0,
      "unrealized_pnl_usd": 1.0, "unrealized_pnl_pct": 1.0}
check("blok je aj vo vetve s otvorenou poziciou",
      "Spätné zhodnotenie staršieho" in build(close_verdict=cv, open_position=op), True)

print()
print("=" * 100)
print("4) OKAMZITA POST-CLOSE REFLEXIA UZ NEHODNOTI CASOVANIE")
print("=" * 100)
sch = str(ca.DECISION_TOOL) + str(ca.POSITION_HEALTH_TOOL)
whole = build() + sch
src = open(os.path.join(_ROOT, "claude_analyst.py"),
           encoding="utf-8").read()
check("stara otazka na casovanie je prec",
      "zatvorenie bolo predčasné/zbytočne opatrné?" in src, False)
check("nova instrukcia zakazuje verdikt o casovani",
      "NETVRĎ, či bolo zatvorenie dobre načasované" in src, True)

print()
print("=" * 100)
print("5) _pending_close_verdict - vyber obchodu")
print("=" * 100)
s = get_session()
now = datetime.now(timezone.utc)
naive = now.replace(tzinfo=None)


def mk_trade(hours_ago, tid):
    t = Trade(id=tid, symbol=SYM, status="closed_by_ai", direction="Long",
              entry_price=0.2, entry_fill_price=0.2, close_fill_price=0.21,
              opened_at=naive - timedelta(hours=hours_ago + 2),
              closed_at=naive - timedelta(hours=hours_ago),
              stop_loss_price=0.19, take_profit_price=0.23,
              pnl_usd=1.0, margin_usd=100.0)
    s.add(t)
    return t


for h in range(0, 8):
    s.add(PriceBar(symbol=SYM, hour_start=naive - timedelta(hours=h),
                   open=0.21, high=0.215, low=0.205, close=0.212))
mk_trade(1, 901)      # prilis cerstvy
s.commit()
check("obchod zatvoreny pred 1h sa NEBERIE",
      tc._pending_close_verdict(SYM, s, now), None)

s.query(Trade).delete()
mk_trade(5, 902)
s.commit()
r = tc._pending_close_verdict(SYM, s, now)
check("obchod zatvoreny pred 5h sa berie", r is not None and r["trade_id"], 902)

s.add(CycleLog(symbol=SYM, reviewed_trade_id=902,
               closed_trade_reflection="verdikt",
               created_at=naive - timedelta(hours=0.5)))
s.commit()
check("po zapisani verdiktu sa uz neponuka",
      tc._pending_close_verdict(SYM, s, now), None)

s.query(Trade).delete()
mk_trade(50, 903)
s.commit()
check("prilis stary obchod (50h) sa uz neberie",
      tc._pending_close_verdict(SYM, s, now), None)
s.close()

print()
print("=" * 100)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 100)
sys.exit(0 if ok else 1)
