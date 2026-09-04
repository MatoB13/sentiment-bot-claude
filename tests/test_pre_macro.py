"""E2E test noveho pre-makro bloku + watch_monitor poistky (bez siete/DB)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/pm.db"
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst  # noqa: E402
import macro_calendar  # noqa: E402
import trade_cycle  # noqa: E402

now = datetime.now(timezone.utc)
asset = assets.enabled_assets()[0]

print("=" * 88)
print("1) _next_scheduled_run vracia bod NA MRIEZKE a v buducnosti")
print("=" * 88)
nxt = trade_cycle._next_scheduled_run(asset, now)
iv = trade_cycle._required_interval_hours(asset, now)
print(f"  {asset['name']}: interval {iv}h, slot {asset['run_slot']} "
      f"(+{asset.get('run_slot_hour_offset') or 0}h)")
print(f"  teraz {now:%Y-%m-%d %H:%M} UTC  ->  dalsi planovany {nxt:%Y-%m-%d %H:%M} UTC "
      f"(o {(nxt-now).total_seconds()/3600:.2f}h)")
assert nxt > now, "dalsi beh musi byt v buducnosti"
assert (nxt - now).total_seconds() / 3600 <= iv + 0.01, "nesmie byt dalej nez jeden interval"
prev = trade_cycle._slot_due_point(now, iv, asset["run_slot"],
                                   asset.get("run_slot_hour_offset") or 0)
assert abs((nxt - prev).total_seconds() / 3600 - iv) < 0.01, "musi byt presne interval od minuleho bodu"
print("  OK - je na mriezke a presne jeden interval od predchadzajuceho bodu")

print()
print("=" * 88)
print("2) macro_calendar.get_upcoming_events pozera DOPREDU")
print("=" * 88)
up = macro_calendar.get_upcoming_events(now, now + timedelta(days=40))
print(f"  najblizsich 40 dni: {len(up)} udalosti")
for e in up[:4]:
    print(f"    {e['name']:<6} {e['datetime_utc']:%Y-%m-%d %H:%M} UTC")
assert all(e["datetime_utc"] > now for e in up), "ziadna nesmie byt v minulosti"
back = macro_calendar.get_upcoming_events(now, now)
assert back == [], "prazdne okno musi dat prazdny zoznam"
print("  OK - vsetky su v buducnosti, prazdne okno dava prazdny zoznam")

print()
print("=" * 88)
print("3) Prompt blok sa vlozi (otvaraci cyklus) a ma spravny obsah")
print("=" * 88)
ev = [{"name": "CPI", "datetime_utc": now + timedelta(hours=3, minutes=30)}]
ta = {"last_price": 100.0, "atr14": 2.0, "rsi14": 55, "trend": "neutral"}


def build(pre):
    return claude_analyst._build_user_prompt(
        asset, ta, {}, {"session": "US"}, [], None, None, pre_macro_events=pre)


with_ev = build(ev)
without = build(None)
assert "POSLEDNÝ plánovaný cyklus" in with_ev, "blok chyba"
assert "POSLEDNÝ plánovaný cyklus" not in without, "blok sa objavil aj bez udalosti"
assert "CPI" in with_ev and "3.5h od teraz" in with_ev, "detail udalosti chyba"
assert "watch_price_2" in with_ev, "obojstrannost sa nespomina"
assert "Event Risk Gate" in with_ev, "vztah k Event Risk Gate chyba"
i = with_ev.index("POSLEDNÝ plánovaný cyklus")
print("  --- vlozeny blok ---")
for ln in with_ev[i - 3:i + 900].splitlines()[:16]:
    print("   ", ln)
print(f"\n  OK - blok pridal {len(with_ev)-len(without)} znakov, bez udalosti sa nevlozi nic")

print()
print("=" * 88)
print("4) Mnozne cislo pri dvoch udalostiach")
print("=" * 88)
two = build(ev + [{"name": "NFP", "datetime_utc": now + timedelta(hours=4)}])


def head(txt):
    """Len nadpis bloku - slovo 'udalosťou' sa vyskytuje aj inde v prompte."""
    i = txt.index("## Toto je POSLEDNÝ")
    return txt[i:txt.index("\n", i)]


assert "udalosťami" in head(two), head(two)
assert "udalosťou" in head(with_ev), head(with_ev)
assert "NFP" in two and "CPI" in two
print(f"  jedna udalost:  {head(with_ev)}")
print(f"  dve udalosti:   {head(two)}")
print("  OK - mnozne cislo sedi, obe udalosti vymenovane")

print()
print("=" * 88)
print("5) Health-check vetva promptu blok tiez dostane")
print("=" * 88)
op = {"direction": "Long", "entry_price": 100.0, "live_price": 101.0,
      "stop_loss_price": 97.0, "take_profit_price": 105.0, "leverage": 3,
      "opened_at_str": "2026-09-02 10:00", "hours_held": 2.0,
      "unrealized_pnl_usd": 5.0, "unrealized_pnl_pct": 10.0}
hp = claude_analyst._build_user_prompt(asset, ta, {}, {"session": "US"}, [], None, None,
                                       open_position=op, pre_macro_events=ev)
assert "POSLEDNÝ plánovaný cyklus" in hp, "blok chyba v health vetve"
assert "OTVORENÁ POZÍCIA" in hp, "health blok sa stratil"
print("  OK - blok je aj pri otvorenej pozicii, health blok ostal")

print()
print("=" * 88)
print("VSETKY TESTY PRESLI")
print("=" * 88)
