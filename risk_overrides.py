"""Rozhoduje, aky SL_PCT/TP_PCT je pre dany ticker PRAVE TERAZ efektivny -
config.py {TICKER}_SL_PCT/{TICKER}_TP_PCT (staticky, cez Railway ENV) je len
POCIATOCNY seed. Ak pre symbol existuje riadok v db.RiskOverride (zapisany
cez nas100-monitor-web "Nastavit ako default" tlacidlo, viz sl_calibration.py
+ db.RiskOverride docstring), TEN ma prednost - prejavi sa okamzite na
dalsom cykle, ziadny redeploy netreba.

Pouziva sa z DVOCH miest: trade_cycle.py (skutocne risk sizing pri otvarani
pozicie) A sl_calibration.py (aby navrhovany pomer TP:SL vychadzal z toho,
co je AKTUALNE efektivne, nie zo stareho config.py defaultu, ak uz raz bol
override aplikovany)."""
from db import RiskOverride


def get_effective_sl_tp(session, asset: dict) -> tuple[float, float]:
    override = session.query(RiskOverride).filter(
        RiskOverride.symbol == asset["strike_symbol"],
    ).first()
    if override is not None:
        return override.sl_pct, override.tp_pct
    return asset["sl_pct"], asset["tp_pct"]
