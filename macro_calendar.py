"""
Rucne udrziavany kalendar makro udalosti s PEVNE ZNAMYM casom vopred (na
rozdiel od cenoveho watch v watch_monitor.py, kde nevieme VOPRED kedy sa
podmienka splni) - FOMC rozhodnutie, CPI, NFP. Vsetky tri su uz sucastou
"Event Risk Gate" pravidiel pre VSETKYCH 7 tickerov (viz claude_analyst.py),
takze pri ich zverejneni ma zmysel spustit mimoriadny cyklus pre kazdy aktivny
asset namiesto cakania na dalsi bezny tik (viz watch_monitor._check_macro_events).

UDRZBA: FOMC datumy Fed zverejnuje rok+ dopredu a takmer nikdy sa nemenia -
viz federalreserve.gov/monetarypolicy/fomccalendars.htm. CPI/NFP zverejnuje
BLS tiez cca rok dopredu, ale s obcasnymi vynimkami (statne sviatky, minule
government shutdowny) - viz bls.gov/schedule/. Tento zoznam treba obcas (2x
rocne stac, ked BLS/Fed zverejnia dalsie obdobie) rucne doplnit - ZIADNE
plateny "economic calendar" API netreba, kedze ide len o ~30 udalosti rocne
z dvoch stabilnych oficialnych zdrojov.

Vsetky casy su uz prepocitane na UTC vratane DST (CPI/NFP 8:30am ET, FOMC
rozhodnutie 2:00pm ET - vzdy DRUHY den dvojdnoveho stretnutia; EDT
marec-november = UTC-4, EST november-marec = UTC-5)."""
from datetime import datetime, timedelta, timezone

MACRO_EVENTS: list[dict] = [
    # --- FOMC rozhodnutie (2:00pm ET) ---
    # Zdroj: federalreserve.gov/monetarypolicy/fomccalendars.htm (overene 2026-08-07)
    {"name": "FOMC", "datetime_utc": datetime(2026, 1, 28, 19, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2026, 12, 9, 19, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 1, 27, 19, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 3, 17, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 4, 28, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 6, 9, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 7, 28, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 9, 15, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 10, 27, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC", "datetime_utc": datetime(2027, 12, 8, 19, 0, tzinfo=timezone.utc)},

    # --- CPI (8:30am ET) ---
    # Zdroj: usinflationcalculator.com/inflation/consumer-price-index-release-schedule
    # (overene 2026-08-07) - 2027 este BLS nezverejnil, doplnit ked bude znamy.
    {"name": "CPI", "datetime_utc": datetime(2026, 1, 13, 13, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 2, 13, 13, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 3, 11, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 4, 10, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 5, 12, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 11, 10, 13, 30, tzinfo=timezone.utc)},
    {"name": "CPI", "datetime_utc": datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)},

    # --- NFP / Employment Situation (8:30am ET) ---
    # Zdroj: financecalendar.com/us-jobs-report (overene 2026-08-07) - 2027
    # este BLS nezverejnil, doplnit ked bude znamy.
    {"name": "NFP", "datetime_utc": datetime(2026, 1, 9, 13, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 2, 11, 13, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 4, 3, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 5, 8, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 6, 5, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 7, 2, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)},
    {"name": "NFP", "datetime_utc": datetime(2026, 12, 4, 13, 30, tzinfo=timezone.utc)},
]


def event_key(event: dict) -> str:
    """Stabilny identifikator na dedup (viz db.TriggeredMacroEvent) - meno +
    datum (nie presny cas, ten sa v ramci jednej udalosti nemeni)."""
    return f"{event['name']}_{event['datetime_utc'].date().isoformat()}"


def get_pending_events(now: datetime, lookback_minutes: int = 30) -> list[dict]:
    """Udalosti, ktorych cas uz nastal, ale nie viac ako `lookback_minutes`
    dozadu - zabrani spatnemu spusteniu davno prebehnutych udalosti po dlhsom
    vypadku/redeployi. Filtrovanie voci uz spustenym (TriggeredMacroEvent)
    rieši volajuci (watch_monitor.py) - tento modul je cisto o CASE, nie o
    perzistentnom stave."""
    cutoff = now - timedelta(minutes=lookback_minutes)
    return [e for e in MACRO_EVENTS if cutoff <= e["datetime_utc"] <= now]
