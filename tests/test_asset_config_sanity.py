"""Zdravy rozum v konfiguracii tickerov - hlavne obchodne hodiny voci trhu.

POVOD (2026-09-06, pri aktivacii ZHIPU): ZHIPU aj UNITREE su cinske firmy zo
sanghajskeho STAR Marketu, ale obe bezali na ZDIELANOM NYSE defaulte
(13-21 UTC). Cela ich realna seansa (01:30-07:00 UTC) tak padala do OFF-HOURS -
ticker sa kontroloval v 6-hodinovom intervale prave vtedy, ked sa jeho
podkladova akcia obchodovala, a v 4-hodinovom vtedy, ked bola burza zatvorena.
Komentar v config.py to pritom sam priznaval ("hruba aproximacia, prehodnotit
pri realnej aktivacii") a nikto to neprehodnotil, kym si toho nevsimol
pouzivatel.

Test nechodi na siet ani do DB - cita len konfiguraciu.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/acfg.db"
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import config  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


BY_NAME = {a["name"]: a for a in assets.ALL_ASSETS}

# Ticker -> (start, end) UTC seansy jeho SKUTOCNEHO trhu. Zamerne sa tu neuvadza
# cely zoznam: staci strazit tie, kde je nesulad realny a uz sa raz stal.
NON_NYSE_MARKETS = {
    "ZHIPU": (1, 7),      # Sanghaj STAR Market 09:30-15:00 CST
    "UNITREE": (1, 7),    # to iste
    "SKHYNIX": (0, 7),    # KRX 09:00-15:30 KST
}

print("1) Tickery na mimo-US burze nesmu pouzivat zdielany NYSE default")
print(f"   (zdielany default je {config.TRADING_HOURS_START_UTC}-{config.TRADING_HOURS_END_UTC} UTC)")
for name, (start, end) in NON_NYSE_MARKETS.items():
    a = BY_NAME[name]
    check(f"{name} zaciatok seansy", a["trading_hours_start_utc"], start)
    check(f"{name} koniec seansy", a["trading_hours_end_utc"], end)
    shared = (a["trading_hours_start_utc"] == config.TRADING_HOURS_START_UTC
              and a["trading_hours_end_utc"] == config.TRADING_HOURS_END_UTC)
    check(f"{name} NEma zdielany NYSE default", shared, False)

print("\n2) Obchodne hodiny kazdeho tickera davaju zmysel")
for a in assets.ALL_ASSETS:
    s_, e_ = a["trading_hours_start_utc"], a["trading_hours_end_utc"]
    if not (0 <= s_ <= 23 and 0 <= e_ <= 23 and s_ != e_):
        check(f"{a['name']} hodiny v rozsahu a nie su rovnake", (s_, e_), "0-23, start != end")

print("\n3) SL/TP kazdeho tickera je pouzitelne")
for a in assets.ALL_ASSETS:
    sl, tp = a["sl_pct"], a["tp_pct"]
    if not (sl and sl > 0):
        check(f"{a['name']} SL je kladne", sl, "> 0")
    # Podlaha pomeru je aj v risk_manager (MIN_REWARD_RISK_RATIO), ale keby ju
    # config porusoval uz v zaklade, kazdy obchod by sa ticho orezaval.
    if not (tp and tp >= sl):
        check(f"{a['name']} TP nie je tesnejsi nez SL", (sl, tp), "tp >= sl")

print("\n4) ZHIPU je pripraveny na aktivaciu (vsetko cez ENV prebijatelne)")
z = BY_NAME["ZHIPU"]
check("SL prepocitane z ATR (uz nie placeholder 6.0)", z["sl_pct"] != 6.0, True)
check("pomer TP/SL je 1.5", round(z["tp_pct"] / z["sl_pct"], 2), 1.5)
for var in ("ZHIPU_SL_PCT", "ZHIPU_TP_PCT", "ZHIPU_MARGIN_USD", "ZHIPU_LEVERAGE",
            "ZHIPU_MIN_CONFIDENCE", "ZHIPU_TRADE_INTERVAL_HOURS",
            "ZHIPU_OFF_HOURS_INTERVAL_HOURS", "ZHIPU_WEEKEND_INTERVAL_HOURS",
            "ZHIPU_TRADING_HOURS_START_UTC", "ZHIPU_TRADING_HOURS_END_UTC",
            "ZHIPU_LIQUIDATION_CUSHION_MULTIPLE", "ZHIPU_EFFORT"):
    check(f"config ma {var}", hasattr(config, var), True)

print("\n5) Ticker bez vlastneho zdroja dat sa NESMIE spoliehat na yfinance")
# ZHIPU/MINIMAX/UNITREE nie su na yfinance ani Binance/CoinGecko - jediny zdroj
# je vlastny 1-min Strike poller. Keby si niekto myslel, ze yf_symbol funguje,
# ocakaval by fallback, ktory neexistuje.
for name in ("ZHIPU", "MINIMAX", "UNITREE"):
    a = BY_NAME[name]
    check(f"{name} nema yf_fallback", a.get("yf_fallback"), None)
    check(f"{name} nema binance OHLC", a.get("binance_ohlc_symbol"), None)
    check(f"{name} nema coingecko", a.get("coingecko_id"), None)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
