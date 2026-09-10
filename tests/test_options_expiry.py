"""Kalendar expiracii opcii (A) a tichy Deribit zber (B), 2026-09-10.

POVOD: rekordny triple witching 18.9.2026 - bot o nom nevedel (zo 7 483 cyklov
spomenulo opcie len 24). Test NIKDY nevola siet: requests.get je nahradene.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys
from datetime import date, datetime, timezone

_DB = os.environ["TEMP"].replace("\\", "/") + "/optexp.db"
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB
sys.path.insert(0, _ROOT)

import options_expiry as oe  # noqa: E402

ok = True


# Poznamky pre Clauda su po slovensky s diakritikou - Windows konzola (cp1252)
# by na nich pri vypise spadla, hoci samotny kod je v poriadku.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    def short(v):
        r = repr(v)
        return r if len(r) <= 80 else r[:77] + "..."
    print(f"  {'OK ' if good else 'CHYBA'} {label:<62} {short(got)} (ocakavane {short(want)})")


U = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731

print("1) US expiracie - datum aj cas v UTC")
check("sep 2026 = piatok 18.9. 20:00 UTC (EDT)", oe.us_expiration(2026, 9), U(2026, 9, 18, 20))
check("dec 2026 = 18.12. 21:00 UTC (EST)", oe.us_expiration(2026, 12), U(2026, 12, 18, 21))
check("mar 2026 = 20.3. uz po zmene casu (EDT)", oe.us_expiration(2026, 3), U(2026, 3, 20, 20))
check("nov 2026 = 20.11. uz v zimnom case (EST)", oe.us_expiration(2026, 11), U(2026, 11, 20, 21))
check("jun 2026: 19.6. Juneteenth -> stvrtok 18.6.", oe.us_expiration(2026, 6), U(2026, 6, 18, 20))
check("jun 2027: 18.6. Juneteenth (observed) -> 17.6.", oe.us_expiration(2027, 6), U(2027, 6, 17, 20))

print("\n2) Deribit - posledny piatok v mesiaci o 08:00 UTC")
check("sep 2026 = 25.9.", oe.deribit_expiration(2026, 9), U(2026, 9, 25, 8))
check("okt 2026 = 30.10.", oe.deribit_expiration(2026, 10), U(2026, 10, 30, 8))
check("parse '25SEP26'", oe.parse_deribit_expiry("25SEP26"), U(2026, 9, 25, 8))
check("parse jednocifernych dni '4SEP26'", oe.parse_deribit_expiry("4SEP26"), U(2026, 9, 4, 8))
check("nezmysel -> None", oe.parse_deribit_expiry("PERPETUAL"), None)

print("\n3) Ktory ticker co dostane")
TSLA = {"name": "TSLA", "asset_class": "stock"}
NAS = {"name": "NAS100", "asset_class": "index"}
SKH = {"name": "SKHYNIX", "asset_class": "stock"}
GOLD = {"name": "GOLD", "asset_class": "commodity"}
ADA = {"name": "ADA", "asset_class": "crypto"}
now = U(2026, 9, 11, 21)  # piatok vecer - presne tyzden pred triple witching
t = oe.upcoming_for_asset(TSLA, now)
check("TSLA 11.9.: vidi triple witching", t["events"][0]["name"].startswith("triple witching"), True)
check("  o 167 hodin", t["events"][0]["in_hours"], 167.0)
check("  poznamka je US", t["note"], oe._NOTE_US)
check("NAS100 tiez", oe.upcoming_for_asset(NAS, now) is not None, True)
check("SKHYNIX (stock, ale bez US opcii) nic", oe.upcoming_for_asset(SKH, now), None)
check("GOLD nic", oe.upcoming_for_asset(GOLD, now), None)
a = oe.upcoming_for_asset(ADA, U(2026, 9, 20, 12))
check("ADA 20.9.: stvrtrocna Deribit expiracia", "štvrťročná" in a["events"][0]["name"], True)
check("  poznamka je krypto", a["note"], oe._NOTE_CRYPTO)

print("\n4) Okno -2/+7 dni")
check("10.9. rano: 18.9. je este mimo okna", oe.upcoming_for_asset(TSLA, U(2026, 9, 10, 6)), None)
past = oe.upcoming_for_asset(TSLA, U(2026, 9, 19, 12))
check("19.9.: ukaze, ze prave prebehla", past["events"][0]["status"], "prebehla")
check("  zaporne hodiny", past["events"][0]["in_hours"] < 0, True)
check("21.9.: uz nic", oe.upcoming_for_asset(TSLA, U(2026, 9, 21, 12)), None)
check("naive datetime sa berie ako UTC",
      oe.upcoming_for_asset(TSLA, datetime(2026, 9, 11, 21)) == t, True)

print("\n5) Zapojenie - kluc sa do promptu naozaj dostane")
src = open(os.path.join(_ROOT, "market_data.py"), encoding="utf-8").read()
check("market_data vola upcoming_for_asset",
      "options_expiry.upcoming_for_asset(asset" in src, True)
import claude_analyst  # noqa: E402
out = claude_analyst._ta_for_prompt({"rsi14": 50, "options_expiry": t})
check("_ta_for_prompt ho neodfiltruje", out.get("options_expiry"), t)
check("poznamky nemaju {} (str.format bug)",
      any(ch in oe._NOTE_US + oe._NOTE_CRYPTO for ch in "{}"), False)

print("\n6) Udrzba sviatkov")
check(f"zoznam NYSE sviatkov pokryva aspon aktualny rok",
      date.today().year <= oe.HOLIDAYS_COVERED_THROUGH, True)
check("a do oktobra posledneho roka treba doplnit dalsi",
      date.today() < date(oe.HOLIDAYS_COVERED_THROUGH, 10, 1), True)

print("\n7) Deribit: max pain")
import deribit_options_poller as dp  # noqa: E402
# S=90: puty 100x2 -> 20; S=100: 0; S=110: call 100 -> 10  => max pain 100
check("jednoduchy priklad", dp.max_pain({100: 1, 110: 1}, {90: 1, 100: 2}), 100)
check("prazdne -> None", dp.max_pain({}, {}), None)

print("\n8) Deribit: summarize nad pevnymi datami")
NOW = U(2026, 9, 10, 7)
book = [
    {"instrument_name": "BTC-11SEP26-80000-C", "open_interest": 10, "volume": 4, "estimated_delivery_price": 78000},
    {"instrument_name": "BTC-11SEP26-76000-P", "open_interest": 5, "volume": 6, "estimated_delivery_price": 78000},
    {"instrument_name": "BTC-25SEP26-90000-C", "open_interest": 20, "volume": 0, "estimated_delivery_price": 78000},
    {"instrument_name": "BTC-25SEP26-70000-P", "open_interest": 25, "volume": 0, "estimated_delivery_price": 78000},
    # uz expirovana - do sumarov OI ide, do expiracii nie
    {"instrument_name": "BTC-4SEP26-78000-C", "open_interest": 1, "volume": 0, "estimated_delivery_price": 78000},
    # rok dopredu - mimo 35-dnoveho horizontu
    {"instrument_name": "BTC-24SEP27-100000-C", "open_interest": 3, "volume": 0, "estimated_delivery_price": 78000},
    {"instrument_name": "BTC-PERPETUAL", "open_interest": 999, "volume": 999},
]
s = dp.summarize(book, NOW)
check("perpetual sa ignoruje", s["instruments"], 6)
check("put/call OI = 30/34", s["put_call_oi"], round(30 / 34, 3))
check("put/call 24h objem = 6/4", s["put_call_volume_24h"], 1.5)
check("celkovy OI v USD = 64 BTC x 78000", s["total_oi_usd"], 64 * 78000)
check("najblizsia expiracia 11.9.", s["next_expiry"], datetime(2026, 9, 11, 8))
check("  jej OI v USD", s["next_expiry_oi_usd"], 15 * 78000)
check("expiracie len do 35 dni (11.9. a 25.9.)",
      [e["expiry"] for e in s["expiries"]], ["2026-09-11T08:00Z", "2026-09-25T08:00Z"])

print("\n9) Deribit: zbiera sa LEN co obchodujeme")
check("podklady su BTC a HYPE (ETH vyhodeny 10.9.)", set(dp.SOURCES), {"BTC", "HYPE"})
check("HYPE je v USDC knihe", dp.SOURCES["HYPE"]["book_currency"], "USDC")
check("DVOL sa pyta len pre BTC", [k for k, v in dp.SOURCES.items() if v["dvol"]], ["BTC"])
check("desatinna 'd' v striku (XRP_USDC 0d85)",
      dp._parse_instrument("XRP_USDC-30OCT26-0d85-P")[1], 0.85)
check("HYPE_USDC instrument sa rozparsuje",
      dp._parse_instrument("HYPE_USDC-25SEP26-78-C"), (U(2026, 9, 25, 8), 78.0, "C"))

print("\n10) Deribit: poll_all - HYPE bez cudzich strikov, zlyhanie jedneho nezhodi druhy")
import db  # noqa: E402


class R:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


calls = []
# USDC kniha obsahuje VSETKY linearne opcie - SOL tam musi byt, aby test overil,
# ze sa do HYPE snimku nepremiesa.
usdc_book = [
    {"instrument_name": "HYPE_USDC-25SEP26-80-C", "open_interest": 1000, "volume": 10, "estimated_delivery_price": 82},
    {"instrument_name": "HYPE_USDC-25SEP26-75-P", "open_interest": 500, "volume": 5, "estimated_delivery_price": 82},
    {"instrument_name": "SOL_USDC-25SEP26-150-C", "open_interest": 99999, "volume": 999, "estimated_delivery_price": 140},
]
fail_usdc = {"on": False}


def fake_get(url, params=None, timeout=None):
    calls.append((url, params))
    if url.endswith("get_book_summary_by_currency"):
        if params["currency"] == "USDC":
            if fail_usdc["on"]:
                raise ConnectionError("simulovany vypadok")
            return R({"result": usdc_book})
        return R({"result": book})
    return R({"result": {"data": [[1, 40, 41, 39, 40.5]]}})


dp.requests.get = fake_get
dp.poll_all()
S = db.get_session()
rows = {r.currency: r for r in S.query(db.OptionsSnapshot).all()}
check("zapisali sa BTC aj HYPE", sorted(rows), ["BTC", "HYPE"])
check("BTC s DVOL", rows["BTC"].dvol, 40.5)
check("HYPE bez DVOL (Deribit ho nema)", rows["HYPE"].dvol, None)
check("HYPE OI = 1500 HYPE x 82 (SOL sa NEprimiesal)", rows["HYPE"].total_oi_usd, 1500 * 82)
check("HYPE put/call = 500/1000", rows["HYPE"].put_call_oi, 0.5)
check("DVOL sa pre HYPE ani nepytal",
      any(u.endswith("get_volatility_index_data") and p.get("currency") == "HYPE" for u, p in calls), False)

fail_usdc["on"] = True
dp.poll_all()
st = {r.currency: r for r in S.query(db.OptionsPollStatus).all()}
check("pri vypadku USDC knihy: BTC ok", st["BTC"].ok, True)
check("  HYPE zlyhal", st["HYPE"].ok, False)
check("  s dovodom", "simulovany vypadok" in (st["HYPE"].error or ""), True)
check("druhy beh v tej istej hodine prepise, nezdvoji",
      S.query(db.OptionsSnapshot).filter_by(currency="BTC").count(), 1)
check("vsetky volania boli GET na deribit.com",
      all(u.startswith("https://www.deribit.com/api/v2/public/") for u, _ in calls), True)
S.close()

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
