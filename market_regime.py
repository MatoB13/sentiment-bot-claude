"""Detekcia trhoveho rezimu (trend vs. ustanovene rozpatie) - 2026-08-31.

DOVOD (audit 2026-08-31, na navrh pouzivatela): bot vstupoval takmer vylucne
v smere prave prebehnuteho pohybu (86.6% vstupov nasledovalo predchadzajucich
4h). Do 21.8. trh trendoval a fungovalo to (smerova presnost 58.4% na 4h).
Od 22.8. sa trh preklopil na mean-reverting a ta ista logika zacala systematicky
kupovat vrcholy - smerova presnost spadla na 23.0% (z=-4.65). Kontrolou overene,
ze to NIE JE porucha modelu: hlupa mechanicka momentum strategia na tych istych
baroch spadla rovnako (48.5% -> 45.6%, z=-4.48), zatial co mean-reversion stupla
na 54.4%.

CO SA OVERILO BACKTESTOM (12 307 vzoriek, 14 tickerov, vlastne hodinove Strike
bary, BEZ LOOKAHEADU - rezim aj rozpatie sa pocitaju vylucne z barov PRED
momentom vstupu):

  fade okraja USTANOVENEHO rozpatia:   do 22.8.   od 22.8.   z (od 22.8.)
      horizont  4h                       51.8%      60.6%      +3.36
      horizont 12h                       49.7%      63.5%      +4.25
      horizont 24h                       54.3%      65.9%      +5.01
  zrkadlovy test - PRERAZENIE okraja:    50.0%      36.5%      -4.25  (co bot robi dnes)

DOLEZITE OBMEDZENIE: vacsina tej vyhody je REZIM, nie detekcia rozpatia - fade
bez podmienky ustanoveneho rozpatia dava od 22.8. tiez 57.7%. Podmienka pridava
len ~3-4 percentualne body. Preto sa rezim NESMIE ignorovat a fade sa NESMIE
zapnut natrvalo - to by bola ta ista chyba ako trvale momentum, len zrkadlovo.
Jedine, co prezilo OBE obdobia, je fade ustanoveneho rozpatia na 24h horizonte
(54.3% / 65.9%, dokopy 56.2% pri z=+4.86, n=1526).

PRVA definicia rozpatia v backteste (max/min z 24h) BOLA ZLA - v trendujucom
trhu je "vrchol 24h rozpatia" jednoducho nove maximum, teda breakout zona, nie
okraj rozpatia. Preto tu rozpatie MUSI splnat vsetky tri podmienky nizsie.

Tento modul LEN POCITA A VRACIA FAKTY. Ziadne rozhodnutie o smere tu nie je -
interpretacia je na claude_analyst._RANGE_NOTE (viz tam)."""
from datetime import datetime, timedelta, timezone

from db import PriceBar

# Okno na ustanovenie rozpatia. 48h zvolene v backteste - kratsie okno (24h)
# nestiha nazbierat dost dotykov oboch okrajov, dlhsie uz miesa viacero
# roznych rozpati do jedneho.
_WINDOW_HOURS = 48
# Pasmo pri okraji, ktore sa pocita ako "dotyk" - 15% sirky rozpatia.
_TOUCH_BAND_FRACTION = 0.15
# Kolko dotykov KAZDEHO okraja musi byt, aby sa rozpatie povazovalo za
# ustanovene (nie len nahodny extrem).
_MIN_TOUCHES_PER_SIDE = 3
# Stabilita sirky: sirka druhej polovice okna deleno prvou musi byt v tomto
# rozsahu. Mimo neho sa rozpatie rozsiruje/zuzuje, teda nie je ustanovene.
_WIDTH_STABILITY_RANGE = (0.6, 1.4)
# Rozpatie musi byt aspon takyto nasobok hodinovej sigmy, inak je prilis uzke
# na to, aby sa fade oplatil po spreade a poplatkoch.
_MIN_WIDTH_SIGMA_MULTIPLE = 2.0
# Ako blizko k okraju uz povazujeme cenu za "na okraji" (podiel z rozpatia).
_EDGE_FRACTION = 0.15


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
    Blizko 1 = ciara (cisty trend), blizko 0 = pila. Pouziva sa LEN ako
    doplnkovy kontext - ako samostatny detektor rezimu v backteste zlyhal
    (oddelil momentum uspesnost 48.9% vs 48.0%, teda takmer nic)."""
    if len(closes) < 3:
        return None
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return round(net / path, 3) if path else None


def compute_regime(symbol: str, session) -> dict | None:
    """Vrati fakty o rezime pre dany ticker, alebo None ak nie je dost barov.

    Vracia:
      regime            "ustanovene_rozpatie" | "trend_alebo_prechod"
      range_high/low    hranice rozpatia (len ak je ustanovene)
      range_width_pct   sirka rozpatia v %
      position_in_range 0.0 = dno, 1.0 = vrchol (len ak je ustanovene)
      at_edge           "vrchol" | "dno" | None
      touches_top/bottom  kolko barov sa dotklo ktoreho okraja
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
    # Potrebujeme aspon polovicu okna, inak by sa "rozpatie" pocitalo z par barov.
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

    established = (
        touches_top >= _MIN_TOUCHES_PER_SIDE
        and touches_bottom >= _MIN_TOUCHES_PER_SIDE
        and stability is not None
        and _WIDTH_STABILITY_RANGE[0] <= stability <= _WIDTH_STABILITY_RANGE[1]
        and sigma > 0
        and width_pct >= _MIN_WIDTH_SIGMA_MULTIPLE * sigma
    )

    if not established:
        out["regime"] = "trend_alebo_prechod"
        out["position_in_range"] = None
        out["at_edge"] = None
        return out

    pos = (last - lo) / (hi - lo)
    out["regime"] = "ustanovene_rozpatie"
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
