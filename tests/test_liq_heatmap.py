"""Likvidacna heatmapa pre dashboard (2026-09-12). Len model - bez siete a DB."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import base64
import os
import sys

import numpy as np

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/liqmap.db"
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import assets  # noqa: E402
import liq_heatmap as lh  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {got!r} (ocakavane {want!r})")


# 16 dni 5-min barov, cena 100 a plocha; OI rastie len v jednom bare.
n = 16 * 288
t = np.datetime64("2026-08-27T00:00") + np.arange(n) * np.timedelta64(5, "m")
c = np.full(n, 100.0); h = c + 0.05; l = c - 0.05; o = c.copy()
oi = np.full(n, 1000.0)
k = n - 5 * 288                                   # 5 dni pred koncom (v zobrazenom okne)
oi[k:] = 1100.0                                    # +100 kontraktov pri cene 100

print("1) Rast OI vytvori longy pod cenou a shorty nad nou (paky 10/25/50/100x)")
m = lh.build_heatmap(t, o, h, l, c, oi)
check("stlpcov = hodin za 14 dni", m["cols"], 14 * 24)
q = np.frombuffer(base64.b64decode(m["matrix_b64"]), dtype=np.uint8).reshape(m["cols"], m["rows"])
edges = np.array(m["edges"]); centers = np.sqrt(edges[:-1] * edges[1:])
last = q[-1]
hot = centers[last > 0]
check("likvidita existuje pod aj nad cenou", (hot.min() < 100, hot.max() > 100), (True, True))
check("najblizsia uroven: 100x long ~99.5 a short ~100.5 (+-0.15 mriezka)",
      (abs(float(hot[hot < 100].max()) - 99.5) <= 0.15, abs(float(hot[hot > 100].min()) - 100.5) <= 0.15), (True, True))
check("pred rastom OI prazdne", int(q[0].sum()), 0)
check("nerovnovaha pri symetrii ~0 (okno aspon 3 %)", abs(m["imbalance"]) < 0.05, True)
check("  okno 3 % pri pokojnom trhu", round(m["imbalance_window_pct"], 1), 3.0)
check("najvacsi zhluk pod cenou existuje", m["top_below"] is not None and m["top_below"]["price"] < 100, True)

print("\n2) Cena prejde cez uroven -> likvidita zmizne (vybratie)")
h2 = h.copy(); h2[-10] = 100.9                     # knot hore cez 100x a 50x shorty (100.5, 101.5 nie)
m2 = lh.build_heatmap(t, o, h2, l, c, oi)
q2 = np.frombuffer(base64.b64decode(m2["matrix_b64"]), dtype=np.uint8).reshape(m2["cols"], m2["rows"])
e2 = np.array(m2["edges"]); c2 = np.sqrt(e2[:-1] * e2[1:])
above = c2[(q2[-1] > 0) & (c2 > 100)]
check("short 100x (~100.5) vybraty, 50x (~101.5) zostal", round(float(above.min()), 1) > 100.9, True)
check("po vybrati hore je viac likvidity dole", m2["imbalance"] < 0, True)

print("\n3) Pokles OI zmensi vsetky urovne proporcne")
oi3 = oi.copy(); oi3[-20:] = 550.0                 # polovica pozicii zatvorena
m3 = lh.build_heatmap(t, o, h, l, c, oi3)
check("najvacsi zhluk pod cenou klesol ~na polovicu",
      round(m3["top_below"]["usd"] / m["top_below"]["usd"], 2), 0.5)

print("\n4) Malo dat -> chyba, nie prazdna mapa")
try:
    lh.build_heatmap(t[:100], o[:100], h[:100], l[:100], c[:100], oi[:100])
    check("vyhodi ValueError", False, True)
except ValueError:
    check("vyhodi ValueError", True, True)

print("\n5) Tickery a hranica s rozhodovanim")
m_ = {a["name"]: a.get("binance_liq_symbol") for a in assets.ALL_ASSETS if a.get("binance_liq_symbol")}
check("BTC, ADA, ZEC, HYPE, NIGHT", sorted(m_), ["ADA", "BTC", "HYPE", "NIGHT", "ZEC"])
src = open(os.path.join(_ROOT, "trade_cycle.py"), encoding="utf-8").read() \
    + open(os.path.join(_ROOT, "claude_analyst.py"), encoding="utf-8").read()
check("trade_cycle/claude_analyst heatmapu NEpouzivaju (len dashboard)", "liq_heatmap" in src, False)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
