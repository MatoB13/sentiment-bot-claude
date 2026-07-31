"""
US Energy Information Administration (EIA) API v2 klient - volny (zdarma po
registracii na https://www.eia.gov/opendata/register.php), oficialne vladne
data. Pouziva sa LEN pre WTI - tyzdenne komercne zasoby ropy (bez SPR), presne
cislo priamo z primarneho zdroja namiesto spolahnutia sa na to, ci web_search
najde a spravne casovo zaradi tento report (viz claude_analyst._ENERGY_MACRO_RULES,
kde je tento report oznaceny ako KLUCOVY event pre WTI).

Report vychadza kazdu stredu ~10:30 ET - toto NIE JE real-time v zmysle
"aktualizuje sa kazdu sekundu", je to tyzdenna kadencia zo strany samotnych dat
(rovnaky limit by mal akykolvek ine zdroj), ale je to garantovane presne a
autoritativne, hned ako EIA report zverejni.
"""
import requests

import config

BASE_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"


def get_weekly_crude_stocks() -> dict | None:
    """Vrati posledne 2 tyzdenne hodnoty komercnych zasob ropy v USA (bez SPR,
    v tisicoch barelov) - {latest_period, latest_value, previous_period,
    previous_value, change, change_pct} - alebo None ak kluc chyba/API zlyha
    (nikdy nevyhadzuje - volajuci to berie ako volitelny doplnok k promptu)."""
    if not config.EIA_API_KEY:
        return None

    params = {
        "api_key": config.EIA_API_KEY,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[product][]": "EPC0",   # Crude Oil
        "facets[duoarea][]": "NUS",    # U.S.
        "facets[process][]": "SAX",    # Ending Stocks Excluding SPR (komercne zasoby)
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 2,
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()["response"]["data"]
    except Exception as e:
        print(f"[eia_client] Nepodarilo sa nacitat tyzdenne zasoby ropy: {e}")
        return None

    if len(rows) < 2:
        return None

    latest, previous = rows[0], rows[1]
    latest_value = float(latest["value"])
    previous_value = float(previous["value"])
    change = latest_value - previous_value

    return {
        "latest_period": latest["period"],
        "latest_value_mbbl": latest_value,
        "previous_period": previous["period"],
        "previous_value_mbbl": previous_value,
        "change_mbbl": round(change, 1),
        "change_pct": round(change / previous_value * 100, 2) if previous_value else None,
        "series": latest["series-description"],
        "source": "EIA Weekly Petroleum Status Report (commercial crude ex. SPR)",
    }


if __name__ == "__main__":
    print(get_weekly_crude_stocks())
