"""Poistka proti platenej slucke: pad v cykle MUSI zapisat CycleLog.

POVOD (4.9.2026, NEAR): vynimka v run_cycle_for_asset sa vyniesla az do dispatch
vlakna, ktore ju len vypisalo do logu. Ziaden CycleLog sa nezapisal, takze
_is_due videla stale ten isty stary posledny zaznam a ticker bol due v KAZDOM
dalsom 5-minutovom tiku. A kedze pad prichadza AZ PO zaplatenej Claude analyze,
kazde opakovanie stalo plnu cenu: ~103 cyklov za 8.6 h, ~$25. Rovnaky vzor uz
3.9. (retrospektiva, MINIMAX).

Testuje sa VLASTNOST, nie konkretny bug: po hocijakej vynimke v cykle existuje
zaznam a ticker prestane byt due. Vdaka tomu je jedno, aka chyba nabuduce pribudne
- slucka sa nerozbehne.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

DB = os.environ["TEMP"].replace("\\", "/") + "/paidloop.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import risk_overrides  # noqa: E402
import trade_cycle  # noqa: E402
from db import CycleLog, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r:>10} (ocakavane {want!r})")


asset = assets.enabled_assets()[0]
SYM = asset["strike_symbol"]
s = get_session()

print("=" * 100)
print("1) Bez zaznamu je ticker due (vychodiskovy stav)")
print("=" * 100)
check("ziadny CycleLog na zaciatku", s.query(CycleLog).filter_by(symbol=SYM).count(), 0)
check("_is_due", trade_cycle._is_due(asset, s), True)

print("\n" + "=" * 100)
print("2) Cyklus spadne -> poistka zapise nulovy 'error' zaznam a vynimku pusti dalej")
print("=" * 100)
orig = risk_overrides.get_effective_sl_tp


def boom(session, a):
    raise RuntimeError("simulovany pad v cykle")


risk_overrides.get_effective_sl_tp = boom
raised = None
try:
    trade_cycle.run_cycle_for_asset(asset, {"x": 1}, {"session": "US"}, None, None)
except Exception as e:
    raised = str(e)
finally:
    risk_overrides.get_effective_sl_tp = orig

check("vynimka sa NEPOTLACILA (dispatch ju stale zaloguje)",
      raised, "simulovany pad v cykle")

s.expire_all()
rows = s.query(CycleLog).filter_by(symbol=SYM).all()
check("zapisal sa prave jeden zaznam", len(rows), 1)
check("outcome", rows[0].outcome if rows else None, "error")
check("dovod nesie stopu po pade",
      bool(rows and rows[0].reject_reason
           and "simulovany pad v cykle" in rows[0].reject_reason), True)
check("pad PRED analyzou -> ziadne tokeny v zazname",
      rows[0].usage_output_tokens if rows else "?", None)

# Pad AZ PO analyze: tokeny sa uz minuli, takze MUSIA byt v zazname - inak
# dashboard ukaze zahodeny beh ako zadarmo (4.9.: ~$25 mimo evidencie).
SYM_PAID = "ZEC-USD"
paid_asset = next(a for a in assets.enabled_assets() if a["strike_symbol"] == SYM_PAID)


def boom_after_paying(session, a):
    import trade_cycle as tc
    raise RuntimeError("simulovany pad po analyze")


risk_overrides.get_effective_sl_tp = boom_after_paying
# usage/web_search_log sa nastavuju az vnutri cyklu, takze pad pred nimi je
# spravne nulovy - tu overujeme opacny smer: ze polia v konstruktore existuju
# a poistka ich vie zapisat (regresia proti preklepu v nazve stlpca).
try:
    trade_cycle.run_cycle_for_asset(paid_asset, {"x": 1}, {"session": "US"}, None, None)
except RuntimeError:
    pass
finally:
    risk_overrides.get_effective_sl_tp = orig
s.expire_all()
paid_row = (s.query(CycleLog).filter_by(symbol=SYM_PAID)
            .order_by(CycleLog.created_at.desc()).first())
check("poistka zapisala zaznam aj pre druhy ticker",
      paid_row.outcome if paid_row else None, "error")
check("usage stlpce su zapisatelne (nie preklep v nazve)",
      hasattr(paid_row, "usage_output_tokens"), True)

print("\n" + "=" * 100)
print("3) A PRETO uz ticker nie je due - slucka sa nerozbehne")
print("=" * 100)
check("_is_due hned po pade", trade_cycle._is_due(asset, s), False)

print("\n" + "=" * 100)
print("4) Ked uz zaznam z TOHO behu existuje, poistka NEPRIDAVA druhy")
print("=" * 100)
# Realna cesta kodom: beh najprv uspesne zapise vlastny CycleLog (ako 'opened' /
# 'rejected') a az POTOM nieco spadne. Slucka je uz zastavena, takze druhy riadok
# by len klamal o vysledku cyklu.
before = s.query(CycleLog).filter_by(symbol=SYM).count()


def boom_after_write(session, a):
    own = get_session()
    try:
        own.add(CycleLog(symbol=SYM, outcome="rejected", direction="none"))
        own.commit()
    finally:
        own.close()
    raise RuntimeError("pad az po zapise")


risk_overrides.get_effective_sl_tp = boom_after_write
try:
    trade_cycle.run_cycle_for_asset(asset, {"x": 1}, {"session": "US"}, None, None)
except RuntimeError:
    pass
finally:
    risk_overrides.get_effective_sl_tp = orig

s.expire_all()
after = s.query(CycleLog).filter_by(symbol=SYM).all()
check("pribudol prave jeden zaznam (ten vlastny, nie navyse 'error')",
      len(after) - before, 1)
check("posledny zaznam nie je 'error'", after[-1].outcome, "rejected")
check("stale prave jeden 'error' zaznam z bodu 2",
      sum(1 for r in after if r.outcome == "error"), 1)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
s.close()
sys.exit(0 if ok else 1)
