"""Overi, ze system prompt sa zostavi pre VSETKY assety (brace bug z 2026-07-30)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os, sys
sys.path.insert(0, _ROOT)
if os.environ.get("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

import assets, claude_analyst

ok = fail = 0
for a in assets.ALL_ASSETS:
    try:
        blocks = claude_analyst._system_prompt_blocks(a)
        txt = blocks[1]["text"]
        has_range = "price_range" in txt
        assert has_range, "chyba _RANGE_NOTE v prompte (hlada sa price_range)"
        print(f"  OK   {a['name']:<14} blokov={len(blocks)}  dlzka per-asset={len(txt):>6}  "
              f"rezim v prompte={'ANO' if has_range else 'NIE'}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {a['name']:<14} {type(e).__name__}: {e}")
        fail += 1

print("VSETKY TESTY PRESLI" if not fail else "NIEKTORE TESTY ZLYHALI")
print(f"  OK={ok}  FAIL={fail}")
import sys as _s; _s.exit(0 if not fail else 1)

