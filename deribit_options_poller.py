"""Hodinovy TICHY zber krypto opcneho trhu z Deribitu do options_snapshots
(2026-09-10, na ziadost pouzivatela).

PRECO: otazka bola, ci by informacia o opciach pomohla botu. Poctivo: pri
drzani pozicie ~11.7 h je to mozne, ale nedokazane - opcne pozicionovanie
posobi hlavne na velmi kratkom horizonte (pinning k strikom pred expiraciou).
Preto sa to najprv LEN ZBIERA a az potom sa zmeria, ci to ma vazbu na pohyb
nasich tickerov. Rovnaky postup ako long/short ratio, ktore meranie nakoniec
vyvratilo (Spearman 0.01-0.03) - bez merania by bolo v prompte zbytocne.

CO SA ZBIERA (BTC a ETH - najvacsi krypto opcny trh; ADA/NEAR/ZEC... opcie
na Deribite nemaju, posobi to na ne nepriamo cez BTC):
  - put/call pomer open interestu a 24h objemu
  - celkovy OI v USD
  - max pain pre KAZDU expiraciu do 35 dni (v case sa posuva, preto hodinovo)
  - DVOL - Deribit implied-volatility index (krypto obdoba VIX)

ZDROJ: verejne Deribit API, bez kluca a bez limitu, ktory by nas trapil
(2 volania na menu za hodinu). Overene naozivo 10.9.: 942 BTC opcii, DVOL
hodinovo.

Zlyhanie je voci botu uplne tiche (nic z tohto modulu nevstupuje do cyklu),
ale zapise sa do OptionsPollStatus, aby vypadok nevyzeral ako "trh sa nehybe".
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import options_expiry
from db import OptionsPollStatus, OptionsSnapshot, get_session

_BASE = "https://www.deribit.com/api/v2/public"
CURRENCIES = ("BTC", "ETH")
# Expiracie dalej nez toto sa do JSON-u neukladaju - na obchod s drzanim
# ~12 h su irelevantne a len by nafukovali tabulku (Deribit ma expiracie az
# rok dopredu).
_EXPIRY_HORIZON_DAYS = 35
_TIMEOUT = 20


def _get(path: str, params: dict) -> dict:
    resp = requests.get(f"{_BASE}/{path}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if "result" not in body:
        raise ValueError(f"Deribit {path}: odpoved bez 'result': {str(body)[:200]}")
    return body["result"]


def _parse_instrument(name: str):
    """'BTC-25SEP26-155000-P' -> (expiry_dt, strike, 'P') alebo None."""
    parts = name.split("-")
    if len(parts) != 4 or parts[3] not in ("C", "P"):
        return None
    expiry = options_expiry.parse_deribit_expiry(parts[1])
    try:
        strike = float(parts[2])
    except ValueError:
        return None
    if expiry is None:
        return None
    return expiry, strike, parts[3]


def max_pain(calls: dict, puts: dict) -> float | None:
    """Strike, pri ktorom by drzitelia opcii pri expiracii dostali najmenej.
    calls/puts: {strike: open_interest}. Kandidatmi su samotne striky - medzi
    nimi je vyplatna funkcia linearna, takze minimum lezi vzdy na niektorom."""
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return None
    best, best_pain = None, None
    for s in strikes:
        pain = (sum(oi * (s - k) for k, oi in calls.items() if s > k)
                + sum(oi * (k - s) for k, oi in puts.items() if s < k))
        if best_pain is None or pain < best_pain:
            best, best_pain = s, pain
    return best


def summarize(book: list[dict], now: datetime) -> dict:
    """Z get_book_summary_by_currency spravi riadok pre OptionsSnapshot.
    Samostatna funkcia bez siete, aby sa dala otestovat na pevnych datach."""
    index_price = next((b.get("estimated_delivery_price") for b in book
                        if b.get("estimated_delivery_price")), None)
    by_expiry = defaultdict(lambda: {"C": defaultdict(float), "P": defaultdict(float)})
    call_oi = put_oi = call_vol = put_vol = 0.0
    parsed = 0
    for b in book:
        p = _parse_instrument(b.get("instrument_name", ""))
        if p is None:
            continue
        expiry, strike, kind = p
        oi = float(b.get("open_interest") or 0)
        vol = float(b.get("volume") or 0)
        parsed += 1
        if kind == "C":
            call_oi += oi
            call_vol += vol
        else:
            put_oi += oi
            put_vol += vol
        if expiry >= now:
            by_expiry[expiry][kind][strike] += oi

    horizon = now + timedelta(days=_EXPIRY_HORIZON_DAYS)
    expiries = []
    for expiry in sorted(by_expiry):
        if expiry > horizon:
            break
        c, p = by_expiry[expiry]["C"], by_expiry[expiry]["P"]
        c_oi, p_oi = sum(c.values()), sum(p.values())
        expiries.append({
            "expiry": expiry.strftime("%Y-%m-%dT%H:%MZ"),
            "oi_usd": round((c_oi + p_oi) * index_price, 0) if index_price else None,
            "put_call_oi": round(p_oi / c_oi, 3) if c_oi else None,
            "max_pain": max_pain(c, p),
        })

    nxt = expiries[0] if expiries else None
    return {
        "index_price": index_price,
        "total_oi_usd": round((call_oi + put_oi) * index_price, 0) if index_price else None,
        "put_call_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "put_call_volume_24h": round(put_vol / call_vol, 3) if call_vol else None,
        "next_expiry": (datetime.strptime(nxt["expiry"], "%Y-%m-%dT%H:%MZ") if nxt else None),
        "next_expiry_oi_usd": nxt["oi_usd"] if nxt else None,
        "next_expiry_max_pain": nxt["max_pain"] if nxt else None,
        "expiries": expiries,
        "instruments": parsed,
    }


def _latest_dvol(currency: str, now: datetime) -> float | None:
    end = int(now.timestamp() * 1000)
    res = _get("get_volatility_index_data", {
        "currency": currency, "start_timestamp": end - 3 * 3600 * 1000,
        "end_timestamp": end, "resolution": "3600"})
    rows = res.get("data") or []
    return float(rows[-1][4]) if rows else None


def _record_status(currency: str, ok: bool, session, error=None) -> None:
    row = session.get(OptionsPollStatus, currency)
    if row is None:
        row = OptionsPollStatus(currency=currency)
        session.add(row)
    row.polled_at = datetime.now(timezone.utc)
    row.ok = ok
    row.error = None if error is None else str(error)[:300]


def poll_all() -> None:
    """Vstupny bod scheduleru (main.py, hodinovo). Zlyhanie jednej meny nesmie
    zhodit druhu ani cely job."""
    print(f"\n=== [deribit_options_poller] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        for ccy in CURRENCIES:
            # Commit po kazdej mene - pri spolocnom by rollback pri zlyhani ETH
            # zahodil aj uz hotovy BTC riadok (chyba chytena v long_short_poller).
            try:
                now = datetime.now(timezone.utc)
                book = _get("get_book_summary_by_currency", {"currency": ccy, "kind": "option"})
                s = summarize(book, now)
                try:
                    s["dvol"] = _latest_dvol(ccy, now)
                except Exception as e:  # DVOL je doplnok - bez neho snimok stale ma zmysel
                    print(f"[deribit_options_poller] [{ccy}] DVOL zlyhal: {e}")
                    s["dvol"] = None
                hour = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
                if s["next_expiry"] is not None:
                    s["next_expiry"] = s["next_expiry"].replace(tzinfo=None)
                row = (session.query(OptionsSnapshot)
                       .filter(OptionsSnapshot.currency == ccy, OptionsSnapshot.hour_start == hour)
                       .first())
                if row is None:
                    row = OptionsSnapshot(currency=ccy, hour_start=hour)
                    session.add(row)
                for k, v in s.items():
                    setattr(row, k, v)
                _record_status(ccy, True, session)
                session.commit()
                # Formatovanie BEZ predpokladu, ze cislo existuje: vypis je az po
                # commite, a pad tu by v except vetve zapisal ok=False, hoci data
                # su ulozene.
                oi_txt = f"${s['total_oi_usd']:,.0f}" if s["total_oi_usd"] is not None else "?"
                print(f"[deribit_options_poller] [{ccy}] {s['instruments']} opcii, "
                      f"P/C OI {s['put_call_oi']}, OI {oi_txt}, "
                      f"DVOL {s['dvol']}, najblizsia expiracia {s['next_expiry']} "
                      f"max pain {s['next_expiry_max_pain']}")
            except Exception as e:
                session.rollback()
                print(f"[deribit_options_poller] [{ccy}] zber zlyhal: {e}")
                try:
                    _record_status(ccy, False, session, error=e)
                    session.commit()
                except Exception as e2:
                    session.rollback()
                    print(f"[deribit_options_poller] [{ccy}] nepodarilo sa zapisat ani status: {e2}")
    except Exception as e:
        session.rollback()
        print(f"[deribit_options_poller] neocakavana chyba celeho behu: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    poll_all()
