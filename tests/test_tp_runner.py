"""PREDLZOVANY TP (tp_runner.py) + REGRESIA klasickych obchodov (2026-09-12).

Pouzivatel: "sprav naozaj dobre regresne testy - ze nic ostatne sa nam
nepokazi, ani ze novy pristup nebude v konflikte so standardnymi (chop) obchodmi"
a "bot obcas straca TP a SL prikazy - taka strata by pri silnom trende mohla
velmi boliet".

BURZA JE PODVRHNUTA - kazde volanie strike_client ide do zoznamu `calls`,
nic nejde na siet (a strike_client._request ma navyse vlastny zamok)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/tprunner.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config  # noqa: E402
import discord_client  # noqa: E402
import position_monitor as pm  # noqa: E402
import strike_client  # noqa: E402
import tp_runner  # noqa: E402
import trade_cycle  # noqa: E402
from db import Trade, TpRunnerEvent, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {got!r} (ocakavane {want!r})")


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---------------- podvrhnuta burza ----------------
calls = []
state = {"positions": [], "markets": [], "open_orders": [], "fail_stop": 0}


def rec(name):
    def f(*a, **k):
        calls.append((name, a, k))
        if name == "place_stop_order" and state["fail_stop"] > 0:
            state["fail_stop"] -= 1
            raise RuntimeError("Strike odmietol SL")
        return {}
    return f


for n in ("cancel_all_orders", "place_stop_order", "place_take_profit_order", "close_position_market"):
    setattr(strike_client, n, rec(n))
strike_client.get_positions = lambda: state["positions"]
strike_client.get_markets = lambda: state["markets"]
strike_client.get_open_orders = lambda symbol=None: state["open_orders"]
strike_client.open_bracket_position = lambda **k: (_ for _ in ()).throw(AssertionError("test nesmie otvarat"))
discord_msgs = []
discord_client.notify_tp_runner_locked = lambda *a, **k: discord_msgs.append(("locked", a)) or True
discord_client.notify_bracket_leg_restored = lambda sym, leg, price: discord_msgs.append(("repair", leg, price))

# nesuvisiace self-heal/notifikacne veci monitora vypnute (chodili by na siet)
for name in ("_backfill_missing_exact_data", "_backfill_stale_reviews", "_backfill_missing_open_notifications",
             "_backfill_missing_close_notifications", "_backfill_missing_entry_fill_price", "_fire_due_recomputes",
             "_fire_post_close_reviews", "_fire_close_notifications", "_fire_open_notifications", "_fire_recomputes"):
    setattr(pm, name, lambda *a, **k: None)
pm._check_and_queue_review = lambda t, s_, p: p.append(t.id)
pm._check_and_queue_close_notification = lambda t, p: p.append(t.id)
pm._check_and_queue_recompute = lambda t: None
pm.watch_monitor.mark_hot = lambda sym: None
exact = {"reason": None}
pm._lookup_exact_close = lambda t: ({"close_reason": exact["reason"], "entry_fill_price": t.entry_price,
                                      "close_fill_price": 0.0, "fees_usd": 0.0, "pnl_usd": 1.0}
                                     if exact["reason"] else None)

config.TP_RUNNER_MAX_HOURS, config.TP_RUNNER_LOCK_ATR, config.TP_RUNNER_LOCK_MAX_FRACTION = 48, 1.0, 0.5
config.TP_RUNNER_TRAIL_ATR, config.TP_RUNNER_TRAIL_MAX_FRACTION, config.TP_RUNNER_MIN_STEP_ATR = 2.0, 1.0, 0.25
config.TP_RUNNER_EXCHANGE_TP_MULT, config.POSITION_MAX_HOURS = 10.0, 24

s = get_session()
now = datetime.now(timezone.utc)
NOWN = now.replace(tzinfo=None)


def mk(tid, sym, direction="Long", mode="runner", hours_ago=2, entry=100.0, sl=97.0, tp=104.0, atr=1.0, **kw):
    t = Trade(id=tid, symbol=sym, direction=direction, status="open", entry_price=entry,
              stop_loss_price=sl, take_profit_price=tp, size=10.0, notional_usd=1000, margin_usd=100,
              leverage=10, opened_at=NOWN - timedelta(hours=hours_ago),
              expires_at=NOWN - timedelta(hours=hours_ago) + timedelta(hours=24),
              tp_mode=mode, entry_atr=atr,
              tp_exchange_price=(tp_runner.exchange_tp_price(direction, entry, tp, 0.01) if mode else None),
              active_stop_price=(sl if mode else None), **kw)
    s.add(t); s.commit()
    return t


def market(sym, price):
    return {"symbol": sym, "mark_price": str(price), "order_tick_price": "0.01"}


def names():
    return [c[0] for c in calls]


print("1) Ciste vypocty")
check("havarijny TP long = 10x dalej (104 -> 140)", tp_runner.exchange_tp_price("Long", 100, 104, 0.01), 140.0)
check("havarijny TP short = 10x dalej (96 -> 60)", tp_runner.exchange_tp_price("Short", 100, 96, 0.01), 60.0)
check("havarijny TP short nikdy pod 10 % ceny", tp_runner.exchange_tp_price("Short", 100, 80, 0.01), 10.0)
lock, trail = tp_runner.lock_and_trail("Long", 100, 104, 1.0)
check("bezny ATR: zamok TP-1 ATR = 103, trailing 2 ATR", (lock, trail), (103.0, 2.0))
lock, trail = tp_runner.lock_and_trail("Long", 100, 104, 10.0)
check("CRASH (ATR 10 > vzdialenost 4): zamok min. polovica TP = 102", lock, 102.0)
check("  trailing max. 1 vzdialenost TP = 4", trail, 4.0)
check("  zamok je VZDY nad vstupom (nikdy strata z vyhry)", lock > 100, True)
lock, _ = tp_runner.lock_and_trail("Short", 100, 96, 10.0)
check("short v crashi: zamok 98 (pod vstupom = zisk)", lock, 98.0)

print("\n2) REGRESIA: klasicky obchod (tp_mode None) - predlzovanie sa ho NETYKA")
t1 = mk(1, "AAA-USD", mode=None)
calls.clear()
r = tp_runner.manage(t1, {"size": 10}, 110.0, 0.01, s, now)
check("manage nic nerobi ani nad TP", (r, names()), ({"orders_changed": False, "closed": False}, []))
check("expiracia ostava 24 h", t1.expires_at, t1.opened_at + timedelta(hours=24))

print("\n3) CHOP: runner pod TP - vsetko ako doteraz")
t2 = mk(2, "BBB-USD")
calls.clear()
for px in (99.0, 101.5, 103.9):
    tp_runner.manage(t2, {"size": 10}, px, 0.01, s, now)
check("ziadny zasah do objednavok", names(), [])
check("nic nezamknute", t2.tp_locked_at, None)
check("SL ostava povodny", t2.active_stop_price, 97.0)
check("expiracia ostava 24 h (chop sa nepredlzuje)", t2.expires_at, t2.opened_at + timedelta(hours=24))

print("\n4) Zasah TP -> zamknutie zisku (pozicia sa NEZATVORI)")
calls.clear()
r = tp_runner.manage(t2, {"size": -10}, 104.2, 0.01, s, now); s.commit()
check("poradie: zrus vsetko, novy SL, havarijny TP", names(),
      ["cancel_all_orders", "place_stop_order", "place_take_profit_order"])
check("novy SL 103 (TP - 1 ATR), absolutna velkost 10", calls[1][1][2:], (10.0, 103.0))
check("havarijny TP ostal na burze (140)", calls[2][1][3], 140.0)
check("pozicia sa NEzatvorila", "close_position_market" in names(), False)
check("zamknute + expiracia 48 h", (t2.tp_locked_at is not None, t2.expires_at), (True, t2.opened_at + timedelta(hours=48)))
check("active_stop_price = 103", t2.active_stop_price, 103.0)
check("oprava noh v tomto tiku preskocena", r["orders_changed"], True)
check("Discord: sprava o predlzeni", discord_msgs[-1][0], "locked")
check("dennik: udalost lock", [e.kind for e in s.query(TpRunnerEvent).filter_by(trade_id=2)], ["lock"])

print("\n5) Posuvanie SL za cenou")
calls.clear()
tp_runner.manage(t2, {"size": 10}, 104.9, 0.01, s, now)
check("maly posun (0.9) = SL sa na burze NEmeni", names(), [])
tp_runner.manage(t2, {"size": 10}, 105.4, 0.01, s, now); s.commit()
check("najlepsia 105.4 -> SL 103.4 (+0.4 >= krok 0.25)", t2.active_stop_price, 103.4)
tp_runner.manage(t2, {"size": 10}, 104.0, 0.01, s, now)
check("pri spatnom pohybe SL NIKDY neklesa", t2.active_stop_price, 103.4)
tp_runner.manage(t2, {"size": 10}, 110.0, 0.01, s, now); s.commit()
check("silny trend 110 -> SL 108", t2.active_stop_price, 108.0)
check("dennik: lock + 2x trail", [e.kind for e in s.query(TpRunnerEvent).filter_by(trade_id=2)], ["lock", "trail", "trail"])

print("\n6) Short v predlzovanom rezime")
t3 = mk(3, "CCC-USD", direction="Short", entry=100, sl=103, tp=96, atr=1.0)
calls.clear()
tp_runner.manage(t3, {"size": -5}, 95.8, 0.01, s, now); s.commit()
check("short zamok 97 (TP + 1 ATR)", t3.active_stop_price, 97.0)
tp_runner.manage(t3, {"size": -5}, 90.0, 0.01, s, now); s.commit()
check("short trend 90 -> SL 92", t3.active_stop_price, 92.0)
check("velkost vzdy kladna (NIGHT incident)", all(c[1][2] > 0 for c in calls if c[0] == "place_stop_order"), True)

print("\n7) Burza SL odmietne 2x -> pozicia NEOSTANE bez ochrany")
t4 = mk(4, "DDD-USD")
state["fail_stop"] = 2
calls.clear()
r = tp_runner.manage(t4, {"size": 10}, 104.5, 0.01, s, now); s.commit()
check("nudzove trhove zatvorenie (sme nad TP = zisk)", "close_position_market" in names(), True)
check("stav + dovod", (t4.status, t4.close_reason), ("closed_by_timeout", tp_runner.REASON_EMERGENCY))
check("manage hlasi zatvorenie", r["closed"], True)
state["fail_stop"] = 1
t5 = mk(5, "EEE-USD")
tp_runner.manage(t5, {"size": 10}, 104.5, 0.01, s, now); s.commit()
check("jedno zlyhanie -> druhy pokus uspel, pozicia bezi", (t5.status, t5.active_stop_price), ("open", 103.0))

print("\n8) Dovod zatvorenia - nebol to klasicky TP/SL")
check("zamknuty + SL fill -> tp_runner_stop", tp_runner.runner_close_reason(t2, "stop_loss"), "tp_runner_stop")
check("zamknuty + havarijny TP -> tp_runner_far_tp", tp_runner.runner_close_reason(t2, "take_profit"), "tp_runner_far_tp")
check("zamknuty + nas timeout -> tp_runner_timeout", tp_runner.runner_close_reason(t2, "force_closed_by_bot"), "tp_runner_timeout")
check("REGRESIA: nezamknuty SL ostava stop_loss", tp_runner.runner_close_reason(t1, "stop_loss"), "stop_loss")
check("REGRESIA: nezamknuty timeout ostava", tp_runner.runner_close_reason(t1, "force_closed_by_bot"), "force_closed_by_bot")
check("predlzeny vystup spusta review aj notifikaciu", ("tp_runner_stop" in pm._TRIGGER_REVIEW_REASONS,
      "tp_runner_stop" in pm._NOTIFY_CLOSE_REASONS), (True, True))
check("REGRESIA: povodne dovody v setoch ostali", {"take_profit", "stop_loss", "liquidation", "force_closed_by_bot",
      "ai_early_close"} <= pm._NOTIFY_CLOSE_REASONS, True)
check("predlzeny vystup NIE JE 'len vyhodnotenie'", "tp_runner_stop" in pm._EVALUATION_ONLY_CLOSE_REASONS, False)

print("\n9) OPRAVA STRATENYCH NOH (kriticke pri silnom trende)")
calls.clear(); discord_msgs.clear()
state["open_orders"] = []          # burza stratila obe nohy
pm._check_and_reheal_bracket_legs(t2, {"size": 10})
sl_c = [c for c in calls if c[0] == "place_stop_order"]; tp_c = [c for c in calls if c[0] == "place_take_profit_order"]
check("zamknuty runner: obnovi ZAMKNUTY SL 108, nie povodny 97", sl_c[0][1][3], 108.0)
check("zamknuty runner: obnovi HAVARIJNY TP 140, nie skutocny 104", tp_c[0][1][3], 140.0)
t6 = mk(6, "FFF-USD")
calls.clear()
pm._check_and_reheal_bracket_legs(t6, {"size": 10})
check("nezamknuty runner: SL povodny 97", [c for c in calls if c[0] == "place_stop_order"][0][1][3], 97.0)
check("nezamknuty runner: TP havarijny 140 (nie 104 - inak by predlzovanie zrusil)",
      [c for c in calls if c[0] == "place_take_profit_order"][0][1][3], 140.0)
calls.clear()
pm._check_and_reheal_bracket_legs(t1, {"size": 10})
check("REGRESIA klasicky: SL 97 a TP 104 ako doteraz",
      ([c[1][3] for c in calls if c[0] == "place_stop_order"], [c[1][3] for c in calls if c[0] == "place_take_profit_order"]),
      ([97.0], [104.0]))
state["open_orders"] = [{"Status": "open", "Type": "stop", "Size": "10", "Filled": "0"},
                        {"Status": "open", "Type": "take_profit_limit", "OriginType": "take_profit_limit"}]
calls.clear()
pm._check_and_reheal_bracket_legs(t2, {"size": 10})
check("REGRESIA: obe nohy na burze -> ziadny zasah", names(), [])

print("\n10) Otvorenie pozicie (trade_cycle._setup_tp_runner)")
sized = {"direction": "Long", "entry_price": 100.0, "take_profit_price": 104.0, "stop_loss_price": 97.0}
tt = Trade()
config.TP_RUNNER_ENABLED = True
ex = trade_cycle._setup_tp_runner(tt, sized, {"order_tick_price": "0.01"}, {"atr14": 1.5})
check("na burzu ide havarijny TP 140", ex, 140.0)
check("obchod: runner, havarijny TP, SL, ATR", (tt.tp_mode, tt.tp_exchange_price, tt.active_stop_price, tt.entry_atr),
      ("runner", 140.0, 97.0, 1.5))
config.TP_RUNNER_ENABLED = False
tt2 = Trade()
ex = trade_cycle._setup_tp_runner(tt2, sized, {"order_tick_price": "0.01"}, {"atr14": 1.5})
check("VYPNUTE: na burzu ide klasicky TP 104", ex, 104.0)
check("VYPNUTE: obchod bez akychkolvek novych poli", (tt2.tp_mode, tt2.tp_exchange_price, tt2.active_stop_price), (None, None, None))
config.TP_RUNNER_ENABLED = True
ex = trade_cycle._setup_tp_runner(Trade(), sized, None, None)
check("bez market_meta/ta nespadne", ex, 140.0)

print("\n11) Cely tik position_monitor.check_open_trades")
for t in s.query(Trade).filter(Trade.status == "open").all():
    t.status = "archiv"
s.commit()
chop = mk(20, "CHOP-USD", hours_ago=25)                 # runner, TP nikdy, 25 h
run = mk(21, "RUN-USD", hours_ago=5)                    # runner, zamkne sa teraz
classic = mk(22, "OLD-USD", mode=None, hours_ago=3)     # stary klasicky obchod
state["positions"] = [{"symbol": x, "size": 10} for x in ("CHOP-USD", "RUN-USD", "OLD-USD")]
state["markets"] = [market("CHOP-USD", 101.0), market("RUN-USD", 104.5), market("OLD-USD", 110.0)]
state["open_orders"] = [{"Status": "open", "Type": "stop", "Size": "10", "Filled": "0"},
                        {"Status": "open", "Type": "take_profit_limit"}]
exact["reason"] = "force_closed_by_bot"
calls.clear(); discord_msgs.clear()
pm.check_open_trades()
s.expire_all()
chop, run, classic = (s.get(Trade, i) for i in (20, 21, 22))
check("CHOP: nezamknuty runner po 24 h zatvoreny ako doteraz", (chop.status, chop.close_reason),
      ("closed_by_timeout", "force_closed_by_bot"))
check("RUN: zamknuty v tomto tiku, bezi dalej", (run.status, run.active_stop_price, run.tp_locked_at is not None),
      ("open", 103.0, True))
check("RUN: limit posunuty na 48 h od otvorenia", run.expires_at, run.opened_at + timedelta(hours=48))
check("  a neskor nez by bol klasicky 24 h limit", run.expires_at > run.opened_at + timedelta(hours=24), True)
check("OLD: klasicky obchod netknuty (cena nad TP riesi burza)", (classic.status, classic.tp_locked_at), ("open", None))
check("ziadna falosna REPAIR notifikacia", [m for m in discord_msgs if m[0] == "repair"], [])
# o 24 h neskor (49 h od otvorenia) -> runner timeout
run.expires_at = NOWN - timedelta(minutes=1); s.commit()
state["positions"] = [{"symbol": "RUN-USD", "size": 10}]
state["markets"] = [market("RUN-USD", 104.6)]
pm.check_open_trades()
s.expire_all(); run = s.get(Trade, 21)
check("RUN: po 48 h zatvoreny s vlastnym dovodom", (run.status, run.close_reason), ("closed_by_timeout", "tp_runner_timeout"))
check("dennik: lock ... close", [e.kind for e in s.query(TpRunnerEvent).filter_by(trade_id=21)][-1], "close")
# zamknuty runner zatvoreny burzou (SL fill)
run2 = mk(23, "RUN2-USD", hours_ago=5)
tp_runner.manage(run2, {"size": 10}, 104.5, 0.01, s, now); s.commit()
state["positions"] = []
exact["reason"] = "stop_loss"
pm.check_open_trades()
s.expire_all(); run2 = s.get(Trade, 23)
check("zamknuty runner zatvoreny burzou -> tp_runner_stop", (run2.status, run2.close_reason),
      ("closed_by_exchange", "tp_runner_stop"))
# REGRESIA: klasicky obchod zatvoreny SL burzou -> stop_loss
old2 = mk(24, "OLD2-USD", mode=None)
pm.check_open_trades()
s.expire_all()
check("REGRESIA: klasicky SL ostava stop_loss", s.get(Trade, 24).close_reason, "stop_loss")

print("\n12) Vypadok /v2/markets - predlzovanie sa len preskoci, nic nespadne")
run3 = mk(25, "RUN3-USD")
state["positions"] = [{"symbol": "RUN3-USD", "size": 10}]
strike_client.get_markets = lambda: (_ for _ in ()).throw(RuntimeError("503"))
crashed = False
try:
    pm.check_open_trades()
except Exception:
    crashed = True
s.expire_all()
check("tik nespadol, pozicia open, nic nezamknute", (crashed, s.get(Trade, 25).status, s.get(Trade, 25).tp_locked_at),
      (False, "open", None))

print("\n13) Claude pri kontrole pozicie vie o predlzeni (a pri klasickej nic nove)")
import assets  # noqa: E402
import claude_analyst  # noqa: E402
A = next(a for a in assets.ALL_ASSETS if a["name"] == "BTC")
op = {"direction": "Long", "entry_price": 100, "live_price": 106, "stop_loss_price": 97, "take_profit_price": 104,
      "leverage": 10, "opened_at_str": "x", "hours_held": 5.0, "unrealized_pnl_usd": 60.0, "unrealized_pnl_pct": 60.0,
      "best_price_since_open": 106, "best_price_hours_ago": 0.1,
      "runner_note": "PREDĹŽENÝ TP: take-profit 104 už bol dosiahnutý ... SL na burze je teraz 104.5"}
p = claude_analyst._build_user_prompt(A, {"last_price": 106, "atr14": 1}, {}, {"session": "US"}, [], None, None,
                                      None, None, None, None, None, None, open_position=op)
check("zamknuta pozicia: riadok o predlzenom TP v prompte", "PREDĹŽENÝ TP" in p, True)
op["runner_note"] = None
p = claude_analyst._build_user_prompt(A, {"last_price": 106, "atr14": 1}, {}, {"session": "US"}, [], None, None,
                                      None, None, None, None, None, None, open_position=op)
check("REGRESIA klasicka pozicia: ziadna zmienka", "PREDĹŽENÝ" in p, False)

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
