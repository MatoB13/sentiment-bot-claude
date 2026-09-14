"""AI zatvorenie s 15-minutovym potvrdenim (ai_close.py) + regresia (2026-09-14).

Pouzivatel schvalil po merani (AI zatvaralo do spicky protipohybu, ZHIPU #224).
BURZA JE PODVRHNUTA - kazde volanie strike_client ide do zoznamu `calls`."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/aiclose.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ai_close  # noqa: E402
import assets  # noqa: E402
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
    print(f"  {'OK ' if good else 'CHYBA'} {label:<70} {got!r} (ocakavane {want!r})")


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
strike_client.get_positions = lambda symbol=None: state["positions"]
strike_client.get_markets = lambda: state["markets"]
strike_client.get_open_orders = lambda symbol=None: state["open_orders"]
strike_client.open_bracket_position = lambda **k: (_ for _ in ()).throw(AssertionError("test nesmie otvarat"))
msgs = []
discord_client.notify_ai_close_pending = lambda *a, **k: msgs.append(("pending", a)) or True
discord_client.notify_ai_close_resolved = lambda *a, **k: msgs.append(("resolved", a)) or True
discord_client.notify_bracket_leg_restored = lambda *a, **k: msgs.append(("repair", a))
discord_client.notify_tp_runner_locked = lambda *a, **k: msgs.append(("locked", a)) or True

for name in ("_backfill_missing_exact_data", "_backfill_stale_reviews", "_backfill_missing_open_notifications",
             "_backfill_missing_close_notifications", "_backfill_missing_entry_fill_price", "_fire_due_recomputes",
             "_fire_post_close_reviews", "_fire_close_notifications", "_fire_open_notifications", "_fire_recomputes"):
    setattr(pm, name, lambda *a, **k: None)
reviews, notes = [], []
pm._check_and_queue_review = lambda t, s_, p: (reviews.append(t.id) if t.close_reason in pm._TRIGGER_REVIEW_REASONS else None)
pm._check_and_queue_close_notification = lambda t, p: notes.append(t.id)
pm._check_and_queue_recompute = lambda t: None
pm.watch_monitor.mark_hot = lambda sym: None
exact = {"reason": None}
pm._lookup_exact_close = lambda t: ({"close_reason": exact["reason"], "entry_fill_price": t.entry_price,
                                     "close_fill_price": 0.0, "fees_usd": 0.0, "pnl_usd": -1.0} if exact["reason"] else None)

DEFAULTS = (config.AI_CLOSE_CONFIRM_MINUTES, config.AI_CLOSE_CONFIRM_MAX_WAIT_MINUTES, config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD)
config.AI_CLOSE_CONFIRM_MINUTES, config.AI_CLOSE_CONFIRM_MAX_WAIT_MINUTES = 15, 30
config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD = 50
s = get_session()
NOW = datetime.now(timezone.utc)
NOWN = NOW.replace(tzinfo=None)
SYM = assets.enabled_assets()[0]["strike_symbol"]
ASSET = assets.enabled_assets()[0]


def mk(tid, sym, direction="Short", entry=94.0, sl=96.6, tp=90.25, mode=None, **kw):
    t = Trade(id=tid, symbol=sym, direction=direction, status="open", entry_price=entry, stop_loss_price=sl,
              take_profit_price=tp, size=5.0, notional_usd=470, margin_usd=47, leverage=10,
              opened_at=NOWN - timedelta(hours=3), expires_at=NOWN + timedelta(hours=21), tp_mode=mode, **kw)
    s.add(t); s.commit()
    return t


def names():
    return [c[0] for c in calls]


def ev(tid):
    return [e.kind for e in s.query(TpRunnerEvent).filter_by(trade_id=tid).order_by(TpRunnerEvent.id)]


print("1) Predvolby a zapis cakajuceho zatvorenia")
check("predvolby: 15 min, max cakanie 30 min, prah istoty ostava 50", DEFAULTS, (15, 30, 50))
t1 = mk(1, "A1-USD")
r = ai_close.start_pending(t1, 95.78, 60, s, NOW); s.commit()
check("odlozene (volajuci nezatvara), cena/istota/cas ulozene",
      (r, t1.ai_close_pending_price, t1.ai_close_pending_conf, t1.ai_close_pending_at is not None), (True, 95.78, 60, True))
check("dennik ai_pending + Discord", (ev(1), msgs[-1][0]), (["ai_pending"], "pending"))
r = ai_close.start_pending(t1, 97.0, 70, s, NOW + timedelta(minutes=5)); s.commit()
check("nove rozhodnutie pocas cakania: povodna cena ostava", (r, t1.ai_close_pending_price), (True, 95.78))
config.AI_CLOSE_CONFIRM_MINUTES = 0
check("potvrdenie vypnute (0) -> volajuci zatvori hned", ai_close.start_pending(mk(2, "A2-USD"), 95.0, 60, s, NOW), False)
config.AI_CLOSE_CONFIRM_MINUTES = 15
check("bez ceny -> volajuci zatvori hned", ai_close.start_pending(mk(3, "A3-USD"), None, 60, s, NOW), False)

print("\n2) trade_cycle._maybe_ai_early_close")
t4 = mk(4, SYM)
state["positions"] = [{"symbol": SYM, "size": -5}]
calls.clear()
trade_cycle._maybe_ai_early_close(ASSET, t4, {"recommendation": "consider_closing", "close_confidence": 60}, s, 95.0)
check("istota 60 -> len cakajuce, ziadne volanie burzy", (names(), t4.status, t4.ai_close_pending_price), ([], "open", 95.0))
t5 = mk(5, SYM)
trade_cycle._maybe_ai_early_close(ASSET, t5, {"recommendation": "consider_closing", "close_confidence": 45}, s, 95.0)
check("istota pod prahom -> nic", t5.ai_close_pending_at, None)
trade_cycle._maybe_ai_early_close(ASSET, t5, {"recommendation": "hold", "close_confidence": 90}, s, 95.0)
check("hold -> nic", t5.ai_close_pending_at, None)
config.AI_CLOSE_CONFIRM_MINUTES = 0
t6 = mk(6, SYM)
calls.clear()
trade_cycle._maybe_ai_early_close(ASSET, t6, {"recommendation": "consider_closing", "close_confidence": 60}, s, 95.0)
check("REGRESIA potvrdenie vypnute: zatvori hned ako predtym", (names(), t6.status, t6.close_reason),
      (["cancel_all_orders", "close_position_market"], "closed_by_ai", "ai_early_close"))
check("  absolutna velkost pri shorte", calls[1][1][1], 5.0)
config.AI_CLOSE_CONFIRM_MINUTES = 15

print("\n3) Vyhodnotenie po 15 min (short, rozhodnutie pri 95.78)")
LIVE = {"size": -5}
t7 = mk(7, "B7-USD"); ai_close.start_pending(t7, 95.78, 60, s, NOW); s.commit()
calls.clear()
r = ai_close.resolve(t7, LIVE, 95.0, 0.01, s, NOW + timedelta(minutes=10))
check("pred uplynutim 15 min: nic", (r, names(), t7.ai_close_pending_at is not None), ({"closed": False, "orders_changed": False}, [], True))
r = ai_close.resolve(t7, LIVE, 96.1, 0.01, s, NOW + timedelta(minutes=15)); s.commit()
check("cena HORSIA (96.1 > 95.78 pri shorte) -> zavriet trhovo",
      (names(), t7.status, t7.close_reason, r["closed"]), (["cancel_all_orders", "close_position_market"], "closed_by_ai", "ai_early_close", True))
check("  dennik ai_pending, ai_close; cakanie zrusene", (ev(7), t7.ai_close_pending_at), (["ai_pending", "ai_close"], None))
t8 = mk(8, "B8-USD"); ai_close.start_pending(t8, 95.78, 60, s, NOW); s.commit()
r = ai_close.resolve(t8, LIVE, 95.78, 0.01, s, NOW + timedelta(minutes=16)); s.commit()
check("cena ROVNAKA -> tiez zavriet (nie je lepsia)", t8.status, "closed_by_ai")
t9 = mk(9, "B9-USD"); ai_close.start_pending(t9, 95.78, 60, s, NOW); s.commit()
calls.clear(); msgs.clear()
r = ai_close.resolve(t9, LIVE, 94.5, 0.01, s, NOW + timedelta(minutes=15)); s.commit()
check("cena LEPSIA (94.5) -> nezatvarat, ochranny SL 95.78 + klasicky TP 90.25",
      [(c[0], c[1][3] if len(c[1]) > 3 else None) for c in calls],
      [("cancel_all_orders", None), ("place_stop_order", 95.78), ("place_take_profit_order", 90.25)])
check("  pozicia otvorena, active_stop 95.78, ai_protected, cakanie zrusene",
      (t9.status, t9.active_stop_price, t9.ai_protected_at is not None, t9.ai_close_pending_at), ("open", 95.78, True, None))
check("  oprava noh v tomto tiku preskocena; dennik ai_protect; Discord", (r["orders_changed"], ev(9)[-1], msgs[-1][0]),
      (True, "ai_protect", "resolved"))

print("\n4) Hranicne pripady")
t10 = mk(10, "C10-USD", active_stop_price=95.5); ai_close.start_pending(t10, 95.78, 60, s, NOW); s.commit()
calls.clear()
ai_close.resolve(t10, LIVE, 94.0, 0.01, s, NOW + timedelta(minutes=15)); s.commit()
check("SL uz tesnejsi (95.5 < 95.78 pri shorte) -> ziadne volanie, SL sa neuvolni",
      (names(), t10.active_stop_price, t10.ai_close_pending_at), ([], 95.5, None))
t11 = mk(11, "C11-USD"); ai_close.start_pending(t11, 95.78, 60, s, NOW); s.commit()
state["fail_stop"] = 2; calls.clear()
r = ai_close.resolve(t11, LIVE, 94.0, 0.01, s, NOW + timedelta(minutes=15)); s.commit()
check("ochranny SL 2x odmietnuty -> zavriet trhovo (Claudovo rozhodnutie plati)",
      (t11.status, "close_position_market" in names(), r["closed"]), ("closed_by_ai", True, True))
check("  dennik ai_fail + ai_close", ev(11)[-2:], ["ai_fail", "ai_close"])
t12 = mk(12, "C12-USD"); ai_close.start_pending(t12, 95.78, 60, s, NOW); s.commit()
calls.clear()
ai_close.resolve(t12, LIVE, None, 0.01, s, NOW + timedelta(minutes=20))
check("vypadok ceny po 15 min: caka (nic)", (names(), t12.status), ([], "open"))
ai_close.resolve(t12, LIVE, None, 0.01, s, NOW + timedelta(minutes=46)); s.commit()
check("  po 15 + 30 min bez ceny: zavriet trhovo", t12.status, "closed_by_ai")
t13 = mk(13, "C13-USD", direction="Long", entry=100, sl=97, tp=104, mode="runner",
         tp_exchange_price=140.0, active_stop_price=97.0)
ai_close.start_pending(t13, 98.5, 60, s, NOW); s.commit()
calls.clear()
ai_close.resolve(t13, {"size": 5}, 99.2, 0.01, s, NOW + timedelta(minutes=15)); s.commit()
check("long v akcnom rezime, cena lepsia -> SL 98.5 + HAVARIJNY TP 140 (nie 104)",
      [(c[0], c[1][3] if len(c[1]) > 3 else None) for c in calls],
      [("cancel_all_orders", None), ("place_stop_order", 98.5), ("place_take_profit_order", 140.0)])

print("\n5) Dovod zatvorenia, oprava noh, predlzeny TP")
check("ochranny SL zasiahnuty -> ai_protect_stop", ai_close.close_reason(t9, "stop_loss"), "ai_protect_stop")
check("REGRESIA bez ochrany -> stop_loss", ai_close.close_reason(mk(14, "D14-USD"), "stop_loss"), "stop_loss")
check("TP ostava take_profit", ai_close.close_reason(t9, "take_profit"), "take_profit")
t9.tp_locked_at = NOWN
check("zamknuty predlzeny TP ma prednost (stop_loss nechava na runner mapovanie)", ai_close.close_reason(t9, "stop_loss"), "stop_loss")
t9.tp_locked_at = None; s.commit()
check("ai_protect_stop ide do notifikacii, nie do okamziteho review",
      ("ai_protect_stop" in pm._NOTIFY_CLOSE_REASONS, "ai_protect_stop" in pm._TRIGGER_REVIEW_REASONS), (True, False))
state["open_orders"] = []
calls.clear()
pm._check_and_reheal_bracket_legs(t9, {"size": -5})
check("oprava stratenych noh: obnovi OCHRANNY SL 95.78 a TP 90.25",
      ([c[1][3] for c in calls if c[0] == "place_stop_order"], [c[1][3] for c in calls if c[0] == "place_take_profit_order"]),
      ([95.78], [90.25]))
t15 = mk(15, "D15-USD", direction="Long", entry=100, sl=97, tp=104, mode="runner", tp_exchange_price=140.0,
         active_stop_price=103.6, entry_atr=1.0)
calls.clear()
tp_runner.manage(t15, {"size": 5}, 104.2, 0.01, s, NOW); s.commit()
check("predlzeny TP: zamok (103) neuvolni tesnejsi ochranny SL 103.6", t15.active_stop_price, 103.6)

print("\n6) Cely tik position_monitor.check_open_trades")
for t in s.query(Trade).filter(Trade.status == "open").all():
    t.status = "archiv"
s.commit()
worse = mk(20, "E20-USD"); ai_close.start_pending(worse, 95.78, 60, s, NOW - timedelta(minutes=16))
better = mk(21, "E21-USD"); ai_close.start_pending(better, 95.78, 60, s, NOW - timedelta(minutes=16))
waiting = mk(22, "E22-USD"); ai_close.start_pending(waiting, 95.78, 60, s, NOW - timedelta(minutes=5))
plain = mk(23, "E23-USD")
s.commit()
state["positions"] = [{"symbol": x, "size": -5} for x in ("E20-USD", "E21-USD", "E22-USD", "E23-USD")]
state["markets"] = [{"symbol": "E20-USD", "mark_price": "96.2", "order_tick_price": "0.01"},
                    {"symbol": "E21-USD", "mark_price": "94.4", "order_tick_price": "0.01"},
                    {"symbol": "E22-USD", "mark_price": "99.0", "order_tick_price": "0.01"},
                    {"symbol": "E23-USD", "mark_price": "99.0", "order_tick_price": "0.01"}]
state["open_orders"] = [{"Status": "open", "Type": "stop", "Size": "5", "Filled": "0"},
                        {"Status": "open", "Type": "take_profit_limit", "OriginType": "take_profit_limit"}]
exact["reason"] = None
calls.clear(); reviews.clear(); notes.clear()
pm.check_open_trades()
s.expire_all()
w, b, wt, pl = (s.get(Trade, i) for i in (20, 21, 22, 23))
check("horsia cena: zatvorene AI, notifikacia ano, okamzity review nie",
      (w.status, w.close_reason, 20 in notes, 20 in reviews), ("closed_by_ai", "ai_early_close", True, False))
check("lepsia cena: otvorena, ochranny SL 95.78", (b.status, b.active_stop_price, b.ai_protected_at is not None), ("open", 95.78, True))
check("este necakalo 15 min: nic sa nedeje", (wt.status, wt.ai_close_pending_at is not None, wt.active_stop_price), ("open", True, None))
check("REGRESIA obchod bez AI rozhodnutia: netknuty", (pl.status, pl.active_stop_price, pl.ai_close_pending_at), ("open", None, None))
check("ziadna falosna REPAIR notifikacia", [m for m in msgs if m[0] == "repair" and "E2" in str(m)], [])

print("\n7) Claude vidi stav v prompte")
check("cakajuce: poznamka o potvrdeni", (trade_cycle._runner_note(wt) or "").startswith("TVOJE ODPORÚČANIE ZATVORIŤ"), True)
check("ochranny SL: poznamka s cenou", "OCHRANNÝ SL" in (trade_cycle._runner_note(b) or "") and "95.78" in trade_cycle._runner_note(b), True)
check("REGRESIA klasicky obchod: ziadna poznamka", trade_cycle._runner_note(pl), None)

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
