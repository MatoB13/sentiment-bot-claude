"""Balik A: v ziadnom prompte/scheme nesmie byt prah (cislo ani odkaz, z ktoreho sa da odvodit)."""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DB = os.environ["TEMP"].replace("\\", "/") + "/pnt.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import claude_analyst as ca  # noqa: E402
import config  # noqa: E402
import market_data  # noqa: E402
import retrospective  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<74} {str(got)[:16]:>16} (ocakavane {want!r})")


FORBIDDEN = [
    "pod prahom", "prekročila prah", "prekrocila prah", "prekročil prah", "prešla prahom",
    "minimálny prah", "prah je externá", "prah nie je zbytočne", "kvoli confidence",
    "kvôli confidence", "dosiahne prah", "číselné pásmo", "ciselne pasmo",
    f"aktuálne {config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD:.0f}",
    f"({config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD:.0f})",
    "kalibracii prahu", "zmenu prahu",
    # 2026-09-04 (holisticka kontrola): posledny zvysok v popise pola `confidence`.
    # Cislo neprezradil, ale priznaval, ze nejaka hranica na otvorenie existuje.
    "prah na otvorenie", "prahu na otvorenie", "hranicu na otvorenie",
]
A = assets.enabled_assets()[0]
now = datetime.now(timezone.utc)
ta = {"last_price": 100.0, "atr14": 2.0, "recent_candles": [], "trend": "mild_uptrend"}


def scan(label, text):
    hits = [f for f in FORBIDDEN if f in text]
    check(f"{label}: ziadna zakazana fraza", hits, [])


print("=" * 110)
print("1) SYSTEMOVY PROMPT + SCHEMY")
print("=" * 110)
sys_text = " ".join(b["text"] for b in ca._system_prompt_blocks(A))
scan("system prompt", sys_text)
scan("DECISION_TOOL", json.dumps(ca.DECISION_TOOL, ensure_ascii=False))
scan("POSITION_HEALTH_TOOL", json.dumps(ca.POSITION_HEALTH_TOOL, ensure_ascii=False))
check("system prompt: 'a nízku confidence' pri none je prec", "a nízku confidence" in sys_text, False)
check("system prompt: nova sekcia 'bez otvorenia pozície'", "bez otvorenia pozície" in sys_text, True)

print()
print("=" * 110)
print("2) USER PROMPT - otvaraci beh so streakom, watchom, retrospektivou, post-close")
print("=" * 110)
p = ca._build_user_prompt(
    A, ta, {}, {}, [], None, "predpoklad", now - timedelta(hours=2),
    performance_facts="## Tvoja doterajšia výkonnosť - spočítané fakty za 30 dní (opis, nie pravidlo)\nCelé portfólio: 20 obchodov, win 45 %, priemer +0.10 R\n",
    new_stats_text="Za 2026-09-03: 5 cyklov.",
    confidence_streak={"direction": "long", "streak_len": 4, "avg_confidence": 44.0, "price_change_pct": 1.2},
    watch_set_context={"created_at": now, "live_price": 99.0, "direction": "none", "confidence": 55,
                       "watch_price": 100.0, "watch_direction": "above", "watch_price_2": None,
                       "watch_direction_2": None, "watch_rationale": "r",
                       "break": {"level": 100.0, "direction": "above", "beyond_price": 1.0, "depth_atr": 0.5}},
    closed_trade={"trade_id": 1, "direction": "Long", "entry_price": 100, "exit_price": 98, "hours_held": 3,
                  "pnl_usd": -10.0, "close_reason": "stop_loss", "evaluation_only": True,
                  "hours_since_close": 0.1, "closed_at_str": "x"},
)
scan("user prompt (otvaraci)", p)
check("streak blok bez priemernej confidence", "priemernou confidence" in p, False)
check("streak blok ma novy nazov", "Opakovane rovnaký smer bez otvorenia pozície" in p, True)
check("evaluation_only text bez 'prahom'", "nech je confidence akákoľvek" in p, True)

print()
print("=" * 110)
print("3) USER PROMPT - health check (otvorena pozicia)")
print("=" * 110)
op = {"direction": "Long", "entry_price": 100.0, "live_price": 99.0, "stop_loss_price": 98.0,
      "take_profit_price": 103.0, "leverage": 10, "opened_at_str": "x", "hours_held": 2.0,
      "unrealized_pnl_usd": -10.0, "unrealized_pnl_pct": -1.0}
ph = ca._build_user_prompt(A, ta, {}, {}, [], None, None, open_position=op)
scan("user prompt (health)", ph)
check("health: cislo prahu zatvorenia sa neuvadza", str(int(config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD)) + ")" in ph, False)
check("health: 'hranicu ti zámerne neuvádzame'", "hranicu ti" in ph, True)

print()
print("=" * 110)
print("4) RETROSPEKTIVA - text statistik")
print("=" * 110)
stats = {"for_date": "2026-09-03", "symbol": A["strike_symbol"], "total_signals": 4, "none_count": 3,
         "opened": [{"confidence": 58, "direction": "Long", "status": "closed_by_exchange", "pnl_usd": 12.0,
                     "margin_usd": 100.0, "close_reason": "take_profit"}],
         "rejected_confidence": [{"confidence": 44, "direction": "long", "would_have": "sl", "hypothetical_pnl_usd": -5.0}],
         "rejected_other_count": 1, "none_missed": [{"would_have_direction": "long", "hypothetical_pnl_usd": 3.0}],
         "none_ambiguous_count": 0, "none_correctly_avoided_count": 2, "trade_reflections": []}
st = retrospective.format_stats_for_prompt(stats)
scan("stats text", st)
check("stats: bez priemernej confidence neotvorenych", "priemerna confidence" in st, False)
check("stats: confidence otvorenych obchodov ZOSTAVA (kalibracia)", "conf 58" in st, True)
check("stats: novy riadok 'Signaly so smerom, ktore sa neotvorili'", "Signaly so smerom, ktore sa neotvorili: 1" in st, True)

print()
print("=" * 110)
print("5) CROSS-MARKET - hodinove data (siet)")
print("=" * 110)
try:
    cm = market_data.get_cross_market_snapshot()
    vix = cm.get("vix")
    check("cross-market ma VIX", vix is not None, True)
    check("kluce change_24h_pct / change_5d_pct", vix is not None and "change_24h_pct" in vix and "change_5d_pct" in vix, True)
    check("stary kluc change_1d_pct prec", vix is not None and "change_1d_pct" in vix, False)
    print(f"       VIX: {vix}")
except Exception as e:
    print(f"  (siet nedostupna, preskocene: {e})")

print()
print("=" * 110)
print("VSETKY TESTY PRESLI" if ok else "NIEKTORE TESTY ZLYHALI")
print("=" * 110)
sys.exit(0 if ok else 1)
