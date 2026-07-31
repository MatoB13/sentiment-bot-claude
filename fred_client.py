"""
FRED (Federal Reserve Bank of St. Louis) API klient - volny (zdarma po
registracii na https://fredaccount.stlouisfed.org), ziadny podnikovy tier,
120 requestov/min. Zdielane pre VSETKY assety (podobne ako cross-market/session
snapshot) - CPI/Core CPI/Fed funds rate su presne cisla priamo z Fedu namiesto
spolahnutia sa na to, ci web_search najde a spravne casovo zaradi tieto reporty
(rovnaky dovod ako eia_client.py pre WTI).
"""
import requests

import config

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# series_id -> (popis, FRED 'units' transformacia). 'pc1' = % zmena oproti
# rovnakemu mesiacu vlani (YoY) - spravne pre CPI/Core CPI. Fed funds rate je
# uz samotna sadzba (%), NIE aplikovat YoY transformaciu na nu.
_SERIES = {
    "cpi_yoy_pct": ("CPIAUCSL", "CPI (vsetci mestski spotrebitelia, medzirocne)", "pc1"),
    "core_cpi_yoy_pct": ("CPILFESL", "Core CPI (bez potravin/energii, medzirocne)", "pc1"),
    "fed_funds_rate_pct": ("FEDFUNDS", "Efektivna Fed funds rate (mesacny priemer)", "lin"),
}


def get_macro_snapshot() -> dict | None:
    """Vrati posledne zverejnene hodnoty CPI/Core CPI (medzirocne %) a Fed
    funds rate (aktualna sadzba %), kazdu s dostupnym datumom obdobia -
    alebo None ak kluc chyba (nikdy nevyhadzuje)."""
    if not config.FRED_API_KEY:
        return None

    out = {}
    for key, (series_id, label, units) in _SERIES.items():
        try:
            resp = requests.get(BASE_URL, params={
                "series_id": series_id,
                "api_key": config.FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
                "units": units,
            }, timeout=20)
            resp.raise_for_status()
            obs = resp.json()["observations"]
            if not obs or obs[0]["value"] == ".":
                continue
            out[key] = {
                "value": round(float(obs[0]["value"]), 2),
                "as_of_period": obs[0]["date"],
                "label": label,
            }
        except Exception as e:
            print(f"[fred_client] Nepodarilo sa nacitat {series_id}: {e}")

    return out or None


if __name__ == "__main__":
    print(get_macro_snapshot())
