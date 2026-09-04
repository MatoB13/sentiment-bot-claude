"""Testy performance_facts (bod 4): riadky sa ukazu len pri dost velkom n, cache, prompt, schemy."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/pf.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import performance_facts as pf  # noqa: E402
import trade_cycle  # noqa: E402
from db import CycleLog, PriceBar, Trade, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<70} {str(got)[:18]:>18} (ocakavane {want!r})")


s = get_session()
now = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
naive = now.replace(tzinfo=None)


def seed(symbol, n, win, direction="Long", adx=30.0, src="scheduled", mom_up=True, conf=55, days_ago=5,
         opened_at=None):
    for i in range(n):
        # 8 h odstup, aby sa bary h0/h0-4h roznych obchodov neprekryvali
        op = opened_at or (naive - timedelta(days=days_ago, hours=8 * i))
        h0 = op.replace(minute=0, second=0, microsecond=0)
        t = Trade(symbol=symbol, direction=direction, confidence=conf, entry_price=100.0, stop_loss_price=98.0,
                  notional_usd=1000.0, margin_usd=100.0, opened_at=op, closed_at=op + timedelta(hours=3),
                  status="closed_by_exchange", close_reason="take_profit" if win else "stop_loss",
                  pnl_usd=30.0 if win else -20.0, dry_run=False)
        s.add(t)
        s.flush()
        s.add(CycleLog(symbol=symbol, trade_id=t.id, outcome="opened", ta={"adx14": adx}, trigger_source=src,
                       created_at=op))
        # bary pre 4h momentum: c4 -> c0 rastie ak mom_up
        for hs, px in ((h0 - timedelta(hours=4), 99.0), (h0, 100.0 if mom_up else 98.0)):
            if not s.query(PriceBar).filter_by(symbol=symbol, hour_start=hs).first():
                s.add(PriceBar(symbol=symbol, hour_start=hs, open=px, high=px, low=px, close=px))
    s.commit()


print("=" * 110)
print("1) MALA VZORKA - ziadny riadok pod prahom, ticker 'malo na zaver'")
print("=" * 110)
seed("ADA-USD", 4, True)
seed("BTC-USD", 4, False, direction="Short")
pf.clear_cache()
txt = pf.get_text(s, "ADA-USD", "ADA", now)
check("hlavicka bloku", txt.startswith("## Tvoja doterajšia výkonnosť"), True)
check("portfolio riadok (8 obchodov)", "8 obchodov" in txt, True)
check("ziadny riadok 'smer long' (n=4 < 20)", "smer long" in txt, False)
check("ticker ADA: malo na zaver (4 < 10)", "ADA (tento ticker): 4 obchodov v okne - málo na záver" in txt, True)
check("kalibracia: zatial 0 na aktualnej skale", "zatiaľ 0 obchodov na aktuálnej škále" in txt, True)

print()
print("=" * 110)
print("2) VELKA VZORKA - riadky sa objavia, cisla sedia")
print("=" * 110)
seed("ADA-USD", 8, True, adx=30.0, src="watch")        # ADA spolu 12 (10 win)
seed("ZEC-USD", 14, False, direction="Short", adx=15.0, src="scheduled", mom_up=True)  # short proti pohybu
pf.clear_cache()
f = pf.compute(s, "ADA-USD", now)
check("portfolio n (4+4+8+14)", f["portfolio"]["n"], 30)
check("ticker ADA n", f["ticker"]["n"], 12)
check("trending n (adx 30: ADA 12 + BTC 4)", f["regime"]["trending"]["n"], 16)
check("weak_no_trend n (adx 15)", f["regime"]["weak_no_trend"]["n"], 14)
check("short n (BTC 4 + ZEC 14)", f["direction"]["short"]["n"], 18)
check("momentum against (shorty pri raste: 18)", f["momentum"]["against"]["n"], 18)
check("momentum with (ADA long pri raste: 12)", f["momentum"]["with"]["n"], 12)
txt = pf.get_text(s, "ADA-USD", "ADA", now)
check("riadok 'smer long' skryty (12 < 20)", "smer long" in txt, False)
check("riadok 'smer short' (18) skryty (<20)", "smer short" in txt, False)
check("ticker ADA riadok (12 >= 10) s win 100 %", "ADA (tento ticker): 12 obchodov, win 100 %" in txt, True)
check("portfolio win 40 % (12/30)", "30 obchodov, win 40 %" in txt, True)
seed("BTC-USD", 6, True, direction="Short", adx=22.0)   # short spolu 24 -> riadok sa ukaze
pf.clear_cache()
txt = pf.get_text(s, "BTC-USD", "BTC", now)
check("riadok 'smer short' (24 >= 20) sa ukaze", "smer short: 24 obchodov" in txt, True)
check("'developing' (6 < 20) skryty", "developing" in txt, False)

print()
print("=" * 110)
print("3) KALIBRACIA na novej skale + posledne 48 h")
print("=" * 110)
seed("NEAR-USD", 22, True, conf=72, opened_at=naive - timedelta(hours=20))
pf.clear_cache()
f = pf.compute(s, "NEAR-USD", now)
check("kalibracia n (22 od 4.9.)", f["calibration"]["n"], 22)
txt = pf.get_text(s, "NEAR-USD", "NEAR", now)
check("kalibracna tabulka sa ukaze", "Kalibrácia tvojej confidence (od 04.09., n=22): conf 70-79: 22 / win 100 %" in txt, True)
check("posledne 48 h riadok", "posledných 48 h: 22 obchodov" in txt, True)

print()
print("=" * 110)
print("4) CACHE - druhy dotaz do 15 min nejde do DB")
print("=" * 110)
pf.clear_cache()
pf.compute(s, "ADA-USD", now)
seed("ADA-USD", 3, True)   # nove obchody po naplneni cache
f2 = pf.compute(s, "ADA-USD", now + timedelta(minutes=5))
check("cache: n sa nezmenil (cachovane)", f2["ticker"]["n"], 12)
f3 = pf.compute(s, "ADA-USD", now + timedelta(minutes=16))
check("po 16 min: prepocitane", f3["ticker"]["n"], 15)

print()
print("=" * 110)
print("5) PROMPT + SCHEMY")
print("=" * 110)
A = assets.enabled_assets()[0]
ta = {"last_price": 100.0, "atr14": 2.0, "recent_candles": []}
p = ca._build_user_prompt(A, ta, {}, {}, [], None, None, performance_facts=txt)
check("blok faktov je v user prompte", "## Tvoja doterajšia výkonnosť" in p, True)
check("stary blok 'Priebežné zhrnutie' nie je", "Priebežné zhrnutie" in p, False)
check("stary 48h blok nie je", "Výkonnosť CELÉHO portfólia" in p, False)
p2 = ca._build_user_prompt(A, ta, {}, {}, [], None, None, performance_facts=None, new_stats_text="Za 2026-09-04: 3 cykly.")
check("stats blok bez summary_reflection v nadpise", "vygeneruj daily_reflection)" in p2 and "summary_reflection" not in p2, True)
for tool in (ca.DECISION_TOOL, ca.POSITION_HEALTH_TOOL):
    check(f"{tool['name']}: summary_reflection prec", "summary_reflection" in json.dumps(tool), False)
    check(f"{tool['name']}: daily_reflection zostava", "daily_reflection" in tool["input_schema"]["properties"], True)
sys_text = " ".join(b["text"] for b in ca._system_prompt_blocks(A))
check("system prompt bez summary_reflection", "summary_reflection" in sys_text, False)
check("trade_cycle nema _get_portfolio_recent_performance", hasattr(trade_cycle, "_get_portfolio_recent_performance"), False)

s.close()
print()
print("=" * 110)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 110)
sys.exit(0 if ok else 1)
