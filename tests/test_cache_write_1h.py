"""1-hodinova cast cache write sa musi dostat z API az do DB.

POVOD: cache write sa uctuje podla TTL - 5-minutovy 1.25x zakladnu input sadzbu,
1-hodinovy 2x. System prompt cachujeme s ttl="1h", user spravu s defaultnym
5-minutovym. Bot dovtedy ukladal len sucet `cache_creation_input_tokens`, takze
dashboard uctoval vsetko jednotne 1.25x a kazdy cyklus podhodnocoval asi o
$0.008 (~4 % tokenoveho uctu). Rozpad z API sa zahadzoval a spatne sa dopocitat
NEDA - preto tento test strazi cely retazec.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

DB = os.environ["TEMP"].replace("\\", "/") + "/cw1h.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import claude_analyst  # noqa: E402
from db import CycleLog, get_session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<60} {got!r:>9} (ocakavane {want!r})")


print("1) _cache_write_1h cita rozpad z usage.cache_creation")
check("realny tvar odpovede",
      claude_analyst._cache_write_1h({
          "cache_creation_input_tokens": 29000,
          "cache_creation": {"ephemeral_5m_input_tokens": 24000,
                             "ephemeral_1h_input_tokens": 5000}}), 5000)
check("iba 5-minutovy zapis",
      claude_analyst._cache_write_1h({
          "cache_creation": {"ephemeral_5m_input_tokens": 24000,
                             "ephemeral_1h_input_tokens": 0}}), 0)

print("\n2) Chybajuci rozpad NIKDY nenadhodnoti (fail-safe na 0)")
check("cache_creation uplne chyba", claude_analyst._cache_write_1h({}), 0)
check("cache_creation je None", claude_analyst._cache_write_1h({"cache_creation": None}), 0)
check("cache_creation nie je dict", claude_analyst._cache_write_1h({"cache_creation": 123}), 0)
check("kluc chyba", claude_analyst._cache_write_1h({"cache_creation": {}}), 0)
check("hodnota je None",
      claude_analyst._cache_write_1h({"cache_creation": {"ephemeral_1h_input_tokens": None}}), 0)

print("\n3) Stlpec existuje a da sa zapisat aj precitat")
s = get_session()
s.add(CycleLog(symbol="TEST-USD", outcome="rejected",
               usage_cache_write_tokens=29000, usage_cache_write_1h_tokens=5000))
s.commit()
row = s.query(CycleLog).filter_by(symbol="TEST-USD").first()
check("usage_cache_write_1h_tokens", row.usage_cache_write_1h_tokens, 5000)
check("celkovy cache_write ostal NEZMENENY (1h je podmnozina)",
      row.usage_cache_write_tokens, 29000)

print("\n4) Stary riadok bez rozpadu ostava None (dashboard mu da 0)")
s.add(CycleLog(symbol="OLD-USD", outcome="rejected", usage_cache_write_tokens=29000))
s.commit()
old = s.query(CycleLog).filter_by(symbol="OLD-USD").first()
check("usage_cache_write_1h_tokens", old.usage_cache_write_1h_tokens, None)
s.close()

print("\n5) Cena: 1h cast sa DOPLACA rozdielom sadzieb, nerata sa dvakrat")
# Presna kopia vzorca z index.html usageRowCosts().
CW, CW1H, CR, OUT, IN = 2.50, 4.00, 0.20, 10.00, 2.00


def cost(cw, cw1h, cr, out):
    return (cw * CW + cw1h * (CW1H - CW) + cr * CR + out * OUT) / 1e6


bez = cost(29000, 0, 115000, 5800)
s_1h = cost(29000, 5000, 115000, 5800)
check("cyklus bez 1h casti (ako doteraz)", round(bez, 4), round((29000*2.5 + 115000*0.2 + 5800*10)/1e6, 4))
check("29k cache write, z toho 5k na 1h", round(s_1h - bez, 4), round(5000 * 1.5 / 1e6, 4))
print(f"       -> rozdiel na cyklus: ${s_1h - bez:.4f}, pri 140 cykloch/den ${140*(s_1h-bez):.2f}/den")

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
