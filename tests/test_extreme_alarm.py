"""ALARM NA EXTREMNY POHYB (extreme_alarm.py) + regresia ostatnych triggerov (2026-09-12).
Burza a Claude su podvrhnute - test nic neplati a nic neposiela."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/xalarm.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import claude_analyst  # noqa: E402
import config  # noqa: E402
import discord_client  # noqa: E402
import extreme_alarm as xa  # noqa: E402
import strike_client  # noqa: E402
import trade_cycle  # noqa: E402
from db import AlarmTrigger, PriceBar, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<66} {got!r} (ocakavane {want!r})")


config.EXTREME_ALARM_ENABLED = True
config.EXTREME_ALARM_1H_ATR, config.EXTREME_ALARM_4H_ATR = 4.0, 7.0
config.EXTREME_ALARM_MIN_1H_PCT, config.EXTREME_ALARM_MIN_4H_PCT = 3.0, 5.0
config.EXTREME_ALARM_COOLDOWN_HOURS, config.EXTREME_ALARM_MAX_PER_HOUR = 12, 10

print("1) Ciste vyhodnotenie")
check("bezny pohyb (1 ATR) -> nic", xa.evaluate(101, 100, 100, 1.0), None)
a = xa.evaluate(105, 100, 100, 1.0)
check("5 ATR a 5 % za hodinu -> alarm nahor", (a["direction"], round(a["move_1h_atr"], 1)), ("up", 5.0))
check("crash -8 % / 8 ATR za 4 h -> alarm nadol", xa.evaluate(92, 99, 100, 1.0)["direction"], "down")
check("AKCIA pri otvoreni: 4 ATR, ale len 2 % -> NIC (bezny otvaraci pohyb)", xa.evaluate(102, 100, 100, 0.5), None)
check("7 ATR za 4 h, ale len 4 % -> nic", xa.evaluate(104, 104, 100, 0.55), None)
check("bez ATR (novy ticker) -> nic", xa.evaluate(110, 100, 100, None), None)
check("bez referencnych cien -> nic", xa.evaluate(110, None, None, 1.0), None)

print("\n2) Referencna cena: minutove vzorky, po restarte hodinove sviecky")
now = datetime.now(timezone.utc)
xa._buffer.clear()
for m in range(300, -1, -1):
    xa.record_price("X-USD", now - timedelta(minutes=m), 100 + (300 - m) * 0.01)
check("cena pred 60 min z pamate", round(xa._price_at("X-USD", now - timedelta(minutes=60)), 2), 102.4)
check("mimo tolerancie -> None", xa._price_at("NIC-USD", now - timedelta(minutes=60)), None)

# DB: hodinove sviecky pre ATR a fallback
s = get_session()
sym = assets.enabled_assets()[0]["strike_symbol"]
name0 = assets.enabled_assets()[0]["name"]
base = now.astimezone(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
for h in range(40, 0, -1):
    s.add(PriceBar(symbol=sym, hour_start=base - timedelta(hours=h), open=100.0, high=100.5, low=99.5, close=100.0))
s.commit()
atr = xa.atr14(s, sym, now)
check("ATR14 z uzavretych sviecok (~1.0)", round(atr, 2), 1.0)
check("fallback 1 h = open sviecky spred 1-2 h", xa._bar_open(s, sym, now, 1), 100.0)

print("\n3) Cely tik: spustenie, cooldown, strop, otvorena pozicia")
dispatched = []
trade_cycle.dispatch_triggered_check = lambda asset, **kw: dispatched.append((asset["name"], kw))
trade_cycle.is_triggered_check_in_flight = lambda symbol: False
notified = []
discord_client.notify_extreme_alarm = lambda *a, **k: notified.append(a) or True
xa._buffer.clear()
prices = {sym: 110.0}
strike_client.get_markets = lambda: [{"symbol": k, "mark_price": str(v)} for k, v in prices.items()]
# zaznam do DB musi existovat UZ v case spustenia (poistka proti platenej slucke)
seen_rows = []
orig_dispatch = trade_cycle.dispatch_triggered_check
def dispatch_and_peek(asset, **kw):
    ss = get_session(); seen_rows.append(ss.query(AlarmTrigger).count()); ss.close()
    orig_dispatch(asset, **kw)
trade_cycle.dispatch_triggered_check = dispatch_and_peek
xa.check_extreme_moves()
check("+10 % a 10 ATR -> jeden mimoriadny cyklus", [d[0] for d in dispatched], [name0])
check("  s popisom alarmu (smer nahor)", dispatched[0][1]["alarm"]["direction"], "up")
check("  zaznam v alarm_triggers existoval PRED spustenim", seen_rows, [1])
check("  Discord sprava", len(notified), 1)
xa.check_extreme_moves()
check("o minutu znova -> cooldown, nic nove", len(dispatched), 1)
# prebiehajuci beh
s.query(AlarmTrigger).delete(); s.commit()
trade_cycle.is_triggered_check_in_flight = lambda symbol: True
xa.check_extreme_moves()
check("prebiehajuci mimoriadny beh -> nespusti ani nezapise", (len(dispatched), s.query(AlarmTrigger).count()), (1, 0))
trade_cycle.is_triggered_check_in_flight = lambda symbol: False
# globalny strop
for i in range(10):
    s.add(AlarmTrigger(symbol=f"Z{i}-USD", direction="up", dispatched=True,
                       triggered_at=now.replace(tzinfo=None) - timedelta(minutes=5)))
s.commit()
xa.check_extreme_moves()
row = s.query(AlarmTrigger).filter(AlarmTrigger.symbol == sym).one()
check("globalny strop 10/h -> zapisane, ale NEspustene", (len(dispatched), row.dispatched), (1, False))
# otvorena pozicia
s.query(AlarmTrigger).delete(); s.commit()
s.add(Trade(symbol=sym, direction="Long", status="open", entry_price=100, opened_at=now.replace(tzinfo=None),
            expires_at=now.replace(tzinfo=None) + timedelta(hours=24)))
s.commit()
xa.check_extreme_moves()
check("otvorena pozicia -> alarm mlci (chrani ju SL/health check)", len(dispatched), 1)
config.EXTREME_ALARM_ENABLED = False
s.query(Trade).delete(); s.commit()
xa.check_extreme_moves()
check("EXTREME_ALARM_ENABLED=false -> nic", len(dispatched), 1)
config.EXTREME_ALARM_ENABLED = True
strike_client.get_markets = lambda: (_ for _ in ()).throw(RuntimeError("503"))
crashed = False
try:
    xa.check_extreme_moves()
except Exception:
    crashed = True
check("vypadok /v2/markets -> tik nespadne", crashed, False)

print("\n4) Napojenie na cyklus (trade_cycle) - a REGRESIA ostatnych triggerov")
check("trigger_source alarm", trade_cycle._trigger_source(alarm={"direction": "up"}), "alarm")
check("REGRESIA: watch", trade_cycle._trigger_source(watch_triggered=True), "watch")
check("REGRESIA: makro ma prednost", trade_cycle._trigger_source(macro_event="CPI", alarm={"x": 1}), "macro")
check("REGRESIA: post_close", trade_cycle._trigger_source(closed_trade={"a": 1}), "post_close")
check("REGRESIA: planovany", trade_cycle._trigger_source(), "scheduled")
note = trade_cycle._alarm_note({"direction": "down", "move_1h_atr": -5.2, "move_1h_pct": -6.1,
                                "move_4h_atr": None, "move_4h_pct": None, "price": 92.5})
check("popis pre Clauda", note, "Cena sa pohla NADOL: za ~1 h -5.2 ATR(1h) (-6.1 %). Aktuálna cena pri alarme: 92.5.")
check("bez alarmu ziadny popis", trade_cycle._alarm_note(None), None)
src = open(os.path.join(_ROOT, "trade_cycle.py"), encoding="utf-8").read()
check("lacny sken sa pri alarme preskakuje (ide rovno plny cyklus)", "and not alarm and new_stats_text is None" in src, True)
check("watch brany ostavaju len pre watch (alarm ich neobchadza ani nespusta)",
      src.count("if watch_triggered:\n            # 2026-09-04 - MRTVE PASMO") == 1, True)

print("\n5) Prompt: vyvazeny blok, len pri alarme")
A = next(a for a in assets.ALL_ASSETS if a["name"] == "BTC")
ta = {"last_price": 100.0, "atr14": 1.0}
p = claude_analyst._build_user_prompt(A, ta, {}, {"session": "US"}, [], None, None, None, None, None,
                                      None, None, None, alarm_note=note)
check("blok o extremnom pohybe je v prompte", "Mimoriadny cyklus: extrémny pohyb ceny" in p, True)
check("  obsahuje cisla pohybu", "-5.2 ATR(1h)" in p, True)
check("  je vyvazeny (nie je signal na vstup ani na cakanie)", "Nie je to signál na vstup" in p and "špičku" in p, True)
p0 = claude_analyst._build_user_prompt(A, ta, {}, {"session": "US"}, [], None, None, None, None, None, None, None, None)
check("REGRESIA: bez alarmu blok chyba", "extrémny pohyb" in p0, False)
import re  # noqa: E402
_strip_time = lambda x: re.sub(r"\(\d{4}-\d{2}-\d{2}T[^)]*\)", "", x)  # noqa: E731 - mikrosekundy v hlavicke
check("REGRESIA: prompt bez alarmu je identicky s volanim bez parametra (okrem casovej peciatky)",
      _strip_time(p0) == _strip_time(claude_analyst._build_user_prompt(
          A, ta, {}, {"session": "US"}, [], None, None, None, None, None, None, None, None, alarm_note=None)), True)

s.close()
print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
