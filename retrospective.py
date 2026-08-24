"""
Denna sebareflexia bota - RAZ za den (pri prvom cykle po polnoci UTC pre dany
asset) sa vypocitaju STATISTIKY (cisto v kode, ZDARMA - ziadne extra Claude
volanie) za PREDCHADZAJUCI den: co bot odporucal, ako to REALNE dopadlo
(skutocne otvorene obchody), co by sa BOLO stalo pri signaloch zamietnutych
LEN kvoli confidence, a co by sa BOLO stalo, keby sa pri 'none' cykloch predsa
len otvoril hypoteticky LONG/SHORT (pomocou skutocneho neskorsieho cenoveho
vyvoja - rovnaka metodika ako manualny backtest z 2026-07-24).

Cielom je zistit, ci je confidence kalibracia primerana, prilis prisna (vela
zamietnutych/none signalov by bolo ziskovych), alebo primerane opatrna - a dat
Claude-ovi podklad na strucnu sebareflexiu (viz claude_analyst.py), ktora sa
potom prenasa do vsetkych dalsich cyklov toho dna.
"""
from datetime import date, datetime, timedelta, timezone

import yfinance as yf

import risk_manager
from db import CycleLog, Trade


def _fetch_price_history(yf_symbol: str, fallback: str | None, start: datetime, end: datetime):
    df = yf.download(yf_symbol, start=start, end=end, interval="5m",
                      progress=False, auto_adjust=True, prepost=True)
    if df.empty and fallback:
        df = yf.download(fallback, start=start, end=end, interval="5m",
                          progress=False, auto_adjust=True, prepost=True)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    if not df.empty and df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df.dropna()


def _hypothetical_sl_tp(live_price: float, direction: str, decision_sl: float,
                         sl_pct: float, tp_pct: float) -> tuple[float, float]:
    """Zrkadli AKTUALNU risk_manager logiku (SL od Claude orezany 0.1x-5x, TP
    dopocitany z pomeru) - viz risk_manager.validate_and_size. Bez tick-
    zaokruhlenia (nepotrebne presne pre retrospektivny odhad)."""
    sl_distance = abs(live_price - decision_sl)
    default_sl_distance = live_price * (sl_pct / 100)
    sl_distance = min(
        max(sl_distance, risk_manager.SAFETY_FLOOR_MULTIPLE * default_sl_distance),
        risk_manager.SAFETY_CAP_MULTIPLE * default_sl_distance,
    )
    tp_distance = sl_distance * (tp_pct / sl_pct)
    if direction == "long":
        return live_price - sl_distance, live_price + tp_distance
    return live_price + sl_distance, live_price - tp_distance


def _hypothetical_sl_tp_default(live_price: float, direction: str,
                                 sl_pct: float, tp_pct: float) -> tuple[float, float]:
    """Ako _hypothetical_sl_tp, ale bez Claude-navrhnutej SL ceny - pouziva sa
    pre 'none' cykly (Claude ziadnu cenu nezadal, lebo direction=none), takze
    tu proste symetricky default_sl_pct/default_tp_pct od live ceny."""
    sl_distance = live_price * (sl_pct / 100)
    tp_distance = live_price * (tp_pct / 100)
    if direction == "long":
        return live_price - sl_distance, live_price + tp_distance
    return live_price + sl_distance, live_price - tp_distance


def _simulate_outcome(price_df, entry_time: datetime, direction: str,
                       sl: float, tp: float, deadline: datetime):
    """Vrati (exit_price, reason) - 'tp'|'sl'|'timeout'|'unresolved'|'no_data'.
    Rovnaka pesimisticka logika (SL prvy ak su oba v tom istom bare) ako
    manualny backtest skript z 2026-07-24."""
    if price_df is None or price_df.empty:
        return None, "no_data"
    window = price_df[(price_df.index > entry_time) & (price_df.index <= deadline)]
    if window.empty:
        return None, "no_data"

    for ts, row in window.iterrows():
        hi, lo = float(row["high"]), float(row["low"])
        if direction == "short":
            sl_hit, tp_hit = hi >= sl, lo <= tp
        else:
            sl_hit, tp_hit = lo <= sl, hi >= tp
        if sl_hit:
            return sl, "sl"
        if tp_hit:
            return tp, "tp"

    last_ts = window.index[-1]
    if (deadline - last_ts).total_seconds() > 600:
        return float(window.iloc[-1]["close"]), "unresolved"
    return float(window.iloc[-1]["close"]), "timeout"


def compute_daily_stats(asset: dict, for_date: date, session) -> dict:
    """Vypocita statistiky za `for_date` (UTC kalendarny den) pre dany asset -
    co sa REALNE otvorilo (a ako to dopadlo), co by sa BOLO stalo pri
    signaloch zamietnutych len kvoli confidence, a co by sa BOLO stalo pri
    'none' cykloch (hypoteticky LONG aj SHORT s default SL/TP % - Claude tam
    ziadnu cenu nenavrhol)."""
    symbol = asset["strike_symbol"]
    day_start = datetime.combine(for_date, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    logs = (
        session.query(CycleLog)
        .filter(
            CycleLog.symbol == symbol,
            CycleLog.direction.in_(["long", "short", "none"]),
            CycleLog.created_at >= day_start,
            CycleLog.created_at < day_end,
        )
        .order_by(CycleLog.created_at)
        .all()
    )

    stats = {
        "for_date": for_date.isoformat(),
        "symbol": symbol,
        "total_signals": sum(1 for log in logs if log.direction in ("long", "short")),
        "opened": [],
        "rejected_confidence": [],
        "rejected_other_count": 0,
        "none_count": sum(1 for log in logs if log.direction == "none"),
        "none_missed": [],
        "none_ambiguous_count": 0,
        "none_correctly_avoided_count": 0,
        # 2026-08-24 (na ziadost pouzivatela, po NIGHT "opakovany vzor rovnakej
        # chyby" postrehu) - closed_trade_reflection/sl_tp_calibration_verdict
        # (viz claude_analyst.py DECISION_TOOL schema) sa doteraz zapisali LEN
        # do CycleLog riadku dovodneho review cyklu a uz sa NIKDY necitali
        # spat - kvalitativne poucenie z konkretneho zatvoreneho obchodu tak
        # bolo stratene, kym ho Claude nahodou znova nezachytil z holych cisel
        # nizsie. Teraz sa zbieraju tu, aby mal Claude sancu ich REALNE
        # zapracovat do summary_reflection (viz format_stats_for_prompt).
        "trade_reflections": [],
    }
    if not logs:
        return stats

    price_df = None
    yf_symbol, yf_fallback = asset["yf_symbol"], asset.get("yf_fallback")

    def ensure_price_df():
        nonlocal price_df
        if price_df is None:
            fetch_start = day_start - timedelta(hours=1)
            fetch_end = min(day_end + timedelta(hours=25), datetime.now(timezone.utc))
            price_df = _fetch_price_history(yf_symbol, yf_fallback, fetch_start, fetch_end)
        return price_df

    for log in logs:
        if log.reviewed_trade_id and (log.closed_trade_reflection or log.sl_tp_calibration_verdict):
            stats["trade_reflections"].append({
                "trade_id": log.reviewed_trade_id,
                "closed_trade_reflection": log.closed_trade_reflection,
                "sl_tp_calibration_verdict": log.sl_tp_calibration_verdict,
            })

        if log.direction == "none":
            if not log.live_price:
                continue
            snap = log.config_snapshot or {}
            sl_pct, tp_pct = snap.get("default_sl_pct"), snap.get("default_tp_pct")
            position_max_hours = snap.get("position_max_hours", 24)
            if sl_pct is None or tp_pct is None:
                continue

            df = ensure_price_df()
            # log.created_at je tz-naive (Postgres TIMESTAMP WITHOUT TIME ZONE cez
            # SQLAlchemy) rovnako ako price_df.index (po tz_localize(None) v
            # _fetch_price_history) - obe musia ostat naive, inak pandas porovnanie zlyha.
            entry_time = log.created_at
            deadline = entry_time + timedelta(hours=position_max_hours)

            long_sl, long_tp = _hypothetical_sl_tp_default(log.live_price, "long", sl_pct, tp_pct)
            short_sl, short_tp = _hypothetical_sl_tp_default(log.live_price, "short", sl_pct, tp_pct)
            _, long_reason = _simulate_outcome(df, entry_time, "long", long_sl, long_tp, deadline)
            _, short_reason = _simulate_outcome(df, entry_time, "short", short_sl, short_tp, deadline)
            if long_reason in (None, "no_data", "unresolved") or short_reason in (None, "no_data", "unresolved"):
                continue

            long_win, short_win = long_reason == "tp", short_reason == "tp"
            if long_win and short_win:
                stats["none_ambiguous_count"] += 1
            elif long_win or short_win:
                leverage = snap.get("leverage", asset["leverage"])
                margin_usd = snap.get("margin_usd", asset["margin_usd"])
                hypothetical_pnl = margin_usd * leverage * (tp_pct / 100)
                stats["none_missed"].append({
                    "would_have_direction": "long" if long_win else "short",
                    "hypothetical_pnl_usd": round(hypothetical_pnl, 2),
                })
            else:
                stats["none_correctly_avoided_count"] += 1
            continue

        if log.outcome == "opened" and log.trade_id:
            trade = session.query(Trade).filter(Trade.id == log.trade_id).first()
            if trade:
                stats["opened"].append({
                    "confidence": log.confidence,
                    "direction": trade.direction,
                    "status": trade.status,
                    "pnl_usd": trade.pnl_usd,
                    # 2026-08-18 - viz format_stats_for_prompt nizsie: bez tychto
                    # dvoch polí Claude pri hodnoteni PnL nemal ziadny odkaz na
                    # KOLKO rizika bolo v hre ani na to, ci islo o SL/TP (teda
                    # presne definovanu max. stratu/zisk, nie nahodny bod).
                    "margin_usd": trade.margin_usd,
                    "close_reason": trade.close_reason,
                })
            continue

        if log.outcome != "rejected" or not log.reject_reason:
            continue
        if "confidence" not in log.reject_reason.lower():
            stats["rejected_other_count"] += 1
            continue

        if not log.live_price or not log.stop_loss_price:
            continue

        snap = log.config_snapshot or {}
        sl_pct, tp_pct = snap.get("default_sl_pct"), snap.get("default_tp_pct")
        position_max_hours = snap.get("position_max_hours", 24)
        if sl_pct is None or tp_pct is None:
            continue

        df = ensure_price_df()
        sl, tp = _hypothetical_sl_tp(log.live_price, log.direction, log.stop_loss_price, sl_pct, tp_pct)
        entry_time = log.created_at
        deadline = entry_time + timedelta(hours=position_max_hours)

        exit_price, reason = _simulate_outcome(df, entry_time, log.direction, sl, tp, deadline)
        if reason in (None, "no_data", "unresolved"):
            continue

        pnl_pct = ((log.live_price - exit_price) / log.live_price if log.direction == "short"
                   else (exit_price - log.live_price) / log.live_price)
        leverage = snap.get("leverage", asset["leverage"])
        margin_usd = snap.get("margin_usd", asset["margin_usd"])
        hypothetical_pnl = margin_usd * leverage * pnl_pct

        stats["rejected_confidence"].append({
            "confidence": log.confidence,
            "direction": log.direction,
            "would_have": reason,
            "hypothetical_pnl_usd": round(hypothetical_pnl, 2),
        })

    return stats


def format_stats_for_prompt(stats: dict) -> str:
    """Kompaktny textovy suhrn stats pre user prompt - Claude z neho ma
    vyvodit strucne poucenie (daily_reflection), NIE prepocitavat cisla sam."""
    none_count = stats.get("none_count", 0)
    total_cycles = stats["total_signals"] + none_count
    if total_cycles == 0:
        return f"Za {stats['for_date']} nebehol ziadny cyklus (asset bol mimo intervalu cely den)."

    lines = [
        f"Za {stats['for_date']}: {total_cycles} cyklov celkovo "
        f"({stats['total_signals']} long/short, {none_count} none)."
    ]

    opened = stats["opened"]
    if opened:
        closed = [o for o in opened if o.get("pnl_usd") is not None]
        wins = sum(1 for o in closed if o["pnl_usd"] > 0)
        total_pnl = sum(o["pnl_usd"] for o in closed)
        pending = len(opened) - len(closed)
        lines.append(
            f"- Realne otvorene: {len(opened)}"
            + (f", z toho {pending} este nie su uzavrete" if pending else "")
            + (f" ({wins}/{len(closed)} vyhier z uzavretych, celkove PNL ${total_pnl:.2f})" if closed else "")
            + "."
        )
        # 2026-08-18 (spatna vazba pouzivatela - SKHYNIX obchod -$35.75 oznaceny
        # v daily_reflection ako "mierna strata", pricom to bolo ~36% risknutej
        # marze PRESNE na SL urovni): bez odkazu na marzu/dovod zatvorenia nema
        # Claude ziadnu kotvu na to, co je "mierne" - holy dolarovy udaj
        # posudzoval len relativne k celkovemu portfoliu, nie k tomu, KOLKO
        # rizika bolo na TOMTO obchode v hre. SL/TP je z definicie presne
        # najhorsi/najlepsi mozny vysledok daneho obchodu, nie nahodny bod.
        for o in closed:
            if not o.get("margin_usd"):
                continue
            pct = o["pnl_usd"] / o["margin_usd"] * 100
            reason_note = ""
            if o.get("close_reason") == "stop_loss":
                reason_note = " - SL, teda MAXIMALNA definovana strata na tomto obchode, nie nahodny bod"
            elif o.get("close_reason") == "take_profit":
                reason_note = " - TP, teda maximalny definovany zisk na tomto obchode"
            lines.append(
                f"  ({o['direction']}, conf {o['confidence']}: {pct:+.0f}% z risknutej marze{reason_note})"
            )
    else:
        lines.append("- Realne otvorene: 0.")

    rc = stats["rejected_confidence"]
    if rc:
        would_win = sum(1 for r in rc if r["hypothetical_pnl_usd"] > 0)
        total_hyp = sum(r["hypothetical_pnl_usd"] for r in rc)
        avg_conf = sum(r["confidence"] for r in rc) / len(rc)
        lines.append(
            f"- Zamietnute LEN kvoli confidence: {len(rc)} (priemerna confidence {avg_conf:.0f}) - "
            f"keby sa vsetky otvorili, {would_win}/{len(rc)} by bolo ziskovych, hypoteticke "
            f"celkove PNL ${total_hyp:.2f}."
        )
        if len(rc) < 10:
            lines.append(
                f"  (POZOR: n={len(rc)} je mala vzorka za JEDEN den - nestavaj na tom trvaly "
                f"zaver o kalibracii prahu v summary_reflection, ani ak vyjde 100% hit rate. "
                f"Over si to voci viacdnovemu vzoru (co uz mas v priebeznom zhrnuti), nie voci "
                f"tomuto jednemu dnu."
            )
    else:
        lines.append("- Zamietnute kvoli confidence: 0.")

    if stats["rejected_other_count"]:
        lines.append(f"- Zamietnute z inych dovodov (nie confidence): {stats['rejected_other_count']}.")

    if none_count:
        nm = stats.get("none_missed", [])
        avoided = stats.get("none_correctly_avoided_count", 0)
        ambiguous = stats.get("none_ambiguous_count", 0)
        if nm:
            missed_pnl = sum(n["hypothetical_pnl_usd"] for n in nm)
            lines.append(
                f"- 'None' (ziadny signal): {none_count}, z toho {len(nm)} malo v spatnom pohlade byt "
                f"LONG/SHORT (uslo hypoteticke PNL spolu ${missed_pnl:.2f}), {avoided} bolo spravne "
                f"vyhnutych strate"
                + (f", {ambiguous} nejednoznacnych (oba smery by vyhrali)." if ambiguous else ".")
            )
            lines.append(
                "  (POZOR pri interpretacii tohto cisla: toto NIE JE realny obchodovany signal - "
                "simuluje sa LONG aj SHORT naraz so symetrickym default SL/TP a 'uslo' sa pripise "
                "kedykolvek ASPON JEDEN z oboch smerov nahodou trafil TP skor nez SL. Pri 24h okne "
                "je bezne, ze niektory z dvoch symetrickych smerov TP trafi cistou nahodou, takze "
                "toto cislo je systematicky optimistickejsie nez realita. Riad sa pri uvahach o "
                "kalibracii prahu PRIMARNE riadkom 'Zamietnute LEN kvoli confidence' vyssie (tam "
                "ide o TVOJ skutocny navrhnuty smer aj SL, teda realny test tvojej zrucnosti, nie "
                "hypoteticky coin-flip)."
            )
        else:
            lines.append(
                f"- 'None' (ziadny signal): {none_count}, ziadne z nich v spatnom pohlade nemalo byt "
                f"long/short."
            )

    reflections = stats.get("trade_reflections", [])
    if reflections:
        lines.append(
            f"- Kvalitativne hodnotenia zatvorenych obchodov za tento den ({len(reflections)}) - "
            "TOTO JE JEDINY MOMENT, kedy tieto konkretne postrehy este uvidis: uz sa nikdy znova "
            "nezobrazia. Ak niektory obsahuje TRVALO uzitocne poucenie (nie jednorazovu nahodnu "
            "okolnost), aktivne ho zapracuj do summary_reflection nizsie - inak sa strati navzdy:"
        )
        for r in reflections:
            parts = []
            if r.get("closed_trade_reflection"):
                parts.append(f"hodnotenie: {r['closed_trade_reflection']}")
            if r.get("sl_tp_calibration_verdict"):
                parts.append(f"SL/TP verdikt: {r['sl_tp_calibration_verdict']}")
            lines.append(f"  (trade #{r['trade_id']}) " + " | ".join(parts))

    return "\n".join(lines)
