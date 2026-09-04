"""Testy skalovania marze confidence (risk_manager.validate_and_size)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/cs.db"
sys.path.insert(0, _ROOT)

import claude_analyst  # noqa: E402
import risk_manager  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>12} (ocakavane {want!r})")


MM = {
    "order_tick_price": 0.01, "order_market_step_size": 0.0001,
    "order_market_min_size": 0.0001, "order_market_max_size": 1e6,
    "order_min_notional": 10,
    "margin_tiers": [{"max_notional": 100000, "max_leverage": 20,
                      "maintenance_margin_rate": 0.005}],
}


def size(conf, margin=100.0, min_conf=50, min_notional=10):
    mm = {**MM, "order_min_notional": min_notional}
    return risk_manager.validate_and_size(
        {"direction": "long", "confidence": conf, "stop_loss_price": 97.0,
         "take_profit_price": 105.0, "reasoning": "test"},
        has_open_position=False, live_price=100.0, market_meta=mm,
        min_confidence=min_conf, sl_pct=3.0, tp_pct=4.5,
        cushion_multiple=1.5, margin_usd=margin)


print("=" * 92)
print("1) MARZA SA SKALUJE LINEARNE CONFIDENCE")
print("=" * 92)
print(f"  {'confidence':>10} {'marza':>9} {'notional':>10} {'paka':>6}")
prev = 0
for conf in (50, 60, 70, 85, 100):
    r = size(conf)
    print(f"  {conf:>10} ${r['margin_usd']:>8.2f} ${r['notional_usd']:>9.2f} "
          f"{r['leverage']:>5}x")
    if r["margin_usd"] <= prev:
        ok = False
    prev = r["margin_usd"]
check("marza rastie s confidence", prev > 0, True)
check("confidence 100 = plna konfigurovana marza", round(size(100)["margin_usd"]), 100)
check("confidence 50 = polovica", round(size(50)["margin_usd"]), 50)
check("confidence 70 = 70 %", round(size(70)["margin_usd"]), 70)

print()
print("=" * 92)
print("2) PRAH STALE PLATI (skalovanie ho nenahradza)")
print("=" * 92)
try:
    size(49, min_conf=50)
    check("confidence pod prahom sa zamietne", "nezamietlo", "RejectedTrade")
except risk_manager.RejectedTrade as e:
    check("confidence pod prahom sa zamietne", "RejectedTrade", "RejectedTrade")
    print(f"       dovod: {e}")

print()
print("=" * 92)
print("3) BURZOVE MINIMUM - mala marza nesmie prejst ticho")
print("=" * 92)
# marza $50, confidence 50 -> $25 marze; pri pake ~20 je notional ~$500
r = size(50, margin=50.0)
print(f"  marza $50, confidence 50 -> marza ${r['margin_usd']:.2f}, "
      f"notional ${r['notional_usd']:.2f}")
check("mala pozicia je stale nad beznym minimom", r["notional_usd"] > 10, True)
try:
    size(50, margin=50.0, min_notional=1000)
    check("pod burzovym minimom sa zamietne", "nezamietlo", "RejectedTrade")
except risk_manager.RejectedTrade as e:
    check("pod burzovym minimom sa zamietne", "RejectedTrade", "RejectedTrade")
    print(f"       dovod: {str(e)[:95]}")

print()
print("=" * 92)
print("4) SL/TP SA SKALOVANIM NEMENIA (riziko na obchod klesa s marzou, nie SL)")
print("=" * 92)
a, b = size(50), size(100)
check("stop_loss rovnaky", a["stop_loss_price"], b["stop_loss_price"])
check("take_profit rovnaky", a["take_profit_price"], b["take_profit_price"])
print(f"       SL {a['stop_loss_price']}  TP {a['take_profit_price']} pri oboch")

print()
print("=" * 92)
print("5) CLAUDE O VZORCI NEVIE")
print("=" * 92)
import assets  # noqa: E402
asset = assets.enabled_assets()[0]
p = claude_analyst._build_user_prompt(
    asset, {"last_price": 100.0, "atr14": 2.0}, {}, {"session": "US"}, [], None, None)
sysblocks = " ".join(str(b) for b in claude_analyst._system_prompt_blocks(asset))
whole = p + sysblocks + str(claude_analyst.DECISION_TOOL)
check("prah nie je v prompte", str(asset["min_confidence"]) in p, False)
for term in ("confidence/100", "margin_usd *", "marža = ", "skaluje"):
    check(f"vzorec '{term}' nie je nikde v prompte", term in whole, False)
check("ale 'reálne peniaze' tam je", "za živé peniaze" in p, True)
# Rozhodnutie pouzivatela (4.9.): spravanie pri direction=none je uz spravne
# (cim vyssia confidence, tym skor watch - 63 % v pasme 20-29 vs 91 % v 40-49),
# takze sa do promptu ZAMERNE nic nedopĺňa. Test to strazi, aby to niekto
# neskor "neopravil" spat.
check("k direction=none sa v prompte nic nedopisuje", "PRI direction=none" in whole, False)
check("stara zmienka o prahu je z popisu confidence prec",
      "nedrž sa umelo" in whole, False)

print()
print("=" * 92)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 92)
sys.exit(0 if ok else 1)
