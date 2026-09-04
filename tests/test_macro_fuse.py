"""Test poistky vo watch_monitor: makro cyklus bezi LEN tickerom bez zivej
watch urovne. Bezi na docasnej sqlite DB, bez siete."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/fuse.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import watch_monitor  # noqa: E402
from db import CycleLog, Trade, get_session  # noqa: E402

now = datetime.now(timezone.utc)
act = assets.enabled_assets()
a_watch, a_none, a_pos, a_stale = act[0], act[1], act[2], act[3]

s = get_session()


def log(asset, **kw):
    s.add(CycleLog(symbol=asset["strike_symbol"], created_at=now - timedelta(minutes=10), **kw))


# 1. ma obojstranny watch -> preskocit
log(a_watch, watch_price=100.0, watch_direction="above",
    watch_price_2=90.0, watch_direction_2="below")
# 2. ziadny watch -> bezat
log(a_none, direction="none")
# 3. otvorena pozicia + watch patriaci PRAVE jej -> preskocit
t = Trade(symbol=a_pos["strike_symbol"], status="open", direction="Long",
          entry_price=100.0, opened_at=now - timedelta(hours=1))
s.add(t)
s.flush()
log(a_pos, trade_id=t.id, watch_price=105.0, watch_direction="above")
# 4. otvorena pozicia, ale watch je z cyklu PRED nou (iny trade_id) -> bezat
t2 = Trade(symbol=a_stale["strike_symbol"], status="open", direction="Short",
           entry_price=50.0, opened_at=now - timedelta(hours=1))
s.add(t2)
s.flush()
log(a_stale, trade_id=None, watch_price=55.0, watch_direction="above")
s.commit()

print("=" * 88)
print("POISTKA: koho makro udalost este zobudi a koho uz nie")
print("=" * 88)
cases = [
    (a_watch, False, "obojstranny watch, bez pozicie"),
    (a_none, True, "ziadna watch uroven"),
    (a_pos, False, "otvorena pozicia + watch TEJTO pozicie"),
    (a_stale, True, "otvorena pozicia, watch z cyklu pred nou (zastarany)"),
]
ok = True
for asset, should_run, label in cases:
    has = watch_monitor._has_live_watch(s, asset)
    runs = not has
    mark = "OK " if runs == should_run else "CHYBA"
    if runs != should_run:
        ok = False
    print(f"  {mark} {asset['name']:<8} {'BEZI':<6} = {runs!s:<5} (ocakavane {should_run})"
          f"   {label}")

print()
print("  Posledny riadok je dolezity: kopiruje presne to, co robi")
print("  _check_price_watch_for_assets - zastarany watch z cyklu pred otvorenim")
print("  pozicie poller ignoruje, takze ho nesmie uznat ani poistka (inak by")
print("  ticker cakal na uroven, ktoru nikto nesleduje).")
s.close()
print()
print("=" * 88)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 88)
sys.exit(0 if ok else 1)
