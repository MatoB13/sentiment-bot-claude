"""Kalendar expiracii opcii ako KONTEXT pre Clauda (2026-09-10, na ziadost pouzivatela).

POVOD: 18.9.2026 je rekordny "triple witching" (~$9.6T US opcii expiruje naraz,
podla Citadel Securities). Bot o tom nevedel a nebolo sa preco spoliehat, ze
na to narazi sam - namerane: zo 7 483 cyklov spomenulo opcie len 24 (0.3 %),
v samotnej uvahe iba 5 (vsetko krypto expiracie najdene nahodou v spravach),
a pri augustovej mesacnej expiracii (21.8.) z 96 akciovych cyklov ani jeden.

PRECO Z PRAVIDLA, NIE ZO SPRAV: datumy su dane burzovym kalendarom, takze ich
netreba stahovat ani udrziavat (okrem zoznamu sviatkov nizsie). Z pravidla sa
da vsak zistit LEN DATUM - velkost expiracie (tych $9.6T) si Claude dohlada
cez web_search sam, ked uz vie, ze nieco take existuje.

PRECO TO NIE JE V macro_calendar.MACRO_EVENTS: tamojsie udalosti v case
udalosti spustaju mimoriadne cykly (namerane 72 % skoncilo na "none", obchod
v 1 % pripadov) a Claude k nim vopred nastavuje watch urovne. Expiracia nie je
zverejnenie spravy s okamzitou reakciou, ale posun v pozicionovani - patri do
kontextu, nie medzi spustace. Nic tu nic nespusta.

UDRZBA: _NYSE_FRIDAY_HOLIDAYS pokryva 2026-2027. Pred rokom 2028 treba doplnit
(zdroj: nyse.com/markets/hours-calendars) - test_options_expiry to strazi.
"""
import calendar
import re
from datetime import date, datetime, timedelta, timezone

# Tickery s US-listovanymi opciami (samotny titul alebo index/ETF naň) - pre ne
# plati mesacna US expiracia. ZAMERNE explicitny zoznam, nie "asset_class ==
# stock": SKHYNIX/MINIMAX/UNITREE/ZHIPU su tiez "stock", ale US opcie nemaju.
# Komodity (GOLD/WTI) maju vlastne COMEX/NYMEX expiracie - zatial mimo rozsahu.
US_OPTIONS_ASSETS = {"NAS100", "NVDA", "GOOGL", "TSLA", "AAOI", "CRCL", "AAPL"}

# NYSE sviatky, ktore padnu na PIATOK. Ak na taky den vyjde treti piatok,
# expiracia sa posuva na stvrtok (napr. 19.6.2026 Juneteenth -> 18.6.).
_NYSE_FRIDAY_HOLIDAYS = {
    date(2026, 4, 3),    # Good Friday
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 12, 25),  # Christmas
    date(2027, 1, 1),    # New Year's Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 6, 18),   # Juneteenth (observed)
    date(2027, 12, 24),  # Christmas (observed)
}
HOLIDAYS_COVERED_THROUGH = 2027

_QUARTER_MONTHS = {3, 6, 9, 12}

# Okno: 7 dni dopredu (aby o 18.9. vedel bot cely tyzden vopred) a 2 dni dozadu
# - hypoteza okolo expiracie hovori aj o obdobi PO nej (odpadne hedging dealerov,
# volatilita moze vzrast), takze "prave prebehla" je tiez uzitocna informacia.
LOOKAHEAD_DAYS = 7
LOOKBACK_DAYS = 2

_NOTE_US = (
    "Dátumy expirácií US opcií vypočítané z burzového kalendára (nie zo správ). "
    "NIE JE to smerový signál. Pred expiráciou býva cena ťahaná k strike cenám s veľkým "
    "open interestom (pinning); po nej môže volatilita vzrásť, lebo odpadne hedging dealerov, "
    "ktorý dovtedy tlmil pohyby. Triple witching (marec/jún/september/december) je štvrťročná, "
    "najväčšia expirácia. Veľkosť konkrétnej expirácie z pravidla nevyplýva - ak je pre "
    "rozhodnutie podstatná, over ju."
)
_NOTE_CRYPTO = (
    "Dátumy expirácií BTC/ETH opcií na Deribite (najväčší krypto opčný trh), vypočítané "
    "z pravidla - posledný piatok v mesiaci o 08:00 UTC, štvrťročná (marec/jún/september/"
    "december) je najväčšia. NIE JE to smerový signál: pred expiráciou býva BTC ťahaný "
    "k strike cenám s veľkým open interestom, po nej sa to uvoľní. Na altcoiny pôsobí "
    "nepriamo cez BTC."
)


def _last_friday(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    return d - timedelta(days=(d.weekday() - 4) % 7)


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def _us_eastern_utc_offset_hours(d: date) -> int:
    """-4 pocas letneho casu (EDT), inak -5 (EST). US DST: od druhej nedele
    v marci do prvej nedele v novembri. Rucne, aby modul nezavisel od tzdata
    v kontajneri (macro_calendar ma casy tiez natvrdo v UTC)."""
    march1 = date(d.year, 3, 1)
    dst_start = march1 + timedelta(days=(6 - march1.weekday()) % 7 + 7)
    nov1 = date(d.year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return -4 if dst_start <= d < dst_end else -5


def us_expiration(year: int, month: int) -> datetime:
    """Mesacna US expiracia: treti piatok (resp. stvrtok, ak je piatok sviatok),
    zatvorenie NYSE 16:00 ET - v UTC."""
    d = _third_friday(year, month)
    if d in _NYSE_FRIDAY_HOLIDAYS:
        d -= timedelta(days=1)
    utc_hour = 16 - _us_eastern_utc_offset_hours(d)
    return datetime(d.year, d.month, d.day, utc_hour, 0, tzinfo=timezone.utc)


def deribit_expiration(year: int, month: int) -> datetime:
    d = _last_friday(year, month)
    return datetime(d.year, d.month, d.day, 8, 0, tzinfo=timezone.utc)


def _months_around(now: datetime):
    """Predosly, aktualny a nasledujuci mesiac - staci na okno -2/+7 dni."""
    y, m = now.year, now.month
    for dm in (-1, 0, 1):
        mm, yy = m + dm, y
        if mm == 0:
            yy, mm = yy - 1, 12
        elif mm == 13:
            yy, mm = yy + 1, 1
        yield yy, mm


def _market_for(asset: dict) -> str | None:
    if asset.get("name") in US_OPTIONS_ASSETS:
        return "us"
    if asset.get("asset_class") == "crypto":
        return "crypto"
    return None


def upcoming_for_asset(asset: dict, now: datetime) -> dict | None:
    """Expiracie v okne [now - 2 dni, now + 7 dni] relevantne pre ticker, alebo
    None (ticker bez opcneho trhu / ziadna expiracia v okne). Tvar je urceny
    priamo do TA snapshotu - odtial ide do promptu aj do CycleLog.ta, takze sa
    neskor da zmerat, ktore cykly expiraciu videli."""
    market = _market_for(asset)
    if market is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lo, hi = now - timedelta(days=LOOKBACK_DAYS), now + timedelta(days=LOOKAHEAD_DAYS)

    events = []
    for y, m in _months_around(now):
        quarterly = m in _QUARTER_MONTHS
        if market == "us":
            when = us_expiration(y, m)
            name = ("triple witching - štvrťročná expirácia US opcií a futures"
                    if quarterly else "mesačná expirácia US opcií")
        else:
            when = deribit_expiration(y, m)
            name = ("štvrťročná expirácia BTC/ETH opcií (Deribit)"
                    if quarterly else "mesačná expirácia BTC/ETH opcií (Deribit)")
        if lo <= when <= hi:
            hours = (when - now).total_seconds() / 3600
            events.append({
                "name": name,
                "datetime_utc": when.strftime("%Y-%m-%dT%H:%MZ"),
                "in_hours": round(hours, 1),
                "status": "prebehla" if hours < 0 else "nadchádza",
            })
    if not events:
        return None
    return {"events": events, "note": _NOTE_US if market == "us" else _NOTE_CRYPTO}


_DERIBIT_EXPIRY_RE = re.compile(r"^(\d{1,2})([A-Z]{3})(\d{2})$")
# Natvrdo, nie z calendar.month_abbr - ten sa riadi locale procesu a pri inom
# nez anglickom by "SEP" ticho prestal sediet.
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def parse_deribit_expiry(code: str) -> datetime | None:
    """'25SEP26' -> 2026-09-25 08:00 UTC (Deribit expiruje vzdy o 08:00 UTC).
    Pouziva deribit_options_poller."""
    m = _DERIBIT_EXPIRY_RE.match(code)
    if not m or m.group(2) not in _MONTHS:
        return None
    return datetime(2000 + int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)),
                    8, 0, tzinfo=timezone.utc)
