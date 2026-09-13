"""Zrusenie okamziteho review po SL / likvidacii / AI zatvoreni (2026-09-13).

Pouzivatel schvalil po merani: 93 reviewov za $18.85 viedlo k 5 obchodom
(-144.60 $). Poucenie nesie odlozeny verdikt, rychly navrat alarm na extremy.
Test strazi, ze:
- SL / likvidacia / AI zatvorenie review NESPUSTIA (ani cez self-heal),
- TP / timeout / kill-switch / predlzeny TP ho spustaju DALEJ (odtial sa da
  hned vstupit v rovnakom aj opacnom smere - kontinuita trendu),
- notifikacia o zatvoreni a hot-watch po SL ostavaju,
- odlozeny verdikt si SL obchod stale najde.
NIKDY nevola burzu ani Clauda."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/nosl_review.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import position_monitor as pm  # noqa: E402
import tp_runner  # noqa: E402
import trade_cycle  # noqa: E402
from db import PriceBar, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<70} {got!r} (ocakavane {want!r})")


pm._build_review_context = lambda trade, session, asset: {"trade_id": trade.id}
SYM = assets.enabled_assets()[0]["strike_symbol"]
s = get_session()
NOWN = datetime.now(timezone.utc).replace(tzinfo=None)


def mk(tid, reason, hours_ago=1.0):
    t = Trade(id=tid, symbol=SYM, direction="Long", status="closed_by_exchange", entry_price=100.0,
              stop_loss_price=97.0, take_profit_price=104.0, size=1.0, notional_usd=100, margin_usd=10,
              leverage=10, opened_at=NOWN - timedelta(hours=hours_ago + 3), expires_at=NOWN + timedelta(hours=20),
              closed_at=NOWN - timedelta(hours=hours_ago), close_reason=reason, close_fill_price=99.0, pnl_usd=-3.0)
    s.add(t); s.commit()
    return t


print("1) Ktore dovody spustaju okamzity review")
for reason in ("stop_loss", "liquidation", "ai_early_close"):
    q = []
    t = mk(len(s.query(Trade).all()) + 1, reason)
    pm._check_and_queue_review(t, s, q)
    check(f"{reason}: review sa NESPUSTI, flag ostava prazdny", (len(q), t.post_close_review_triggered_at), (0, None))
for reason in ("take_profit", "force_closed_by_bot", "manual_kill_switch") + tuple(sorted(tp_runner.RUNNER_REASONS)):
    q = []
    t = mk(len(s.query(Trade).all()) + 1, reason)
    pm._check_and_queue_review(t, s, q)
    check(f"{reason}: review sa SPUSTI (moze hned otvorit poziciu)", (len(q), t.post_close_review_triggered_at is not None), (1, True))

print("\n2) Self-heal zaseknutych review nevzkriesi SL review")
t = mk(100, "stop_loss")
t.post_close_review_triggered_at = datetime.now(timezone.utc) - timedelta(minutes=30)   # stara hodnota spred zmeny
s.commit()
q = []
pm._backfill_stale_reviews(s, q)
check("stary SL obchod so zaseknutym flagom: znova sa nezaradi", [x[1]["trade_id"] for x in q if x[1]["trade_id"] == 100], [])

print("\n3) Notifikacia a hot-watch po SL ostavaju")
check("SL je stale v dovodoch na Discord notifikaciu", "stop_loss" in pm._NOTIFY_CLOSE_REASONS, True)
check("AI zatvorenie tiez", "ai_early_close" in pm._NOTIFY_CLOSE_REASONS, True)
check("poistka: SL/AI/likvidacia stale 'len vyhodnotenie'", {"stop_loss", "liquidation", "ai_early_close"} <= pm._EVALUATION_ONLY_CLOSE_REASONS, True)
hot = []
pm.watch_monitor.mark_hot = lambda sym: hot.append(sym)
notified = []
pm._check_and_queue_close_notification = lambda t, p: notified.append(t.id)
pm._check_and_queue_recompute = lambda t: None
pm._lookup_exact_close = lambda t: {"close_reason": "stop_loss", "entry_fill_price": 100.0, "close_fill_price": 97.0,
                                    "fees_usd": 0.1, "pnl_usd": -3.0}
for name in ("_backfill_missing_exact_data", "_backfill_stale_reviews", "_backfill_missing_open_notifications",
             "_backfill_missing_close_notifications", "_backfill_missing_entry_fill_price", "_fire_due_recomputes",
             "_fire_post_close_reviews", "_fire_close_notifications", "_fire_open_notifications", "_fire_recomputes"):
    setattr(pm, name, lambda *a, **k: None)
fired = []
pm._fire_post_close_reviews = lambda pending: fired.extend(pending)
for t in s.query(Trade).all():
    t.status = "archiv"
live = Trade(id=200, symbol=SYM, direction="Long", status="open", entry_price=100.0, stop_loss_price=97.0,
             take_profit_price=104.0, size=1.0, notional_usd=100, margin_usd=10, leverage=10,
             opened_at=NOWN - timedelta(hours=2), expires_at=NOWN + timedelta(hours=22))
s.add(live); s.commit()
pm.strike_client.get_positions = lambda: []          # pozicia zmizla z burzy = SL
pm.strike_client.cancel_all_orders = lambda symbol: None
pm.strike_client.get_markets = lambda: []
pm.check_open_trades()
s.expire_all()
t = s.get(Trade, 200)
check("cely tik: SL zatvorenie zapisane", (t.status, t.close_reason), ("closed_by_exchange", "stop_loss"))
check("  ziadny review sa nespustil", fired, [])
check("  Discord notifikacia o zatvoreni zaradena", 200 in notified, True)
check("  hot-watch po zatvoreni zapnuty (rychle watch urovne)", SYM in hot, True)

print("\n4) Odlozeny verdikt si SL obchod najde (4-36 h po zatvoreni)")
for t in s.query(Trade).all():
    t.status = "archiv"
sl = mk(300, "stop_loss", hours_ago=6)
sl.status = "closed_by_exchange"
base = NOWN.replace(minute=0, second=0, microsecond=0)
for h in range(8, -1, -1):
    s.add(PriceBar(symbol=SYM, hour_start=base - timedelta(hours=h), open=99.0, high=100.0, low=98.5, close=99.5))
s.commit()
pv = trade_cycle._pending_close_verdict(SYM, s, datetime.now(timezone.utc))
check("pending verdikt pre SL obchod existuje", pv is not None and pv["trade_id"] == 300, True)

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
