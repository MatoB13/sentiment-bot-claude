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

S/R rozsirenie (2026-08-19): pre TOP 5 uz vybranych (sl_k, tp_k) kandidatov
danho tickera sa navyse simuluje S/R-upravena verzia - namiesto cisteho
k*ATR sa SL/TP "prichyti" na najblizsiu support/resistance uroven (swing
high/low z HISTORIE TOHTO TICKERA PRED danym vstupom - ziadny look-ahead),
s malym bufferom (SL kusok ZA najdenou urovnou, TP kusok PRED nou).
compute_sr_calibration() potom pre kazdy ticker/rank prepocita AKTUALNU
(dnesnu) S/R-prichytenu % hodnotu - toto feeduje druhu (S/R) tabulku v
per-ticker "Kalibracia SL/TP" tabe. POZOR: S/R tabulka je zatial CISTO
INFORMATIVNA (bez tlacidla na aplikovanie) - jej realny zmysel príde az
neskor, ked bude Claude prompt upraveny, aby sam hladal S/R pri kazdom
cykle (viz diskusia s pouzivatelom 2026-08-19)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pandas_ta as ta

import assets
import config
import market_data
import risk_manager
import strike_client
from db import CycleLog, PriceBar, SlTpBacktestCandidate, SrCalibration, Trade, get_session

# Rovnaka mriezka, aka sa pouzila na jednorazovy grid search 2026-08-19 (viz
# memory pending_sl_calibration_methodology_fix) - siroke pokrytie navyse
# overene, ze SL_k=5-10 uz vykazuje POKLES oproti SL_k=5 (nie je to len
# artefakt kraja mriezky).
_SL_K_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0]
_TP_K_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]

_CLOSED_STATUSES = ["closed_by_exchange", "closed_by_timeout", "closed_by_safety", "closed_by_user"]

_TOP_N = 5

# S/R detekcia: fraktalovy pivot - bar je swing high/low, ak je jeho high/low
# extrem v ramci +-_PIVOT_WINDOW barov.
_PIVOT_WINDOW = 3
# Kolko % vzdialenosti (entry->uroven) sa SL posunie ZA najdenu uroven (extra
# priestor) a TP PRED najdenu uroven (nie presne na nu - cena sa casto
# odrazi presne na resistencii/podpore).
_SR_BUFFER_FRACTION = 0.10


def _find_swings(highs: list[float], lows: list[float]) -> tuple[list[float], list[float]]:
    """Vrati (swing_high_prices, swing_low_prices) z paralelnych zoznamov
    high/low cien."""
    n = len(highs)
    swing_highs, swing_lows = [], []
    for i in range(_PIVOT_WINDOW, n - _PIVOT_WINDOW):
        window_h = highs[i - _PIVOT_WINDOW:i + _PIVOT_WINDOW + 1]
        window_l = lows[i - _PIVOT_WINDOW:i + _PIVOT_WINDOW + 1]
        if highs[i] == max(window_h):
            swing_highs.append(highs[i])
        if lows[i] == min(window_l):
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def _nearest_level(levels: list[float], reference: float) -> float | None:
    """Najblizsia uroven K REFERENCII (moze byt blizsie aj dalej od entry) -
    None ak ziadna uroven."""
    if not levels:
        return None
    return min(levels, key=lambda lv: abs(lv - reference))


def _apply_sr_buffer(sl_level: float | None, tp_level: float | None, sl_ref: float, tp_ref: float,
                      entry: float, is_long: bool) -> tuple[float, float]:
    """Aplikuje buffer na najdene S/R urovne (alebo padne spat na cisty ATR
    referencny bod, ak sa ziadna uroven nenasla): SL sa posunie o
    _SR_BUFFER_FRACTION dalej OD entry (za urovnou), TP o rovnaky podiel
    BLIZSIE k entry (pred urovnou)."""
    away_sign_sl = -1 if is_long else 1  # smer "dalej od entry" pre SL stranu
    away_sign_tp = 1 if is_long else -1  # smer "dalej od entry" pre TP stranu

    if sl_level is not None:
        actual_sl = sl_level + away_sign_sl * _SR_BUFFER_FRACTION * abs(sl_level - entry)
    else:
        actual_sl = sl_ref
    if tp_level is not None:
        actual_tp = tp_level - away_sign_tp * _SR_BUFFER_FRACTION * abs(tp_level - entry)
    else:
        actual_tp = tp_ref
    return actual_sl, actual_tp


def _prepare_trade(t: Trade, session, market_meta_cache: dict) -> dict | None:
    """Pripravi vsetko potrebne pre jeden realny obchod (entry/smer/ATR% v
    case vstupu/forward bary/S/R swingy/leverage-vstupy) - None ak dat nie
    je dost."""
    cl = session.query(CycleLog).filter(CycleLog.trade_id == t.id, CycleLog.outcome == "opened").first()
    if cl is None or cl.config_snapshot is None:
        return None
    cushion_multiple = cl.config_snapshot.get("liquidation_cushion_multiple", 1.5)

    entry = t.entry_price
    is_long = (t.direction or "").lower() == "long"
    opened_at = t.opened_at
    window_end = opened_at + timedelta(hours=config.POSITION_MAX_HOURS)

    # Ziadna dolna hranica na hour_start - S/R detekcia potrebuje CO NAJVIAC
    # historie PRED vstupom (ziadny look-ahead), bot zatial bezi len ~3
    # tyzdne, takze objem dat ostava maly.
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

    edf = pd.DataFrame([{"high": b.high, "low": b.low, "close": b.close} for b in hist_bars])
    edf["atr14"] = ta.atr(edf["high"], edf["low"], edf["close"], length=14)
    if pd.isna(edf["atr14"].iloc[-1]):
        return None
    atr_pct_entry = float(edf["atr14"].iloc[-1]) / entry * 100
    if atr_pct_entry <= 0:
        return None

    swing_highs, swing_lows = _find_swings(
        [b.high for b in hist_bars], [b.low for b in hist_bars],
    )

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
        "fwd_bars": fwd_bars, "swing_highs": swing_highs, "swing_lows": swing_lows,
        "cushion_multiple": cushion_multiple, "market_meta": market_meta,
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
    """First-touch simulacia cisteho k*ATR (bez S/R) danej (sl_k, tp_k)
    kombinacie naprieč VSETKYMI pripravenymi obchodmi (uz filtrovanymi na
    jeden ticker volajucim)."""
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


def _simulate_sr(prepared: list[dict], sl_k: float, tp_k: float) -> dict | None:
    """First-touch simulacia S/R-upravenej verzie danej (sl_k, tp_k) - k*ATR
    slúži len ako referencny bod, skutocny SL/TP sa prichyti na najblizsiu
    S/R uroven z historie PRED danym vstupom (+ buffer, viz _apply_sr_buffer)."""
    total_pnl = 0.0
    wins = 0
    n = 0
    for p in prepared:
        entry = p["entry"]
        is_long = p["is_long"]
        sl_ref_dist = entry * (sl_k * p["atr_pct"] / 100)
        tp_ref_dist = entry * (tp_k * p["atr_pct"] / 100)
        if sl_ref_dist <= 0:
            continue
        sl_ref = entry - sl_ref_dist if is_long else entry + sl_ref_dist
        tp_ref = entry + tp_ref_dist if is_long else entry - tp_ref_dist

        sl_side_levels = p["swing_lows"] if is_long else p["swing_highs"]
        tp_side_levels = p["swing_highs"] if is_long else p["swing_lows"]
        sl_level = _nearest_level(sl_side_levels, sl_ref)
        tp_level = _nearest_level(tp_side_levels, tp_ref)
        actual_sl, actual_tp = _apply_sr_buffer(sl_level, tp_level, sl_ref, tp_ref, entry, is_long)

        final_sl_dist = abs(entry - actual_sl)
        if final_sl_dist <= 0:
            continue

        try:
            leverage = _leverage_for(p, final_sl_dist)
        except Exception:
            continue

        actual_sl_pct = final_sl_dist / entry * 100
        actual_tp_pct = abs(actual_tp - entry) / entry * 100
        pnl = _first_touch_pnl(p, actual_sl, actual_tp, actual_sl_pct, actual_tp_pct, leverage)
        total_pnl += pnl
        wins += 1 if pnl > 0 else 0
        n += 1

    if n == 0:
        return None
    return {"total_pnl": total_pnl, "win_rate": wins / n, "trade_count": n}


def _run_grid_for_symbol(prepared_for_symbol: list[dict]) -> list[dict]:
    """Cely (sl_k, tp_k) grid search + S/R rozsirenie pre JEDEN ticker (uz
    filtrovane obchody) - vrati TOP N zoradenych podla total_pnl, kazdy
    doplneny o sr_total_pnl/sr_win_rate."""
    results = []
    for sl_k in _SL_K_GRID:
        for tp_k in _TP_K_GRID:
            r = _simulate(prepared_for_symbol, sl_k, tp_k)
            if r is not None:
                results.append(r)
    if not results:
        return []
    results.sort(key=lambda r: -r["total_pnl"])
    top = results[:_TOP_N]
    for r in top:
        sr = _simulate_sr(prepared_for_symbol, r["sl_k"], r["tp_k"])
        r["sr_total_pnl"] = sr["total_pnl"] if sr else None
        r["sr_win_rate"] = sr["win_rate"] if sr else None
    return top


def compute_leaderboard() -> None:
    """Vstupny bod scheduleru (main.py, denne) - PRE KAZDY ticker nezavisle
    prepocita grid search NAD VLASTNYMI obchodmi a prepise jeho TOP 5 v
    SlTpBacktestCandidate (VRATANE S/R-upravenej PnL/win_rate). Kazdy ticker
    ma VLASTNY pocet obchodov vo vzorke (trade_count) - ziadne pozicanie
    statistickej sily od inych tickerov (2026-08-19 prepracovane z povodnej
    pooled verzie, viz memory poznamka a diskusia s pouzivatelom). Nic sa
    NIKDY automaticky neaplikuje do RiskOverride - cisto informativny,
    priebezne sa aktualizujuci rebricek."""
    print(f"\n=== [sl_grid_backtest] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        closed = (
            session.query(Trade)
            .filter(Trade.status.in_(_CLOSED_STATUSES))
            .order_by(Trade.opened_at)
            .all()
        )
        if not closed:
            print("[sl_grid_backtest] Ziadne uzavrete obchody, preskakujem.")
            return

        market_meta_cache: dict = {}
        by_symbol: dict[str, list[dict]] = {}
        for t in closed:
            p = _prepare_trade(t, session, market_meta_cache)
            if p is not None:
                by_symbol.setdefault(t.symbol, []).append(p)

        session.query(SlTpBacktestCandidate).delete()
        for symbol, prepared in by_symbol.items():
            top = _run_grid_for_symbol(prepared)
            if not top:
                print(f"[sl_grid_backtest] [{symbol}] ziadne pouzitelne vysledky ({len(prepared)} obchodov), preskakujem.")
                continue
            print(f"[sl_grid_backtest] [{symbol}] ({len(prepared)} obchodov):")
            for i, r in enumerate(top, start=1):
                sr_pnl, sr_wr = r["sr_total_pnl"], r["sr_win_rate"]
                print(f"  #{i}: SL_k={r['sl_k']} TP_k={r['tp_k']} total_pnl=${r['total_pnl']:.2f} "
                      f"win_rate={r['win_rate']*100:.0f}% n={r['trade_count']} | "
                      f"S/R: total_pnl={'N/A' if sr_pnl is None else f'${sr_pnl:.2f}'} "
                      f"win_rate={'N/A' if sr_wr is None else f'{sr_wr*100:.0f}%'}")
                session.add(SlTpBacktestCandidate(
                    symbol=symbol, rank=i, sl_k=r["sl_k"], tp_k=r["tp_k"], total_pnl=r["total_pnl"],
                    win_rate=r["win_rate"], trade_count=r["trade_count"],
                    sr_total_pnl=sr_pnl, sr_win_rate=sr_wr,
                ))
        session.commit()
    finally:
        session.close()

    # Zavisla na candidates z vyssie (SlTpBacktestCandidate) - musi bezat AZ
    # PO ich commit-e, preto volana odtial namiesto samostatneho scheduler
    # jobu (ktory by mohol bezat v inom poradi).
    try:
        compute_sr_calibration()
    except Exception as e:
        print(f"[sl_grid_backtest] compute_sr_calibration zlyhalo (neblokujuce): {e}")


def compute_sr_calibration() -> None:
    """Vstupny bod scheduleru (main.py, denne) - pre KAZDY ticker (aj
    neaktivny, rovnaky vzor ako sl_calibration.py) a KAZDY z jeho VLASTNYCH
    (uz per-ticker) TOP 5 kandidatov prepocita DNESNU S/R-prichytenu SL%/TP%
    (na zaklade vlastnej cenovej historie tickera) a zapise do SrCalibration -
    feeduje druhu tabulku v per-ticker "Kalibracia SL/TP" tabe.

    Smer (long/short) sa este nevie (toto je len navrh pre buduci obchod) -
    pouziva sa symetricka konvencia: SL strana = support POD live cenou, TP
    strana = resistance NAD live cenou (rovnaky vysledny % by risk_manager
    aplikoval spravne pre kazdy smer, kedze RiskOverride uklada len
    percenta, nie smerovo-viazane ceny)."""
    print(f"\n=== [sl_grid_backtest] compute_sr_calibration {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        session.query(SrCalibration).delete()
        for asset in assets.ALL_ASSETS:
            name = asset["name"]
            symbol = asset["strike_symbol"]

            candidates = (
                session.query(SlTpBacktestCandidate)
                .filter(SlTpBacktestCandidate.symbol == symbol)
                .order_by(SlTpBacktestCandidate.rank)
                .all()
            )
            if not candidates:
                continue

            try:
                df = market_data.get_price_history(asset, session)
            except Exception as e:
                print(f"[sl_grid_backtest] [{name}] get_price_history zlyhalo: {e}")
                continue
            if df.empty or len(df) < 30:
                print(f"[sl_grid_backtest] [{name}] nedost historickych dat, preskakujem.")
                continue

            df = df.copy()
            df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
            if pd.isna(df["atr14"].iloc[-1]):
                continue
            live_price = float(df["close"].iloc[-1])
            atr_pct = float(df["atr14"].iloc[-1]) / live_price * 100
            if atr_pct <= 0:
                continue

            swing_highs, swing_lows = _find_swings(df["high"].tolist(), df["low"].tolist())

            for c in candidates:
                sl_ref_dist = live_price * (c.sl_k * atr_pct / 100)
                tp_ref_dist = live_price * (c.tp_k * atr_pct / 100)
                if sl_ref_dist <= 0:
                    continue
                sl_ref = live_price - sl_ref_dist   # support strana (pod live cenou)
                tp_ref = live_price + tp_ref_dist   # resistance strana (nad live cenou)

                sl_level = _nearest_level(swing_lows, sl_ref)
                tp_level = _nearest_level(swing_highs, tp_ref)
                actual_sl, actual_tp = _apply_sr_buffer(
                    sl_level, tp_level, sl_ref, tp_ref, live_price, is_long=True,
                )
                sl_pct = abs(live_price - actual_sl) / live_price * 100
                tp_pct = abs(actual_tp - live_price) / live_price * 100
                if sl_pct <= 0:
                    continue

                session.add(SrCalibration(
                    symbol=symbol, rank=c.rank, sl_pct=sl_pct, tp_pct=tp_pct,
                ))
            print(f"[sl_grid_backtest] [{name}] S/R kalibracia zapisana pre {len(candidates)} kandidatov "
                  f"(ATR%={atr_pct:.4f}, live_price={live_price}).")
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    compute_leaderboard()  # vola aj compute_sr_calibration() interne, viz vyssie
