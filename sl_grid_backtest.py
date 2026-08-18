""""Zivy" TOP-5 rebricek SL/TP kandidatov PER TICKER (2026-08-19, prepracovane
z povodnej POOLED verzie na ziadost pouzivatela) - kazdy ticker ma VLASTNY
grid search nad VLASTNYMI uzavretymi obchodmi a VLASTNY pocitadlo vzorky,
nie zdielany naprieč vsetkymi tickermi.

POZOR (zamerne ponechane, nie chyba): pri malom pocte obchodov na ticker
(dnes 1-8 na vela z nich) je vysledok statisticky slaby - presne preto
kazdy kandidat nesie trade_count a frontend farebne vyznacuje, ci uz ticker
dosiahol dovervyhodny prah (pouzivatel navrhol ~20).

Metodika (na rozdiel od povodneho, chybneho sl_calibration._sweep_k, ktory
testoval NAHODNE body v historii - viz jeho docstring/komentare a memory
poznamka pending_sl_calibration_methodology_fix): tento grid search pouziva
VYHRADNE SKUTOCNE vstupy bota (entry price/smer/cas z realnych Trade
zaznamov), kedze tie nesu genuinne smerove presvedcenie Claude-a, ktore
nahodny bod nema.

Pre kazdu kombinaciu (SL_k, TP_k) z mriezky sa simuluje first-touch (SL vs
TP, ktory sa dotkne skor) na REALNEJ nasledujucej cenovej ceste (hodinove
PriceBar, okno POSITION_MAX_HOURS - rovnaka politika ako zivy bot), s
leverage prepocitanou cez rovnaky vzorec ako risk_manager (_leverage_from_cushion),
aby $ PnL bolo realisticke.

Lokalna citlivost (2026-08-19): pre TOP 1 kandidata kazdeho tickera sa navyse
otestuje plna 3x3 mriezka okolo neho (viz _LOCAL_SENSITIVITY_OFFSETS) - ukaze,
ci #1 sedi v stabilnej plosine alebo je to osamely vrchol/sum.

Event-driven prepocet (2026-08-19, na ziadost pouzivatela): PREPOCET UZ NIE JE
scheduler job bezici raz denne pre VSETKY tickery - vysledok zavisi VYHRADNE
od historie obchodov daneho tickera, takze denny beh pre tickery bez noveho
uzavreteho obchodu bol cisty odpad (a naopak, hned po uzavreti bol rebricek
az 24h neaktualny). Namiesto toho position_monitor.py vola recompute_symbol()
PRIAMO PO uzavreti kazdeho obchodu, LEN pre ten jeden ticker. compute_leaderboard()
zostava ako manualny/backfill vstupny bod (napr. `python sl_grid_backtest.py`).

S/R rozsirenie (bolo tu 2026-08-19, ZRUSENE tym istym dnom) - pouzivatel od
neho upustil, kedze S/R tabulka bola cisto informativna (tlacidlo na
aplikovanie by robilo presne to iste ako ATR tabulka - zapis % do
risk_overrides) a jej realny zmysel by vyzadoval upravu Claude promptu, aby
sam hladal S/R pri kazdom cykle - prilis vela premennych naraz. Viz memory
poznamka pending_sr_calibration_shelved, ak sa k tomu bude chciet niekto
niekedy vratit."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pandas_ta as ta

import config
import risk_manager
import strike_client
from db import (CycleLog, PriceBar, SlTpBacktestCandidate, SlTpBacktestCandidateConstrained,
                 SlTpLocalSensitivity, SlTpLocalSensitivityConstrained, SlTpRecomputeStatus, Trade, get_session)


def _grid_range(start: float, stop: float, step: float) -> list[float]:
    """Aritmeticky rad start..stop (vratane) po step, zaokruhleny na 2
    desatinne miesta (predide plavakovym artefaktom ako 1.7999999999998)."""
    n = round((stop - start) / step)
    return [round(start + i * step, 2) for i in range(n + 1)]


# Jednotna 0.5 granularita, SYMETRICKY rozsah 0.5-20 na oboch osiach
# (2026-08-19, na ziadost pouzivatela - povodna mriezka mala 0.5 kroky len v
# nizsom rozsahu a potom skakala po celych cislach 3->4->5->7->10->15->20,
# a TP mala len do 12 kym SL do 20). Overene, ze SL_k=7-10 uz vykazuje POKLES
# oproti SL_k=5 (viz memory pending_sl_calibration_methodology_fix), nie je
# to artefakt kraja mriezky. #1 kandidat sa dalej rozpada na jemnejsie
# +-0.25 v lokalnej citlivosti nizsie. 40x40 = 1600 kombinacii - lacne
# (ziadne I/O v smycke), beziace len event-driven po uzavreti obchodu, nie
# kontinualne.
_SL_K_GRID = _grid_range(0.5, 20.0, 0.5)
_TP_K_GRID = _grid_range(0.5, 20.0, 0.5)

_CLOSED_STATUSES = ["closed_by_exchange", "closed_by_timeout", "closed_by_safety", "closed_by_user"]

_TOP_N = 5

# Minimalny TP:SL pomer pre "disciplinovany" rebricek (2026-08-19, na ziadost
# pouzivatela) - povodny bot bol navrhnuty s TP=1.5xSL zamerne (viz stary
# system prompt), nie nahodou. Cisto volny grid search moze najst kombinacie
# so SL>=TP, ktore su ziskove LEN ak je odhadnuty win rate (z malej vzorky)
# presny - taky pomer nema ZIADNU rezervu proti chybe odhadu (matematicky:
# potrebuje win_rate > SL/(SL+TP), co pri SL>=TP znamena >50%, kym pri
# TP=1.5xSL stac >40%). Oba rebricky (volny aj obmedzeny) sa pocitaju z
# JEDNEJ simulacie (_run_full_grid) - obmedzeny je len filter nad tymi
# istymi vysledkami, ziadna druha simulacia netreba.
_MIN_REWARD_RISK_RATIO = 1.5

# Lokalna citlivostna analyza okolo #1 (2026-08-19, na ziadost pouzivatela -
# povodna verzia testovala kazdu os OSOBITNE s +-0.25/+-0.5, pouzivatel ju
# zjednodusil na PLNU 3x3 mriezku (vsetky kombinacie oboch osi), ale LEN
# +-0.25 (ziadne +-0.5) - menej "osamelych" testov, ale zato pokryva aj
# diagonalne kombinacie (napr. SL+0.25 A ZAROVEN TP-0.25 naraz).
# (label, sort_order, sl_delta, tp_delta).
_LOCAL_SENSITIVITY_OFFSETS = [
    ("SL-0.25/TP-0.25", 0, -0.25, -0.25),
    ("SL-0.25", 1, -0.25, 0.0),
    ("SL-0.25/TP+0.25", 2, -0.25, 0.25),
    ("TP-0.25", 3, 0.0, -0.25),
    ("base", 4, 0.0, 0.0),
    ("TP+0.25", 5, 0.0, 0.25),
    ("SL+0.25/TP-0.25", 6, 0.25, -0.25),
    ("SL+0.25", 7, 0.25, 0.0),
    ("SL+0.25/TP+0.25", 8, 0.25, 0.25),
]


def _prepare_trade(t: Trade, session, market_meta_cache: dict) -> dict | None:
    """Pripravi vsetko potrebne pre jeden realny obchod (entry/smer/ATR% v
    case vstupu/forward bary/leverage-vstupy) - None ak dat nie je dost ALEBO
    ak cenova historia este NEPOKRYVA CELE 24h okno (viz nizsie).

    KRITICKY guard (2026-08-19, na ziadost pouzivatela): ak sa obchod zatvoril
    SKOR ako za POSITION_MAX_HOURS (napr. SL po 2h), grid search tu skusa aj
    SIRSIE hypoteticke SL/TP kombinacie, ktore skutocny obchod nikdy nepouzil -
    tie potrebuju cenovu historiu AZ DO KONCA 24h okna, inak by sa "no_touch"
    vetva (_first_touch_pnl) ticho pozrela na cenu na hranici NEUPLNYCH dat
    (napr. po 2h) namiesto skutocnej ceny na konci 24h - falosny "predcasny
    timeout" vysledok pre kazdu sirsiu kombinaciu. Preto NESTACI, ze fwd_bars
    nie je prazdny - musia siahat (s 1h tolerantnou rezervou na aktualne
    formovanu hodinu) az do window_end. Bez tohto guardu by okamzity
    event-driven prepocet (viz position_monitor._check_and_queue_recompute)
    davat nespravne vysledky pre kazdy obchod zatvoreny predcasne - presne
    preto sa prepocet teraz aj ODKLADA (recompute_due_at), aby tu tento guard
    v beznej prevadzke skoro nikdy neodmietol pripraveny obchod."""
    cl = session.query(CycleLog).filter(CycleLog.trade_id == t.id, CycleLog.outcome == "opened").first()
    if cl is None or cl.config_snapshot is None:
        return None
    cushion_multiple = cl.config_snapshot.get("liquidation_cushion_multiple", 1.5)

    entry = t.entry_price
    is_long = (t.direction or "").lower() == "long"
    opened_at = t.opened_at
    window_end = opened_at + timedelta(hours=config.POSITION_MAX_HOURS)

    all_bars = (
        session.query(PriceBar)
        .filter(PriceBar.symbol == t.symbol, PriceBar.hour_start <= window_end)
        .order_by(PriceBar.hour_start)
        .all()
    )
    hist_bars = [b for b in all_bars if b.hour_start <= opened_at]
    fwd_bars = [b for b in all_bars if b.hour_start > opened_at]
    if len(hist_bars) < 30 or not fwd_bars:
        return None
    if fwd_bars[-1].hour_start < window_end - timedelta(hours=1):
        return None

    edf = pd.DataFrame([{"high": b.high, "low": b.low, "close": b.close} for b in hist_bars])
    edf["atr14"] = ta.atr(edf["high"], edf["low"], edf["close"], length=14)
    if pd.isna(edf["atr14"].iloc[-1]):
        return None
    atr_pct_entry = float(edf["atr14"].iloc[-1]) / entry * 100
    if atr_pct_entry <= 0:
        return None

    if t.symbol not in market_meta_cache:
        try:
            market_meta_cache[t.symbol] = strike_client.get_market(t.symbol)
        except Exception as e:
            print(f"[sl_grid_backtest] get_market({t.symbol}) zlyhalo: {e}")
            market_meta_cache[t.symbol] = None
    market_meta = market_meta_cache[t.symbol]
    if market_meta is None:
        return None

    return {
        "trade": t, "entry": entry, "is_long": is_long, "atr_pct": atr_pct_entry,
        "fwd_bars": fwd_bars, "cushion_multiple": cushion_multiple, "market_meta": market_meta,
    }


def _leverage_for(p: dict, sl_dist: float):
    return risk_manager._leverage_from_cushion(
        sl_dist, p["entry"], p["trade"].margin_usd, p["market_meta"]["margin_tiers"], p["cushion_multiple"],
    )


def _first_touch_pnl(p: dict, sl_price: float, tp_price: float, sl_pct: float, tp_pct: float,
                      leverage: float) -> float:
    entry = p["entry"]
    is_long = p["is_long"]
    outcome = "no_touch"
    for b in p["fwd_bars"]:
        hit_sl = (b.low <= sl_price) if is_long else (b.high >= sl_price)
        hit_tp = (b.high >= tp_price) if is_long else (b.low <= tp_price)
        if hit_sl:
            outcome = "sl"
            break
        if hit_tp:
            outcome = "tp"
            break
    if outcome == "sl":
        return p["trade"].margin_usd * leverage * (-sl_pct / 100)
    if outcome == "tp":
        return p["trade"].margin_usd * leverage * (tp_pct / 100)
    last_close = p["fwd_bars"][-1].close
    pnl_pct = ((last_close - entry) / entry) if is_long else ((entry - last_close) / entry)
    return p["trade"].margin_usd * leverage * pnl_pct


def _simulate(prepared: list[dict], sl_k: float, tp_k: float) -> dict | None:
    """First-touch simulacia danej (sl_k, tp_k) kombinacie naprieč VSETKYMI
    pripravenymi obchodmi (uz filtrovanymi na jeden ticker volajucim)."""
    total_pnl = 0.0
    wins = 0
    n = 0
    for p in prepared:
        entry = p["entry"]
        sl_dist = entry * (sl_k * p["atr_pct"] / 100)
        tp_dist = entry * (tp_k * p["atr_pct"] / 100)
        if sl_dist <= 0:
            continue
        sl_price = entry - sl_dist if p["is_long"] else entry + sl_dist
        tp_price = entry + tp_dist if p["is_long"] else entry - tp_dist

        try:
            leverage = _leverage_for(p, sl_dist)
        except Exception:
            continue

        pnl = _first_touch_pnl(p, sl_price, tp_price, sl_k * p["atr_pct"], tp_k * p["atr_pct"], leverage)
        total_pnl += pnl
        wins += 1 if pnl > 0 else 0
        n += 1

    if n == 0:
        return None
    return {"sl_k": sl_k, "tp_k": tp_k, "total_pnl": total_pnl, "win_rate": wins / n, "trade_count": n}


def _run_full_grid(prepared_for_symbol: list[dict]) -> list[dict]:
    """Cely (sl_k, tp_k) grid search pre JEDEN ticker (uz filtrovane obchody)
    - vrati VSETKY pouzitelne vysledky (neusporiadane, nefiltrovane). _top_n()
    nizsie z nich odvodi volny aj 1.5x-obmedzeny rebricek bez druhej
    simulacie."""
    results = []
    for sl_k in _SL_K_GRID:
        for tp_k in _TP_K_GRID:
            r = _simulate(prepared_for_symbol, sl_k, tp_k)
            if r is not None:
                results.append(r)
    return results


def _top_n(results: list[dict], min_reward_risk_ratio: float | None = None) -> list[dict]:
    """TOP _TOP_N z uz vypocitanych vysledkov (viz _run_full_grid), zoradene
    podla total_pnl. Ak je zadany min_reward_risk_ratio, najskor zahodi
    kombinacie s TP_k < min_reward_risk_ratio * SL_k (viz _MIN_REWARD_RISK_RATIO)."""
    pool = results
    if min_reward_risk_ratio is not None:
        pool = [r for r in pool if r["tp_k"] >= min_reward_risk_ratio * r["sl_k"]]
    if not pool:
        return []
    return sorted(pool, key=lambda r: -r["total_pnl"])[:_TOP_N]


def _run_local_sensitivity_for_symbol(prepared_for_symbol: list[dict], base_sl_k: float, base_tp_k: float,
                                       min_reward_risk_ratio: float | None = None) -> list[dict]:
    """Otestuje plnu 3x3 mriezku okolo #1 (base_sl_k, base_tp_k), vsetky
    kombinacie delta z {-0.25, 0, +0.25} na oboch osiach (viz
    _LOCAL_SENSITIVITY_OFFSETS) - _simulate() je uz genericka na lubovolny
    float sl_k/tp_k, ziadny novy simulacny kod netreba. Varianty s vysledym
    sl_k/tp_k <= 0 (napr. base SL_k=0.25 mensi o 0.25 = 0) sa jednoducho
    preskocia. Ak je zadany min_reward_risk_ratio (2026-08-19, pre
    "obmedzenu" citlivost okolo #1 z uz-obmedzeneho rebricka), preskocia sa
    aj varianty, ktore by pomer poslali POD tuto hranicu (napr. TP-0.25 pri
    #1 presne na hranici 1.5x) - inak by "obmedzena" tabulka nekonzistentne
    obsahovala presne to, co ma vylucovat."""
    results = []
    for variant, sort_order, sl_delta, tp_delta in _LOCAL_SENSITIVITY_OFFSETS:
        sl_k = base_sl_k + sl_delta
        tp_k = base_tp_k + tp_delta
        if sl_k <= 0 or tp_k <= 0:
            continue
        if min_reward_risk_ratio is not None and tp_k < min_reward_risk_ratio * sl_k:
            continue
        r = _simulate(prepared_for_symbol, sl_k, tp_k)
        if r is None:
            continue
        r["variant"] = variant
        r["sort_order"] = sort_order
        results.append(r)
    return results


def _write_candidates(session, symbol: str, top: list[dict], model, label: str) -> None:
    """Spolocny zapis pre volny/obmedzeny rebricek (rovnaky tvar riadku,
    iny model/tabulka)."""
    print(f"[sl_grid_backtest] [{symbol}] {label}:")
    for i, r in enumerate(top, start=1):
        print(f"  #{i}: SL_k={r['sl_k']} TP_k={r['tp_k']} total_pnl=${r['total_pnl']:.2f} "
              f"win_rate={r['win_rate']*100:.0f}% n={r['trade_count']}")
        session.add(model(
            symbol=symbol, rank=i, sl_k=r["sl_k"], tp_k=r["tp_k"], total_pnl=r["total_pnl"],
            win_rate=r["win_rate"], trade_count=r["trade_count"],
        ))


def _write_sensitivity(session, symbol: str, prepared: list[dict], base_sl_k: float, base_tp_k: float,
                        model, label: str, min_reward_risk_ratio: float | None = None) -> None:
    """Spolocny zapis lokalnej citlivosti pre volny/obmedzeny rebricek."""
    sensitivity = _run_local_sensitivity_for_symbol(prepared, base_sl_k, base_tp_k, min_reward_risk_ratio)
    print(f"[sl_grid_backtest] [{symbol}] lokalna citlivost {label} okolo #1 ({len(sensitivity)}/9 pouzitelnych):")
    for r in sensitivity:
        print(f"  {r['variant']}: SL_k={r['sl_k']} TP_k={r['tp_k']} total_pnl=${r['total_pnl']:.2f} "
              f"win_rate={r['win_rate']*100:.0f}%")
        session.add(model(
            symbol=symbol, variant=r["variant"], sort_order=r["sort_order"],
            sl_k=r["sl_k"], tp_k=r["tp_k"], total_pnl=r["total_pnl"],
            win_rate=r["win_rate"], trade_count=r["trade_count"],
        ))


def recompute_symbol(symbol: str) -> None:
    """Prepocita OBA TOP-5 grid-search rebricky (volny + 1.5x-obmedzeny,
    viz _MIN_REWARD_RISK_RATIO) + ich lokalne citlivosti LEN pre JEDEN
    ticker (2026-08-19, na ziadost pouzivatela) - volane z position_monitor.py
    po uzavreti obchodu tohto tickera (event-driven, s odkladom - viz
    position_monitor._check_and_queue_recompute), namiesto povodneho
    globalneho 24h scheduler jobu (viz docstring modulu vyssie). Maze a znova
    zapisuje LEN riadky tohto symbolu (nie cele tabulky) - iny ticker sa
    mohol medzitym prepocitat nezavisle vo vlastnom volani. Simulacia
    (_run_full_grid) bezi LEN RAZ - oba rebricky su len rozne filtre/top-5
    nad tymi istymi vysledkami."""
    print(f"\n=== [sl_grid_backtest] recompute_symbol({symbol}) {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        closed = (
            session.query(Trade)
            .filter(Trade.symbol == symbol, Trade.status.in_(_CLOSED_STATUSES))
            .order_by(Trade.opened_at)
            .all()
        )
        if not closed:
            print(f"[sl_grid_backtest] [{symbol}] ziadne uzavrete obchody, preskakujem.")
            return

        market_meta_cache: dict = {}
        prepared = []
        for t in closed:
            p = _prepare_trade(t, session, market_meta_cache)
            if p is not None:
                prepared.append(p)

        session.query(SlTpBacktestCandidate).filter(SlTpBacktestCandidate.symbol == symbol).delete()
        session.query(SlTpLocalSensitivity).filter(SlTpLocalSensitivity.symbol == symbol).delete()
        session.query(SlTpBacktestCandidateConstrained).filter(
            SlTpBacktestCandidateConstrained.symbol == symbol).delete()
        session.query(SlTpLocalSensitivityConstrained).filter(
            SlTpLocalSensitivityConstrained.symbol == symbol).delete()

        # 2026-08-19 (na ziadost pouzivatela) - VZDY zapiseme "kedy naposledy
        # prepocitane + z kolkych POUZITELNYCH (t.j. uz s kompletnou 24h
        # cenovou historiou, viz _prepare_trade guard) obchodov", aj ked grid
        # search nizsie nenajde ziadne pouzitelne vysledky - dashboard tak
        # vzdy vie ukazat "ako cerstvy" je rebricek, nie len ked sa podari.
        session.merge(SlTpRecomputeStatus(
            symbol=symbol, computed_at=datetime.now(timezone.utc), closed_trade_count=len(prepared),
        ))

        full_results = _run_full_grid(prepared)

        top_free = _top_n(full_results)
        if top_free:
            _write_candidates(session, symbol, top_free, SlTpBacktestCandidate,
                               f"volny rebricek ({len(prepared)} obchodov)")
            _write_sensitivity(session, symbol, prepared, top_free[0]["sl_k"], top_free[0]["tp_k"],
                                SlTpLocalSensitivity, "(volny)")
        else:
            print(f"[sl_grid_backtest] [{symbol}] volny rebricek: ziadne pouzitelne vysledky "
                  f"({len(prepared)} obchodov).")

        top_constrained = _top_n(full_results, min_reward_risk_ratio=_MIN_REWARD_RISK_RATIO)
        if top_constrained:
            _write_candidates(session, symbol, top_constrained, SlTpBacktestCandidateConstrained,
                               f"obmedzeny rebricek TP>={_MIN_REWARD_RISK_RATIO}xSL ({len(prepared)} obchodov)")
            _write_sensitivity(session, symbol, prepared, top_constrained[0]["sl_k"], top_constrained[0]["tp_k"],
                                SlTpLocalSensitivityConstrained, "(obmedzeny)",
                                min_reward_risk_ratio=_MIN_REWARD_RISK_RATIO)
        else:
            print(f"[sl_grid_backtest] [{symbol}] obmedzeny rebricek: ziadne pouzitelne vysledky "
                  f"(TP>={_MIN_REWARD_RISK_RATIO}xSL, {len(prepared)} obchodov).")

        session.commit()
    finally:
        session.close()


def compute_leaderboard() -> None:
    """Manualny/backfill vstupny bod (NIE VIAC scheduler job - viz docstring
    modulu vyssie, prepocet je teraz event-driven cez recompute_symbol()
    volany z position_monitor.py po kazdom uzavreti obchodu). Prejde vsetky
    tickery so aspon jednym uzavretym obchodom a zavola pre kazdy
    recompute_symbol() - uzitocne na jednorazovy manualny beh (napr. `python
    sl_grid_backtest.py`), nie na priebeznu prevadzku."""
    print(f"\n=== [sl_grid_backtest] compute_leaderboard (manualny beh vsetkych tickerov) {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        symbols = [
            row[0] for row in
            session.query(Trade.symbol).filter(Trade.status.in_(_CLOSED_STATUSES)).distinct().all()
        ]
    finally:
        session.close()
    if not symbols:
        print("[sl_grid_backtest] Ziadne uzavrete obchody, preskakujem.")
        return
    for symbol in symbols:
        recompute_symbol(symbol)


if __name__ == "__main__":
    compute_leaderboard()
