"""Detekcia USTANOVENEHO CENOVEHO PASMA - 2026-08-31.

POZOR NA NAZOV: tento modul NEROZPOZNAVA TRHOVY REZIM. Povodne sa volal
market_regime.py a vracal hodnoty "ustanovene_rozpatie"/"trend_alebo_prechod",
co sluboval o dost viac, nez skutocne meria - premenovane hned v ten isty den.

CO TENTO MODUL VIE: spolahlivo zistit, ci je ticker PRAVE TERAZ vnutri
ustanoveneho cenoveho pasma. Je to lokalne, mechanicke meranie z vlastnych
Strike barov. Overene - reprodukuje backtest (historicky oznaci 40.5% okien
za pasmo, backtest dal 40.7%).

CO TENTO MODUL NEVIE: povedat, ci je trh v trendovom alebo mean-reverting
rezime, teda ci pohyb bude pokracovat alebo sa vrati. Testovali sa styri
kandidatske miery - trailing autokorelacia, Kaufmanov efficiency ratio, sirka
pasma v nasobkoch sigmy a historia prerazeni predchadzajuceho pasma. ANI JEDNA
neoddelila ziskove obdobie od stratoveho: kazde pasmo kazdej miery vyslo
zaporne pred 22.8. a kladne po nom. Rozdiel medzi tymi obdobiami je vlastnost
celeho trhu v case, nie nieco, co sa da odmerat lokalne pred vstupom.

PRECO SA TO TEDA OPLATI POCITAT (audit 2026-08-31):
Detektor najde miesta, kde MOMENTUM historicky nefunguje - a to plati v OBOCH
obdobiach. Na vstupoch "v pasme + na okraji" (ATR SL/TP, max drzanie 24h,
zapocitane realne poplatky a spread, bez lookaheadu):

                          do 22.8.    od 22.8.    spolu
    momentum (doteraz)     -157.6 R    -65.9 R   -223.5 R
    fade (proti pohybu)    -169.7 R    +69.2 R   -100.5 R
    rozdiel                 -12.1 R   +135.2 R   +123.1 R

Cize: ked sa mylime, stojí nas to -12.1 R na 1209 prilezitosti (-0.01 R na kus,
sum). Ked mame pravdu, ziskame +135.2 R na 238 prilezitosti (+0.57 R na kus).
Nie je to stavka na rezim - je to vymena zlej volby za volbu, ktora nie je
horsia a niekedy je vyrazne lepsia.

CO NIE JE OVERENE: ze mimo pasma momentum funguje. Overilo sa len to, ze
V PASME nefunguje. Preto claude_analyst._RANGE_NOTE pri `v_pasme=false`
zamerne NIC neodporuca a necha rozhodovanie na ostatnych signaloch.

Tento modul LEN POCITA A VRACIA FAKTY - ziadne rozhodnutie o smere tu nie je."""
from datetime import datetime, timedelta, timezone

from db import PriceBar

# Okno na ustanovenie pasma. 48h zvolene v backteste - kratsie (24h) nestiha
# nazbierat dost dotykov oboch okrajov, dlhsie uz miesa viacero roznych pasiem.
_WINDOW_HOURS = 48
# Pasmo pri okraji, ktore sa pocita ako "dotyk" - 15% sirky.
_TOUCH_BAND_FRACTION = 0.15
# Kolko dotykov KAZDEHO okraja musi byt, aby slo o ustanovene pasmo, nie
# o nahodny jednorazovy extrem.
_MIN_TOUCHES_PER_SIDE = 3
# Stabilita sirky: sirka druhej polovice okna deleno prvou. Mimo tohto rozsahu
# sa pasmo rozsiruje alebo zuzuje, teda nie je ustanovene.
_WIDTH_STABILITY_RANGE = (0.6, 1.4)
# Pasmo musi byt aspon takyto nasobok hodinovej sigmy, inak je prilis uzke na
# to, aby sa vstup oplatil po spreade a poplatkoch.
_MIN_WIDTH_SIGMA_MULTIPLE = 2.0
# Ako blizko k okraju uz povazujeme cenu za "na okraji" (podiel zo sirky).
_EDGE_FRACTION = 0.15

# PRVA definicia pasma v backteste (max/min z 24h) BOLA ZLA - v trendujucom trhu
# je "vrchol 24h pasma" jednoducho nove maximum, teda breakout zona, nie okraj
# pasma. S nou vychadzal fade na okraji ako STRATOVY (41.9%, z=-2.46). Az po
# pridani troch podmienok vyssie (dotyky, stabilita, minimalna sirka) sa ukazal
# skutocny obraz. Preto sa ziadna z nich nesmie vypustit.


def _sigma(closes: list[float]) -> float:
    """Smerodajna odchylka hodinovych percentualnych zmien."""
    if len(closes) < 10:
        return 0.0
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100
            for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 5:
        return 0.0
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5


def _efficiency_ratio(closes: list[float]) -> float | None:
    """Kaufmanov efficiency ratio: |cisty pohyb| / |sucet vsetkych pohybov|.
    Blizko 1 = ciara, blizko 0 = pila. Vracia sa LEN ako doplnkovy kontext -
    ako detektor rezimu v backteste zlyhal (viz hlavicka modulu), takze sa
    NESMIE pouzit na rozhodnutie o smere."""
    if len(closes) < 3:
        return None
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return round(net / path, 3) if path else None


def compute_price_range(symbol: str, session) -> dict | None:
    """Vrati fakty o cenovom pasme pre dany ticker, alebo None ak nie je dost barov.

    Vracia:
      in_range          True = cena je v ustanovenom pasme, False = nie je
      range_high/low    hranice pasma (len ak in_range)
      range_width_pct   sirka pasma v %
      position_in_range 0.0 = dno, 1.0 = vrchol (len ak in_range)
      at_edge           "vrchol" | "dno" | None
      touches_top/bottom  kolko barov sa dotklo ktoreho okraja
      width_stability   pomer sirky druhej a prvej polovice okna
      hourly_sigma_pct  smerodajna odchylka hodinovych zmien (%)
      min_width_required_pct  hranica, ktoru musi sirka prekonat (2x sigma)
      failed_conditions zoznam podmienok, ktore NEPRESLI (prazdny = in_range)
      efficiency_ratio  doplnkovy kontext (viz _efficiency_ratio)
    """
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
              - timedelta(hours=_WINDOW_HOURS))
    bars = (
        session.query(PriceBar)
        .filter(PriceBar.symbol == symbol, PriceBar.hour_start >= cutoff)
        .order_by(PriceBar.hour_start)
        .all()
    )
    # Potrebujeme aspon polovicu okna, inak by sa "pasmo" pocitalo z par barov.
    if len(bars) < _WINDOW_HOURS // 2:
        return None

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    hi, lo, last = max(highs), min(lows), closes[-1]
    if hi <= lo or not lo:
        return None

    width_pct = (hi - lo) / lo * 100
    sigma = _sigma(closes)
    out = {
        "window_hours": _WINDOW_HOURS,
        "range_width_pct": round(width_pct, 3),
        "efficiency_ratio": _efficiency_ratio(closes),
    }

    band = (hi - lo) * _TOUCH_BAND_FRACTION
    touches_top = sum(1 for h in highs if h >= hi - band)
    touches_bottom = sum(1 for l in lows if l <= lo + band)
    out["touches_top"] = touches_top
    out["touches_bottom"] = touches_bottom

    half = len(bars) // 2
    w1 = max(highs[:half]) - min(lows[:half])
    w2 = max(highs[half:]) - min(lows[half:])
    stability = (w2 / w1) if w1 else None
    out["width_stability"] = round(stability, 2) if stability is not None else None

    # 2026-09-01 (po externom audite) - ktora KONKRETNE podmienka padla.
    # Dovod: ZEC 31.8. nahlasil falosny `data_issue` ("rozpor v datach"), lebo
    # videl in_range=false pri dotykoch 4/6 a nemal ako zistit, ze padla
    # width_stability. Styri podmienky su nezavisle a staci, aby zlyhala jedna -
    # bez tohto zoznamu to z vystupu proste nebolo vidiet.
    failed = []
    if touches_top < _MIN_TOUCHES_PER_SIDE:
        failed.append("touches_top")
    if touches_bottom < _MIN_TOUCHES_PER_SIDE:
        failed.append("touches_bottom")
    if stability is None or not (_WIDTH_STABILITY_RANGE[0] <= stability <= _WIDTH_STABILITY_RANGE[1]):
        failed.append("width_stability")
    # Minimalna sirka sa doteraz nedala spatne overit VOBEC - sigma sa nikam
    # neukladala, takze ani dashboard ani spatna analyza nevedeli povedat, ci
    # pasmo padlo prave na nej. Teraz sa uklada aj pozadovana hranica.
    min_width_required = _MIN_WIDTH_SIGMA_MULTIPLE * sigma if sigma > 0 else None
    out["hourly_sigma_pct"] = round(sigma, 4) if sigma else None
    out["min_width_required_pct"] = round(min_width_required, 3) if min_width_required else None
    if not (sigma > 0 and width_pct >= _MIN_WIDTH_SIGMA_MULTIPLE * sigma):
        failed.append("min_width")

    in_range = not failed
    out["in_range"] = in_range
    out["failed_conditions"] = failed

    if not in_range:
        out["position_in_range"] = None
        out["at_edge"] = None
        return out

    pos = (last - lo) / (hi - lo)
    out["range_high"] = round(hi, 8)
    out["range_low"] = round(lo, 8)
    out["position_in_range"] = round(pos, 3)
    if pos >= 1 - _EDGE_FRACTION:
        out["at_edge"] = "vrchol"
    elif pos <= _EDGE_FRACTION:
        out["at_edge"] = "dno"
    else:
        out["at_edge"] = None
    return out
