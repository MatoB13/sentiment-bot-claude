"""Minutove ceny (price_minutes) z price_pollera + regresia hodinovych sviecok.

2026-09-14, na ziadost pouzivatela: graf "posledna hodina" pri otvorenej pozicii.
Test strazi, ze:
- kazdy tik zapise jednu minutovu cenu na ticker (z tej istej /v2/markets
  odpovede, ziadne volanie navyse),
- hodinova sviecka (PriceBar) sa pocita presne ako doteraz,
- stare minutove ceny sa mazu LEN v prvom tiku hodiny a len starsie ako limit,
- vypadok /v2/markets ani /v2/account nic nezhodi a nezapise polovicate data.
NIKDY nevola burzu."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/price_minutes.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import price_poller as pp  # noqa: E402
from db import PriceBar, PriceMinute, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {got!r} (ocakavane {want!r})")


FIXED = {"now": datetime(2026, 9, 14, 10, 5, 30, tzinfo=timezone.utc)}


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED["now"]


pp.datetime = FakeDT
SYMS = [a["strike_symbol"] for a in assets.ALL_ASSETS]
state = {"price": 100.0, "markets_fail": False, "account_fail": False}


def get_markets():
    if state["markets_fail"]:
        raise RuntimeError("Strike 503")
    return [{"symbol": s, "mark_price": str(state["price"] + i), "funding_rate": "0.0001"} for i, s in enumerate(SYMS)]


def get_account():
    if state["account_fail"]:
        raise RuntimeError("account 500")
    return {"wallet_balance": 1000, "available_balance": 900, "margin_balance": 1000, "unrealized_pnl": 0, "total_margin": 100}


pp.strike_client.get_markets = get_markets
pp.strike_client.get_account = get_account
for n in ("place_stop_order", "place_take_profit_order", "close_position_market", "cancel_all_orders", "open_bracket_position"):
    setattr(pp.strike_client, n, lambda *a, **k: (_ for _ in ()).throw(AssertionError("TEST SIAHOL NA BURZU")))
s = get_session()
S0 = SYMS[0]

print("1) Prvy tik 10:05")
pp.poll_prices()
s.expire_all()
check("jedna minutova cena na kazdy ticker", s.query(PriceMinute).count(), len(SYMS))
m = s.query(PriceMinute).filter_by(symbol=S0).one()
check("cena a cas tiku (naive UTC)", (m.price, m.ts), (100.0, datetime(2026, 9, 14, 10, 5, 30)))
b = s.query(PriceBar).filter_by(symbol=S0).one()
check("REGRESIA: hodinova sviecka 10:00 O/H/L/C = 100", (b.hour_start, b.open, b.high, b.low, b.close),
      (datetime(2026, 9, 14, 10, 0), 100.0, 100.0, 100.0, 100.0))

print("\n2) Dalsie tiky v tej istej hodine")
FIXED["now"] = datetime(2026, 9, 14, 10, 6, 30, tzinfo=timezone.utc); state["price"] = 103.0
pp.poll_prices()
FIXED["now"] = datetime(2026, 9, 14, 10, 7, 30, tzinfo=timezone.utc); state["price"] = 98.0
pp.poll_prices()
s.expire_all()
check("tri minutove ceny pre ticker, v spravnom poradi",
      [x.price for x in s.query(PriceMinute).filter_by(symbol=S0).order_by(PriceMinute.ts)], [100.0, 103.0, 98.0])
b = s.query(PriceBar).filter_by(symbol=S0).one()
check("REGRESIA: sviecka O 100 / H 103 / L 98 / C 98", (b.open, b.high, b.low, b.close), (100.0, 103.0, 98.0, 98.0))

print("\n3) Upratanie starych minutovych cien")
old = datetime(2026, 9, 10, 9, 0)           # 4 dni stare
edge = datetime(2026, 9, 11, 11, 30)        # 2 dni 23.5 h - este sa drzi
s.add_all([PriceMinute(symbol=S0, ts=old, price=1.0), PriceMinute(symbol=S0, ts=edge, price=2.0)])
s.commit()
FIXED["now"] = datetime(2026, 9, 14, 10, 30, 0, tzinfo=timezone.utc)
pp.poll_prices()
s.expire_all()
check("v strede hodiny sa nic nemaze", s.query(PriceMinute).filter(PriceMinute.ts <= edge).count(), 2)
FIXED["now"] = datetime(2026, 9, 14, 11, 0, 20, tzinfo=timezone.utc)
pp.poll_prices()
s.expire_all()
check("prvy tik hodiny: starsie ako 3 dni zmazane, mladsie ostali",
      sorted(x.price for x in s.query(PriceMinute).filter(PriceMinute.ts <= edge)), [2.0])
check("  aktualne minuty nedotknute (5 tikov na ticker)", s.query(PriceMinute).filter_by(symbol=S0).filter(PriceMinute.ts > edge).count(), 5)
check("  nova hodinova sviecka 11:00", s.query(PriceBar).filter_by(symbol=S0).count(), 2)

print("\n4) Vypadky")
before = s.query(PriceMinute).count()
state["markets_fail"] = True
FIXED["now"] = datetime(2026, 9, 14, 11, 1, 20, tzinfo=timezone.utc)
crashed = False
try:
    pp.poll_prices()
except Exception:
    crashed = True
s.expire_all()
check("/v2/markets zlyhal: tik nespadol, nic sa nezapisalo", (crashed, s.query(PriceMinute).count()), (False, before))
state["markets_fail"] = False; state["account_fail"] = True
FIXED["now"] = datetime(2026, 9, 14, 11, 2, 20, tzinfo=timezone.utc)
pp.poll_prices()
s.expire_all()
check("/v2/account zlyhal: minutove ceny sa zapisali aj tak", s.query(PriceMinute).count(), before + len(SYMS))

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
