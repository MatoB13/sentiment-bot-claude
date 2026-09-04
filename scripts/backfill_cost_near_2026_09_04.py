"""Jednorazovy dopocet nakladov za NEAR slucku 4.9.2026.

SPUSTENIE (idempotentne - opakovane spustenie riadok len prepise):
    railway run --service Postgres -- python scripts/backfill_cost_near_2026_09_04.py

CO SA STALO: commit 969b1ea nechal v konstruktore CycleLog dvakrat kwarg
`reviewed_trade_id`. Python vyhodil TypeError AZ PO zaplatenej Claude analyze a
PRED zapisom riadku, takze NEAR ostal navzdy "due" a scheduler ho spustal znova
kazdych 5 minut od 05:42:34 do 14:56:39 UTC. V cycle_logs po tom nie je ani
stopa, takze dashboard za ten den ukazoval $14 namiesto ~$52 podla konzoly.

CO JE TVRDY UDAJ (nie odhad):
  * okno 554 minut  -> 110 petminutovych scheduler tikov. Ticker bol due v
    KAZDOM z nich, lebo bez zapisu sa _is_due nemala od coho odrazit.
  * 33 watch dispatchov z tabulky triggered_watches. Kazdy jej riadok = jeden
    REALNE dispatchnuty cyklus: watch_monitor ho zapisuje az PO in-flight aj
    hodinovej kontrole, tesne pred dispatch_triggered_check.
  * priemerna spotreba plneho NEAR cyklu z 19 realne zapisanych behov za
    3 dni: input 6148, cache_write 31964, cache_read 109369, output 6476,
    3.5 web_search dotazov. To je MERANIE, nie odhad.

CO JE ODHAD: kolko z tych 110 tikov naozaj zbehlo. Watch cyklus drzi in-flight
zamok, takze tik, ktory don spadne, sa preskoci. Odtial rozsah:
    dolna hranica 110 behov (kazdy watch beh zablokoval prave jeden tik)
    horna hranica 143 behov (ziaden tik nezablokovany)
Pouzivame STRED (127). Pri cene $0.1788/cyklus + $0.035 web to dava ~$27,
co sedi do rozdielu voci konzole ($37.73) spolu s pouzitim z Console UI a
niekolkymi spadnutymi BTC behmi, ktore zamerne NEDOPOCITAVAME - dolozit sa
daju len z 20-minutoveho useku logu, takze radsej podhodnotime.

BTC ani ine tickery sa nedopocitavaju z toho isteho dovodu.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# `railway run` podstrci DATABASE_URL s INTERNYM hostname (postgres.railway.
# internal), ktory sa z lokalneho stroja nedá preložiť. Verejna adresa ma teda
# prednost, ak existuje.
if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from db import CostCorrection, get_session  # noqa: E402

REASON = "NEAR slucka 4.9. (dvojity reviewed_trade_id, commit 969b1ea)"

RUNS_LOW, RUNS_HIGH = 110, 143
RUNS = (RUNS_LOW + RUNS_HIGH) // 2          # 126

# Merany priemer plneho NEAR cyklu (n=19, 3 dni)
PER_RUN = {"input": 6148, "cache_write": 31964, "cache_read": 109369,
            "output": 6476, "web": 3.5}

s = get_session()
row = s.query(CostCorrection).filter_by(reason=REASON).first()
if row is None:
    row = CostCorrection(reason=REASON)
    s.add(row)
    action = "vlozeny"
else:
    action = "prepisany"

row.symbol = "NEAR-USD"
row.period_start = datetime(2026, 9, 4, 5, 42, 34)
row.period_end = datetime(2026, 9, 4, 14, 56, 39)
row.method = (
    "554 min okna = 110 petminutovych tikov (tvrde) + 33 watch dispatchov z "
    "triggered_watches (tvrde). Rozsah 110-143 behov podla toho, kolko tikov "
    "zablokoval prave beziaci watch cyklus; pouzity stred. Spotreba na beh je "
    "priemer z 19 realne zapisanych NEAR cyklov za 3 dni. BTC sa nedopocitava."
)
row.runs_low, row.runs_high, row.runs_used = RUNS_LOW, RUNS_HIGH, RUNS
row.input_tokens = round(PER_RUN["input"] * RUNS)
row.cache_write_tokens = round(PER_RUN["cache_write"] * RUNS)
# 1h rozpad sme vtedy este neukladali (stlpec pribudol az 4.9. vecer), takze
# ho zamerne nechavame prazdny - radsej podhodnotit nez vymysliet cislo.
row.cache_write_1h_tokens = None
row.cache_read_tokens = round(PER_RUN["cache_read"] * RUNS)
row.output_tokens = round(PER_RUN["output"] * RUNS)
row.web_searches = round(PER_RUN["web"] * RUNS)
s.commit()

R = {"in": 2.00, "cw": 2.50, "cr": 0.20, "out": 10.00}
tok = (row.input_tokens * R["in"] + row.cache_write_tokens * R["cw"]
       + row.cache_read_tokens * R["cr"] + row.output_tokens * R["out"]) / 1e6
web = row.web_searches * 0.01
print(f"Riadok {action}: {row.symbol}  {row.period_start:%d.%m %H:%M} - {row.period_end:%H:%M}")
print(f"  behov: {RUNS} (rozsah {RUNS_LOW}-{RUNS_HIGH})")
print(f"  tokeny ${tok:.2f} + web ${web:.2f} = ${tok + web:.2f}")
for lo_hi, n in (("dolna hranica", RUNS_LOW), ("horna hranica", RUNS_HIGH)):
    t = tok * n / RUNS + web * n / RUNS
    print(f"  {lo_hi}: {n} behov = ${t:.2f}")
s.close()
