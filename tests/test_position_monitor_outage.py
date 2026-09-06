"""Vypadok burzy nesmie vyzerat ako "pozicie sa zatvorili".

POVOD (Strike outage 2026-09-06, 09:13-12:37 UTC, 3.4 h): check_open_trades
vyhadzoval vynimku do scheduleru - job sa opakoval spravne, ale logy boli plne
tracebackov. price_poller aj watch_monitor to iste zlyhanie riesia potichu
("preskakujem tento tik"), takze sa to dorovnalo na rovnaky vzor.

Pri tom sa ale nesmie stratit to, co nas pocas vypadku ZACHRANILO: chybajuci
symbol v odpovedi /v2/positions znamena "pozicia sa zatvorila". Keby to volanie
pri vypadku vratilo prazdny zoznam namiesto vynimky, bot by oznacil VSETKY
otvorene pozicie za zatvorene, spustil na ne post-close review a poslal
notifikacie o zatvoreni, ktore sa nikdy nestalo. Vtedy to boli dve realne
pozicie (#191 WTI, #192 NVDA), obe nakoniec skoncili v zisku.

Test teda strazi OBE strany: ze zlyhanie nezhodi job, a ze zaroven nezhodi ani
pozicie.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/posmon.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import position_monitor as pm  # noqa: E402
import strike_client  # noqa: E402
from db import Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


# Self-heal backfilly chodia na siet a s testovanou vetvou nesuvisia.
# _check_and_reheal_bracket_legs je tu NAJDOLEZITEJSI: bez neho test realne
# vojde do vetvy, ktora vola strike_client.place_stop_order/
# place_take_profit_order - teda ZADAVA OBJEDNAVKY na burze. Toto je
# live-money bot; test nesmie mat ani teoreticku moznost sa tam dostat.
for name in ("_backfill_missing_exact_data", "_backfill_stale_reviews",
             "_backfill_missing_open_notifications", "_backfill_missing_close_notifications",
             "_backfill_missing_entry_fill_price", "_fire_due_recomputes",
             "_check_and_reheal_bracket_legs"):
    setattr(pm, name, lambda *a, **k: None)


# Poistka: keby sa niekedy objavila nova cesta k zadaniu objednavky, test
# spadne nahlas namiesto toho, aby ticho siahol na burzu.
def _must_not_call(*a, **k):
    raise AssertionError("TEST SIAHOL NA BURZU - toto sa nesmie stat")


for name in ("place_stop_order", "place_take_profit_order", "open_bracket_position",
             "close_position", "get_open_orders"):
    if hasattr(strike_client, name):
        setattr(strike_client, name, _must_not_call)

fired = {"reviews": 0, "close_notifs": 0, "open_notifs": 0, "recomputes": 0}
pm._fire_post_close_reviews = lambda x: fired.__setitem__("reviews", fired["reviews"] + len(x or []))
pm._fire_close_notifications = lambda x: fired.__setitem__("close_notifs", fired["close_notifs"] + len(x or []))
pm._fire_open_notifications = lambda x: fired.__setitem__("open_notifs", fired["open_notifs"] + len(x or []))
pm._fire_recomputes = lambda x: fired.__setitem__("recomputes", fired["recomputes"] + len(x or []))

s = get_session()
opened = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
for i, sym in enumerate(("WTI-USD", "NVDA-USD"), start=191):
    s.add(Trade(id=i, symbol=sym, direction="Long", status="open", entry_price=100.0,
                stop_loss_price=99.0, take_profit_price=102.0, size=1.0,
                notional_usd=100.0, margin_usd=10.0, leverage=10, opened_at=opened,
                expires_at=opened + timedelta(hours=24)))
s.commit()


def statuses():
    s.expire_all()
    return {t.id: t.status for t in s.query(Trade).order_by(Trade.id)}


print("1) Vypadok burzy (503) - job nesmie spadnut")
def boom():
    raise RuntimeError("Strike API GET /v2/positions -> 503: Service Temporarily Unavailable")


strike_client.get_positions = boom
crashed = False
try:
    pm.check_open_trades()
except Exception as e:
    crashed = True
    print(f"       vynimka unikla: {type(e).__name__}: {e}")
check("check_open_trades vynimku neprepusti", crashed, False)

print("\n2) A HLAVNE - ziadna pozicia sa nesmie oznacit za zatvorenu")
check("obe zostavaju 'open'", statuses(), {191: "open", 192: "open"})
check("ziadny post-close review sa nespustil", fired["reviews"], 0)
check("ziadna notifikacia o zatvoreni", fired["close_notifs"], 0)

print("\n3) Iny typ zlyhania (timeout, DNS) sa sprava rovnako")
def timeout():
    raise TimeoutError("read timeout")


strike_client.get_positions = timeout
pm.check_open_trades()
check("stale obe 'open'", statuses(), {191: "open", 192: "open"})

print("\n4) Ked burza nabehne, normalne spravanie zostava")
# Iba WTI je este na burze -> NVDA sa ma korektne oznacit za zatvorenu.
strike_client.get_positions = lambda: [{"symbol": "WTI-USD", "size": 1.0}]
strike_client.cancel_all_orders = lambda symbol: None
pm._apply_exact_close = lambda trade, reason: None
pm._check_and_queue_review = lambda t, sess, pending: pending.append(t.id)
pm._check_and_queue_close_notification = lambda t, pending: pending.append(t.id)
pm._check_and_queue_recompute = lambda t: None
pm.watch_monitor.mark_hot = lambda sym: None
pm.check_open_trades()
st = statuses()
check("WTI (stale na burze) zostava open", st[191], "open")
check("NVDA (uz nie je na burze) sa zatvorila", st[192], "closed_by_exchange")
check("a spustil sa na nu review", fired["reviews"], 1)

print("\n5) Prazdny zoznam z burzy je LEGITIMNY vysledok, nie zlyhanie")
# Rozdiel oproti bodu 1: tu burza ODPOVEDALA a povedala "ziadne pozicie".
strike_client.get_positions = lambda: []
pm.check_open_trades()
check("aj WTI sa teraz zatvorila", statuses()[191], "closed_by_exchange")

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
