"""Testy opravy objemu prebiehajucej sviecky.

Rekonstruuje presne situaciu ZEC #175 (3.9. 14:17 UTC): hodina 14:00 bola
hotova na 17/60 min, videny objem 2730, skutocny po dokonceni 14506.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/vf.db"
sys.path.insert(0, _ROOT)

import pandas as pd  # noqa: E402
import market_data  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>12} (ocakavane {want!r})")


# --- realne ZEC hodiny 3.9. (Binance ZECUSDT), posledna = prebiehajuca 14:00
CLOSES = [828.5, 829.2, 833.1, 845.6, 847.0, 856.4, 860.5]
VOLS = [3969, 3969, 2775, 8502, 7479, 9336, 2730]   # 2730 = 17 min z hodiny 14:00
# 260 barov, aby vysli aj ema200/MACD - realne hodiny su na konci
N = 260
idx = pd.date_range("2026-08-24 00:00", periods=N, freq="h")
closes = [830.0] * (N - len(CLOSES)) + CLOSES
vols = [8000.0] * (N - len(VOLS)) + [float(v) for v in VOLS]
df = pd.DataFrame({"open": closes, "high": [c * 1.002 for c in closes],
                   "low": [c * 0.998 for c in closes], "close": closes,
                   "volume": vols}, index=idx)
NOW = idx[-1].to_pydatetime() + timedelta(minutes=17)


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.replace(tzinfo=tz) if tz else NOW


print("=" * 94)
print("1) RATIO SA POCITA Z DOKONCENEJ SVIECKY, NIE Z PREBIEHAJUCEJ")
print("=" * 94)
with mock.patch.object(market_data, "datetime", FakeDT):
    ta = market_data.compute_indicators(df, include_volume=True)

baseline = sum(vols[-22:-2]) / 20
print(f"  priemer 20 dokoncenych pred poslednou dokoncenou: {baseline:.0f}")
print(f"  posledna DOKONCENA hodina (13:00): {vols[-2]:.0f}")
print(f"  prebiehajuca (14:00, 17 min):      {vols[-1]:.0f}")
expected = round(vols[-2] / baseline, 2)
check("ratio je z dokoncenej 13:00, nie z prebiehajucej",
      ta["last_candle_volume_vs_avg20_ratio"], expected)
print(f"       (stary kod by dal {round(vols[-1]/ (sum(vols[-21:-1])/20), 2)} "
      f"-> vyzeralo to ako vycerpanie)")
check("ratio je nad 1.0, cize objem NAROSTOL",
      ta["last_candle_volume_vs_avg20_ratio"] > 1.0, True)

print()
print("=" * 94)
print("2) PREBIEHAJUCA SVIECKA MA V recent_candles volume=null")
print("=" * 94)
rc = ta["recent_candles"]
check("posledna sviecka ma volume None", rc[-1][4], None)
check("predposledna ma objem zachovany", rc[-2][4], float(vols[-2]))
check("poznamka varuje pred prebiehajucou hodinou",
      "PREBIEHAJÚCA" in ta["recent_candles_note"], True)

print()
print("=" * 94)
print("3) TEMPO A PROJEKCIA IDU OSOBITNE")
print("=" * 94)
cv = ta["current_candle_volume"]
check("minutes_elapsed", cv["minutes_elapsed"], 17)
check("volume_so_far", cv["volume_so_far"], float(vols[-1]))
check("pace_reliable", cv["pace_reliable"], True)
proj = round(vols[-1] * 60 / 17, 2)
check("projekcia na celu hodinu", cv["projected_full_hour_volume"], proj)
print(f"\n  Skutocny objem hodiny 14:00 po dokonceni bol 14506.")
print(f"  Projekcia z 17. minuty: {proj:.0f}  -> "
      f"{'spravne naznacila NADPRIEMERNU hodinu' if proj > baseline else 'podcenila'}")
print(f"  projected_vs_avg20_ratio = {cv.get('projected_vs_avg20_ratio')}")
print()
print("  note:")
print(f"    {cv['note']}")

print()
print("=" * 94)
print("4) HRANICNE PRIPADY")
print("=" * 94)
NOW = idx[-1].to_pydatetime() + timedelta(minutes=2)
with mock.patch.object(market_data, "datetime", FakeDT):
    ta2 = market_data.compute_indicators(df, include_volume=True)
check("2. minuta: projekcia sa NEPOCITA",
      "projected_full_hour_volume" in ta2["current_candle_volume"], False)
check("2. minuta: pace_reliable=False", ta2["current_candle_volume"]["pace_reliable"], False)

NOW = idx[-1].to_pydatetime() + timedelta(hours=5)
with mock.patch.object(market_data, "datetime", FakeDT):
    ta3 = market_data.compute_indicators(df, include_volume=True)
check("zastarane data (5h): ziadna projekcia", ta3["current_candle_volume"], None)

df_nv = df.drop(columns=["volume"])
ta4 = market_data.compute_indicators(df_nv, include_volume=False)
check("bez volume dat: ratio None", ta4["last_candle_volume_vs_avg20_ratio"], None)
check("bez volume dat: current_candle_volume None", ta4["current_candle_volume"], None)
check("bez volume: sviecky maju 4 polozky", len(ta4["recent_candles"][-1]), 4)

df_nan = df.copy()
df_nan.iloc[-1, df_nan.columns.get_loc("volume")] = float("nan")
NOW = idx[-1].to_pydatetime() + timedelta(minutes=17)
with mock.patch.object(market_data, "datetime", FakeDT):
    ta5 = market_data.compute_indicators(df_nan, include_volume=True)
check("chybajuci objem prebiehajucej: current_candle_volume None",
      ta5["current_candle_volume"], None)
check("...ale ratio z dokoncenej stale funguje",
      ta5["last_candle_volume_vs_avg20_ratio"], expected)

print()
print("=" * 94)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 94)
sys.exit(0 if ok else 1)
