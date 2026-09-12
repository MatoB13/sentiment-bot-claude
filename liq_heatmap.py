"""Likvidacna heatmapa (odhad ako Coinglass) pre dashboard - 2026-09-12, na
ziadost pouzivatela ("zobrazuj mi to na konfig tabe, vzdy automaticky
aktualizovane - podobnu heatmapu ako na obrazku").

LEN ZOBRAZENIE. Do rozhodovania bota nic nejde: test 12.9. (Binance 29 dni,
BTC/ADA/ZEC/HYPE/NIGHT) nenasiel predikcnu hodnotu nad obycajny trend a
"vybratie likvidity -> otocenie" sa nepotvrdilo. Viz pamat
liquidation_heatmap_tested_2026_09_12.

MODEL (rovnaky ako v teste): rast open interestu o d kontraktov pri cene p = otvorilo
sa d longov AJ d shortov (OI rata jednu stranu). Pre kazdu paku L z LEVERAGE
(s vahou) lezi likvidacia longu na p*(1-1/L+MMR), shortu na p*(1+1/L-MMR).
Pokles OI = zatvaranie - vsetky urovne sa proporcne zmensia. Ked cena prejde cez
uroven (low <= long, high >= short), likvidita je vybrata a zmizne.
Nie su to data burzy - skutocne likvidacne ceny pozicii vidi len burza.

Data: Binance USDT-M futures, 5-min OI + sviecky (verejne GET, bez klucov).
Binance dava OI len ~30 dni dozadu, preto sa ukladaju do binance_oi_bars -
historia sa bude predlzovat sama.

NIKDY nevyhodi vynimku von - zlyhanie tickera sa zapise do liq_heatmaps.ok/error.
"""
import base64
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

import assets
from db import BinanceOiBar, LiqHeatmap, get_session

_F = "https://fapi.binance.com"
_TIMEOUT = 20
_STEP_MS = 300_000                     # 5 min
_FIRST_FETCH_DAYS = 29                 # Binance OI historia ~30 dni
_KEEP_DAYS = 120                       # dlhsia historia pre buduci test
_MODEL_DAYS = 45                       # z kolkych dni sa mapa stavia (starsie urovne su uz vacsinou vybrate)
_SHOW_DAYS = 14                        # kolko dni ukazuje heatmapa (ako na obrazku z Coinglass: 2 tyzdne)
_ROWS = 160                            # cenove riadky heatmapy
_MARGIN = 0.08                         # +-8 % nad/pod rozpatim ceny za zobrazene obdobie
_BIN = np.log(1.001)                   # vnutorna mriezka modelu 0.1 %
MMR = 0.005
LEVERAGE = {10: 0.35, 25: 0.30, 50: 0.20, 100: 0.15}
IMBALANCE_ATR = 5.0
IMBALANCE_MIN_PCT = 3.0


def _get(path, params):
    for attempt in range(4):
        r = requests.get(_F + path, params=params, timeout=_TIMEOUT)
        if r.status_code in (418, 429):
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Binance {path}: opakovane {r.status_code}")


def _paged(path, params, start_ms, end_ms, limit):
    out, t = [], start_ms
    while t < end_ms:
        page = _get(path, dict(params, startTime=t, endTime=min(end_ms, t + _STEP_MS * limit - 1), limit=limit))
        out.extend(page or [])
        t += _STEP_MS * limit
        time.sleep(0.2)
    return out


def _fetch_new(session, symbol: str, bsym: str) -> int:
    """Dotiahne 5-min OI + sviecky od posledneho ulozeneho baru. Vrati pocet novych."""
    last = (session.query(BinanceOiBar.t).filter(BinanceOiBar.symbol == symbol)
            .order_by(BinanceOiBar.t.desc()).first())
    now_ms = int(time.time() * 1000)
    if last:
        start_ms = int(last[0].replace(tzinfo=timezone.utc).timestamp() * 1000) + _STEP_MS
    else:
        start_ms = now_ms - _FIRST_FETCH_DAYS * 86_400_000
    # Len UZAVRETE 5-min periody - prebiehajuca by sa pri dalsom behu uz neaktualizovala.
    end_ms = now_ms - now_ms % _STEP_MS - 1
    if end_ms - start_ms < _STEP_MS:
        return 0
    oi = {int(x["timestamp"]): float(x["sumOpenInterest"])
          for x in _paged("/futures/data/openInterestHist", {"symbol": bsym, "period": "5m"},
                          start_ms, end_ms, 500)}
    kl = _paged("/fapi/v1/klines", {"symbol": bsym, "interval": "5m"}, start_ms, end_ms, 1500)
    rows = []
    for k in kl:
        ts = int(k[0])
        if ts in oi and ts + _STEP_MS - 1 <= end_ms:
            rows.append(BinanceOiBar(
                symbol=symbol, t=datetime.fromtimestamp(ts / 1000, timezone.utc).replace(tzinfo=None),
                oi=oi[ts], o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4])))
    session.add_all(rows)
    session.query(BinanceOiBar).filter(
        BinanceOiBar.symbol == symbol,
        BinanceOiBar.t < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_KEEP_DAYS),
    ).delete(synchronize_session=False)
    session.commit()
    return len(rows)


def build_heatmap(t, o, h, l, c, oi, now=None) -> dict:
    """Z 5-min barov (numpy polia, t = datetime64 UTC) spocita heatmapu pre dashboard.
    Cista funkcia bez siete a DB - testuje sa priamo."""
    n_bars = len(c)
    if n_bars < 300:
        raise ValueError(f"malo dat ({n_bars} 5-min barov)")
    lo_p, hi_p = float(np.min(l)) * 0.5, float(np.max(h)) * 1.6
    base = np.floor(np.log(lo_p) / _BIN)
    n = int(np.ceil(np.log(hi_p) / _BIN) - base) + 2
    grid = np.exp((np.arange(n) + base) * _BIN)

    def idx(p):
        return int(np.clip(np.floor(np.log(p) / _BIN) - base, 0, n - 1))

    long_l, short_l = np.zeros(n), np.zeros(n)
    minutes = (t.astype("datetime64[m]").astype(np.int64)) % 60
    cols, col_t, col_c = [], [], []
    show_from = t[-1] - np.timedelta64(_SHOW_DAYS * 24 * 60, "m")
    for i in range(1, n_bars):
        il, ih = idx(l[i]), idx(h[i])
        long_l[il:] = 0.0
        short_l[:ih + 1] = 0.0
        delta = oi[i] - oi[i - 1]
        if delta > 0:
            for lev, w in LEVERAGE.items():
                long_l[idx(c[i] * (1 - 1 / lev + MMR))] += delta * w
                short_l[idx(c[i] * (1 + 1 / lev - MMR))] += delta * w
        elif delta < 0 and oi[i - 1] > 0:
            f = max(0.0, 1 + delta / oi[i - 1])
            long_l *= f
            short_l *= f
        if minutes[i] == 55 and t[i] > show_from:      # koniec hodiny
            cols.append((long_l + short_l) * grid)      # v USD
            col_t.append(t[i])
            col_c.append(float(c[i]))
    if not cols:
        raise ValueError("ziadna uplna hodina v zobrazenom obdobi")

    # Zobrazovacia mriezka: log-rovnomerna, rozpatie ceny za zobrazene obdobie +-8 %.
    shown = t >= show_from
    p_lo, p_hi = float(np.min(l[shown])) * (1 - _MARGIN), float(np.max(h[shown])) * (1 + _MARGIN)
    edges = np.exp(np.linspace(np.log(p_lo), np.log(p_hi), _ROWS + 1))
    m = np.searchsorted(edges, grid) - 1
    valid = (m >= 0) & (m < _ROWS)
    mat = np.array([np.bincount(m[valid], weights=col[valid], minlength=_ROWS) for col in cols])
    # 0-255 voci 99. percentilu nenulovych buniek - jeden extremny zhluk by inak
    # vsetko ostatne zatemnil (Coinglass ma na to posuvnik "liquidity threshold").
    nz = mat[mat > 0]
    scale = float(np.percentile(nz, 99)) if nz.size else 1.0
    q = np.clip(np.round(mat / scale * 255), 0, 255).astype(np.uint8)

    # Aktualny stav: nerovnovaha v okne +-5 ATR(1h) a najvacsi zhluk nad/pod cenou.
    price = float(c[-1])
    hourly_idx = np.where(minutes == 55)[0]
    hc = c[hourly_idx]
    hh = np.maximum.reduceat(h, np.r_[0, hourly_idx[:-1] + 1])[: len(hourly_idx)]
    hl = np.minimum.reduceat(l, np.r_[0, hourly_idx[:-1] + 1])[: len(hourly_idx)]
    tr = np.maximum(hh[1:] - hl[1:], np.maximum(abs(hh[1:] - hc[:-1]), abs(hl[1:] - hc[:-1])))
    atr = float(tr[0]) if len(tr) else price * 0.01
    for x in tr[1:]:
        atr += (float(x) - atr) / 14
    # Okno aspon +-3 % ceny - v pokojnom trhu je 5 ATR uzsich nez vzdialenost
    # likvidacii aj pri 100x paku a nerovnovaha by vychadzala nahodne +-1.
    win = max(IMBALANCE_ATR * atr, IMBALANCE_MIN_PCT / 100 * price)
    up_m = (grid > price) & (grid <= price + win)
    dn_m = (grid < price) & (grid >= price - win)
    up_usd = float((short_l[up_m] * grid[up_m]).sum())
    dn_usd = float((long_l[dn_m] * grid[dn_m]).sum())
    last_row = mat[-1]
    centers = np.sqrt(edges[:-1] * edges[1:])

    def top(mask):
        if not mask.any():
            return None
        i = int(np.argmax(np.where(mask, last_row, -1)))
        return {"price": float(centers[i]), "usd": float(last_row[i]),
                "dist_pct": (float(centers[i]) / price - 1) * 100}

    return {
        "model": "OI Binance 5 min, paky 10/25/50/100x (35/30/20/15 %), MMR 0.5 %",
        "t0": str(np.datetime_as_string(col_t[0], unit="m")) + "Z",
        "step_minutes": 60,
        "cols": len(cols),
        "rows": _ROWS,
        "edges": [float(x) for x in edges],
        "scale_usd": scale,
        "matrix_b64": base64.b64encode(q.tobytes()).decode("ascii"),   # po stlpcoch (cas), v kazdom _ROWS riadkov
        "price_line": col_c,
        "price": price,
        "atr14_1h": atr,
        "imbalance": (up_usd - dn_usd) / (up_usd + dn_usd) if up_usd + dn_usd > 0 else None,
        "imbalance_window_pct": win / price * 100,
        "above_usd": up_usd, "below_usd": dn_usd,
        # Najvacsi zhluk V TOM ISTOM OKNE ako nerovnovaha - na okraji mapy (+-10 %)
        # je vzdy pas z predpokladanej 10x paky, co je artefakt modelu, nie trh.
        "top_above": top((centers > price) & (centers <= price + win)),
        "top_below": top((centers < price) & (centers >= price - win)),
        "oi_coins": float(oi[-1]),
        "data_from": str(np.datetime_as_string(t[0], unit="m")) + "Z",
        "data_to": str(np.datetime_as_string(t[-1], unit="m")) + "Z",
    }


def _compute(session, symbol: str) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_MODEL_DAYS)
    rows = (session.query(BinanceOiBar.t, BinanceOiBar.o, BinanceOiBar.h, BinanceOiBar.l,
                          BinanceOiBar.c, BinanceOiBar.oi)
            .filter(BinanceOiBar.symbol == symbol, BinanceOiBar.t >= since)
            .order_by(BinanceOiBar.t).all())
    if not rows:
        raise ValueError("ziadne data v binance_oi_bars")
    t = np.array([r[0] for r in rows], dtype="datetime64[m]")
    arr = np.array([r[1:] for r in rows], dtype=float)
    return build_heatmap(t, arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])


def poll_all() -> None:
    """Scheduler job (main.py, kazdych 15 min) - pre kazdy ticker s binance_liq_symbol."""
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            bsym = asset.get("binance_liq_symbol")
            if not bsym:
                continue
            symbol = asset["strike_symbol"]
            row = session.get(LiqHeatmap, symbol) or LiqHeatmap(symbol=symbol)
            row.computed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                new = _fetch_new(session, symbol, bsym)
                row.payload = _compute(session, symbol)
                row.ok, row.error = True, None
                print(f"[liq_heatmap] {asset['name']}: +{new} barov, nerovnovaha "
                      f"{row.payload['imbalance'] if row.payload['imbalance'] is not None else '-'}")
            except Exception as e:
                session.rollback()
                row = session.get(LiqHeatmap, symbol) or LiqHeatmap(symbol=symbol)
                row.computed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                row.ok, row.error = False, f"{type(e).__name__}: {str(e)[:200]}"
                print(f"[liq_heatmap] {asset['name']} zlyhal (neblokujuce): {row.error}")
            session.merge(row)
            session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    poll_all()
