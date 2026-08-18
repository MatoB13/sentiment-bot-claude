""""Zivy" TOP-5 rebricek SL/TP kandidatov (2026-08-19) - naprieč VSETKYMI
uzavretymi obchodmi vsetkych tickerov (POOLED, ATR-normalizovane jednotky),
nie per-ticker ako sl_calibration.py.

Dovod POOLED pristupu: per-ticker vzorka je prilis mala (36 obchodov na 13
tickerov) na nezavisly fit - v ATR-normalizovanych jednotkach (SL_k = SL_pct/
ATR_pct v case vstupu) vsak vsetky tickery prispievaju do JEDNEJ zdielanej
vzorky, kedze rovnaky SL_k = rovnaka relativna "sirka" bez ohladu na
absolutnu volatilitu konkretneho tickera.

Metodika (na rozdiel od povodneho, chybneho sl_calibration._sweep_k, ktory
testoval NAHODNE body v historii - viz jeho docstring/komentare a
memory poznamka pending_sl_calibration_methodology_fix): tento grid search
pouziva VYHRADNE SKUTOCNE vstupy bota (entry price/smer/cas z realnych
Trade zaznamov), kedze tie nesu genuinne smerove presvedcenie Claude-a,
ktore nahodny bod nema.

Pre kazdu kombinaciu (SL_k, TP_k) z mriezky sa simuluje first-touch (SL vs
TP, ktory sa dotkne skor) na REALNEJ nasledujucej cenovej ceste (hodinove
PriceBar, okno POSITION_MAX_HOURS - rovnaka politika ako zivy bot), s
leverage prepocitanou cez rovnaky vzorec ako risk_manager (_leverage_from_cushion),
aby $ PnL bolo realisticke."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pandas_ta as ta

import config
import risk_manager
import strike_client
from db import CycleLog, PriceBar, SlTpBacktestCandidate, Trade, get_session

# Rovnaka mriezka, aka sa pouzila na jednorazovy grid search 2026-08-19 (viz
# memory pending_sl_calibration_methodology_fix) - siroke pokrytie navyse
# overene, ze SL_k=5-10 uz vykazuje POKLES oproti SL_k=5 (nie je to len
# artefakt kraja mriezky).
_SL_K_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0]
_TP_K_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]

_CLOSED_STATUSES = ["closed_by_exchange", "closed_by_timeout", "closed_by_safety", "closed_by_user"]

_TOP_N = 5


def _prepare_trade(t: Trade, session, market_meta_cache: dict) -> dict | None:
    """Pripravi vsetko potrebne pre jeden realny obchod (entry/smer/ATR% v
    case vstupu/forward bary/leverage-vstupy) - None ak dat nie je dost."""
    cl = session.query(CycleLog).filter(CycleLog.trade_id == t.id, CycleLog.outcome == "opened").first()
    if cl is None or cl.config_snapshot is None:
        return None
    cushion_multiple = cl.config_snapshot.get("liquidation_cushion_multiple", 1.5)

    entry = t.entry_price
    is_long = (t.direction or "").lower() == "long"
    opened_at = t.opened_at
    window_end = opened_at + timedelta(hours=config.POSITION_MAX_HOURS)

    bars = (
        session.query(PriceBar)
        .filter(PriceBar.symbol == t.symbol, PriceBar.hour_start >= opened_at - timedelta(hours=20),
                PriceBar.hour_start <= window_end)
        .order_by(PriceBar.hour_start)
        .all()
    )
    entry_bars = [b for b in bars if b.hour_start <= opened_at]
    fwd_bars = [b for b in bars if b.hour_start > opened_at]
    if len(entry_bars) < 15 or not fwd_bars:
        return None

    edf = pd.DataFrame([{"high": b.high, "low": b.low, "close": b.close} for b in entry_bars])
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


def _simulate(prepared: list[dict], sl_k: float, tp_k: float) -> dict | None:
    """First-touch simulacia danej (sl_k, tp_k) kombinacie naprieč VSETKYMI
    pripravenymi obchodmi - vrati suhrnne total_pnl/win_rate/trade_count."""
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
            leverage = risk_manager._leverage_from_cushion(
                sl_dist, entry, p["trade"].margin_usd, p["market_meta"]["margin_tiers"], p["cushion_multiple"],
            )
        except Exception:
            continue

        outcome = "no_touch"
        for b in p["fwd_bars"]:
            hit_sl = (b.low <= sl_price) if p["is_long"] else (b.high >= sl_price)
            hit_tp = (b.high >= tp_price) if p["is_long"] else (b.low <= tp_price)
            if hit_sl:
                outcome = "sl"
                break
            if hit_tp:
                outcome = "tp"
                break

        sl_pct = sl_k * p["atr_pct"]
        tp_pct = tp_k * p["atr_pct"]
        if outcome == "sl":
            pnl = p["trade"].margin_usd * leverage * (-sl_pct / 100)
        elif outcome == "tp":
            pnl = p["trade"].margin_usd * leverage * (tp_pct / 100)
        else:
            last_close = p["fwd_bars"][-1].close
            pnl_pct = ((last_close - entry) / entry) if p["is_long"] else ((entry - last_close) / entry)
            pnl = p["trade"].margin_usd * leverage * pnl_pct

        total_pnl += pnl
        wins += 1 if pnl > 0 else 0
        n += 1

    if n == 0:
        return None
    return {"sl_k": sl_k, "tp_k": tp_k, "total_pnl": total_pnl, "win_rate": wins / n, "trade_count": n}


def compute_leaderboard() -> None:
    """Vstupny bod scheduleru (main.py, denne) - prepocita cely grid search
    a prepise TOP 5 v SlTpBacktestCandidate. Nic sa NIKDY automaticky
    neaplikuje do RiskOverride - cisto informativny, priebezne sa
    aktualizujuci rebricek (viz nas100-monitor-web Prehlad tab)."""
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
        prepared = []
        for t in closed:
            p = _prepare_trade(t, session, market_meta_cache)
            if p is not None:
                prepared.append(p)

        print(f"[sl_grid_backtest] Pripravenych {len(prepared)}/{len(closed)} obchodov na grid search.")
        if not prepared:
            return

        results = []
        for sl_k in _SL_K_GRID:
            for tp_k in _TP_K_GRID:
                r = _simulate(prepared, sl_k, tp_k)
                if r is not None:
                    results.append(r)

        if not results:
            print("[sl_grid_backtest] Ziadne pouzitelne vysledky, preskakujem zapis.")
            return

        results.sort(key=lambda r: -r["total_pnl"])
        top = results[:_TOP_N]

        session.query(SlTpBacktestCandidate).delete()
        for i, r in enumerate(top, start=1):
            print(f"[sl_grid_backtest] #{i}: SL_k={r['sl_k']} TP_k={r['tp_k']} "
                  f"total_pnl=${r['total_pnl']:.2f} win_rate={r['win_rate']*100:.0f}% n={r['trade_count']}")
            session.add(SlTpBacktestCandidate(
                rank=i, sl_k=r["sl_k"], tp_k=r["tp_k"], total_pnl=r["total_pnl"],
                win_rate=r["win_rate"], trade_count=r["trade_count"],
            ))
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    compute_leaderboard()
