"""ATR-zalozena SL/TP kalibracia (2026-08-19) - systematicky nahradza rucne
odhadnute {TICKER}_SL_PCT defaulty (viz config.py, vacsina "NIE empiricky
backtestovane") a starú client-side "35% z 24h range" kalibraciu v
nas100-monitor-web (Kalibracia SL/TP tab, teraz nahradena touto).

Metodika (per ticker, nezavisle):
1. OHLC historia z market_data.get_price_history() - rovnaky zdroj (vlastne
   price_bars, fallback yfinance/CoinGecko) a rovnake ~30d okno, aky Claude
   realne vidi v TA snapshote - kalibracia tak odraza presne tie iste data.
2. Hodinovy ATR14 v kazdom bode historie (rovnaky vzorec ako
   market_data.compute_indicators, konzistentnost s TA, ktore Claude dostava).
3. Sweep kandidatov k (_K_CANDIDATES): pre kazde k odsimuluje na kazdom
   historickom bode SL=k*ATR%, TP=SL*ratio (ratio = AKTUALNE efektivny
   tp_pct/sl_pct tickera - viz risk_overrides.get_effective_sl_tp, cita sa
   priamo z premennej, nie hardcoded 1.5) a pozrie, ci nasledujucich
   _LOOKAHEAD_BARS hodin cena zasiahne najprv SL alebo TP (LONG aj SHORT
   simulacia z kazdeho bodu, aby sa trendovy drift priblizne vyrusil).
4. Vyberie k s najlepsou historickou expektanciou (v R jednotkach:
   tp_hit_rate*ratio - sl_hit_rate), pri viacerych k v ramci 5% od maxima
   najmensie (tesnejsi SL = kapitalovo efektivnejsie).
5. suggested_sl_pct = najnovsi ATR% * vybrane k. suggested_tp_pct =
   suggested_sl_pct * ratio.
6. Zapise db.AtrCalibration riadok (append-only historia, VSETKY assety aj
   neaktivne - rovnaky vzor ako price_poller zbiera pre vsetky).

NIC sa tu NIKDY automaticky nemeni v db.RiskOverride/config.py - toto je LEN
navrh. Pouzivatel si v nas100-monitor-web (Kalibracia SL/TP tab) pozrie
najnovsi riadok pre dany symbol a rucne (tlacidlom "Nastavit ako default")
sa rozhodne, ci ho chce aplikovat - viz nas100-monitor-web
api/apply-calibration.js + db.RiskOverride docstring."""
from datetime import datetime, timezone

import pandas_ta as ta

import assets
import discord_client
import market_data
import risk_overrides
from db import AtrCalibration, RiskOverride, RiskOverrideHistory, get_session

_K_CANDIDATES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]

# 2026-08-31 (UNITREE #140/#145 incident) - "najnovsi ATR%" (posledny riadok
# df, jeden bodovy odpocet) sa ukazal krehky: UNITREE malo v ramci tej istej
# 9-dnovej historie ATR% kolisajuce od ~2.3% (den po IPO) po ~0.14-0.34%
# (kratke pokojne okna) - trafit vypocet presne do jedneho z tychto tichych
# okien dalo absurdne tesny navrh (0.209% SL), ktory potom (bez tejto opravy)
# islo priamo do RiskOverride cez auto-apply. Median za posledne
# _ATR_ROBUST_WINDOW_BARS hodin namiesto jedneho bodu vyhladi tento sum,
# rovnaky princip ako uz pouzity pri manualnych ATR kalibraciach tento tyzden
# (72h okno pre UNITREE/CRCL namiesto poslednej sviecky).
_ATR_ROBUST_WINDOW_BARS = 48

# Absolutna bezpecnostna podlaha na navrhovane SL% - NEZAVISLA od
# risk_manager.SAFETY_FLOOR_MULTIPLE (ten je len NASOBOK uz existujuceho
# cieloveho sl_pct, takze ak by bol SAMOTNY cielovy sl_pct/override chybny -
# presne to sa stalo pri UNITREE cez auto-apply - floor v risk_manager
# nepomoze). 0.3% je zamerne pod najtesnejsim aktualnym legitimnym cielom v
# portfoliu (NAS100 0.4%), takze ziadny sucasny dobre kalibrovany ticker sa
# tymto neorezeva - je to cisto poistka proti degenerovanemu/sumovemu
# vstupu, nie navrh vhodnej sirky pre konkretny ticker.
_MIN_SUGGESTED_SL_PCT = 0.3

# 2026-08-20, na ziadost pouzivatela (odcestovany, bez pocitaca/CLI pristupu
# pocas cesty) - MINIMAX/UNITREE su JEDINE tickery bez akehokolvek externeho
# fallback zdroja (yfinance/CoinGecko, viz assets.py) - market_data.
# get_price_history() pre ne vrati PRAZDNY DataFrame, kym nemaju aspon
# market_data.MIN_OWN_BARS (210) vlastnych price_bars, cim padne aj tento
# vypocet ("ziadne OHLC data, preskakujem" nizsie). Preto je pre TIETO DVA
# tickery "dost dat na ATR" a "dost dat na plnu produkcnu TA" TEN ISTY
# okamih (na rozdiel od tickerov s fallbackom, kde by ATR mohol byt hotovy
# skor). Ked sa to stane prvykrat (source=="own_bars"), automaticky
# aplikujeme navrhnute SL/TP ako RiskOverride (rovnaky mechanizmus ako rucne
# "Nastavit ako default") a posleme Discord notifikaciu - pouzivatel tak z
# telefonu vie, ze stavi ENABLE_{TICKER}=true zvazit, SL/TP uz nie je slepy
# odhad. Ziadne Claude/LLM volanie, cista uz aj tak vypocitana aritmetika.
# Guard proti opakovanemu re-aplikovaniu kazdy den: preskoci, ak uz existuje
# RiskOverride so source=AUTO_APPLY_SOURCE pre dany symbol.
#
# 2026-08-31 (UNITREE #140/#145 incident): tento mechanizmus 30.8. tichy
# PREPISAL predtym manualne (a robustne, z 72h ATR priemeru) skalibrovany
# UNITREE override (2.0%/3.0%) hodnotou 0.209%/0.3135% - vypocitanou z
# JEDNEHO bodoveho ATR odcitania (0.139%) presne v momente, ked ticker prvy
# raz prekrocil MIN_OWN_BARS prah. Rovnaky (menej extremny) incident sa stal
# aj pri MINIMAX 25.8. (3.2%/4.8% manualne -> 1.6641%/2.4961% auto). V oboch
# pripadoch UZ MAME manualnu, robustnu kalibraciu - "prvy raz dost dat" ucel
# tohto mechanizmu (viz komentar vyssie) je pre tieto dva tickery uz splneny,
# takze ich vynechavame, aby sa incident nemohol zopakovat (aj po oprave
# _compute_for_asset nizsie na robustnejsi ATR vypocet - dve nezavisle
# poistky su lepsie nez jedna). Zaroven boli existujuce riadky v risk_overrides
# rucne opravene spat na 2.0%/3.0% (UNITREE) a 3.2%/4.8% (MINIMAX) so
# source="manual_atr_recalibration" - BEZ tohto vynechania by ich guard
# vyssie (kontroluje presne AUTO_APPLY_SOURCE) povazoval za "este neaplikovane"
# a pri najblizsom dennom behu by ich znova prepisal.
AUTO_APPLY_SYMBOLS: set[str] = set()
AUTO_APPLY_SOURCE = "auto_atr_recalibration"

# ~1 den - priblizne zodpoveda typickej dobe drzania pred POSITION_MAX_HOURS
# force-close, teda relevantne okno na to, ci by SL/TP realne stihli rozhodnut.
_LOOKAHEAD_BARS = 24

# 14 (ATR warm-up) + _LOOKAHEAD_BARS (posledny pouzitelny bod potrebuje cely
# lookahead pred sebou) + 30 (aspon 30 nezavislych vstupnych bodov, aby sweep
# nebol zalozeny na hrsti nahodnych sviecok).
_MIN_BARS_REQUIRED = 14 + _LOOKAHEAD_BARS + 30


def _sweep_k(df, ratio: float) -> tuple[float, dict]:
    """Pre kazdeho kandidata k odsimuluje SL=k*ATR%/TP=SL*ratio na kazdom
    pouzitelnom historickom bode (LONG aj SHORT) a vyberie k s najlepsou
    historickou expektanciou. Ak sa v ramci jednej sviecky dotknu OBE urovne
    (z OHLC sa neda urcit poradie v ramci hodiny), konzervativne pocitame SL
    ako prve (rovnaky "predpokladaj horsi pripad" princip ako inde v kode)."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    atr_pcts = df["atr_pct"].values
    n = len(df)

    results = []
    for k in _K_CANDIDATES:
        tp_hits = sl_hits = no_touch = 0
        for i in range(n - _LOOKAHEAD_BARS):
            entry = closes[i]
            atr_pct_i = atr_pcts[i]
            if not atr_pct_i or atr_pct_i <= 0:
                continue
            sl_dist = entry * (k * atr_pct_i / 100)
            tp_dist = sl_dist * ratio
            w_high = highs[i + 1:i + 1 + _LOOKAHEAD_BARS]
            w_low = lows[i + 1:i + 1 + _LOOKAHEAD_BARS]

            for is_long in (True, False):
                sl_level = entry - sl_dist if is_long else entry + sl_dist
                tp_level = entry + tp_dist if is_long else entry - tp_dist
                outcome = "none"
                for h, l in zip(w_high, w_low):
                    hit_sl = (l <= sl_level) if is_long else (h >= sl_level)
                    hit_tp = (h >= tp_level) if is_long else (l <= tp_level)
                    if hit_sl:
                        outcome = "sl"
                        break
                    if hit_tp:
                        outcome = "tp"
                        break
                if outcome == "sl":
                    sl_hits += 1
                elif outcome == "tp":
                    tp_hits += 1
                else:
                    no_touch += 1

        total = tp_hits + sl_hits + no_touch
        if total == 0:
            continue
        expectancy = (tp_hits * ratio - sl_hits) / total
        results.append({"k": k, "expectancy": expectancy, "tp_rate": tp_hits / total,
                         "sl_rate": sl_hits / total, "no_touch_rate": no_touch / total})

    if not results:
        return _K_CANDIDATES[len(_K_CANDIDATES) // 2], {}

    best_expectancy = max(r["expectancy"] for r in results)
    tolerance = abs(best_expectancy) * 0.05 + 1e-9
    close_enough = [r for r in results if r["expectancy"] >= best_expectancy - tolerance]
    chosen = min(close_enough, key=lambda r: r["k"])
    return chosen["k"], chosen


def _maybe_auto_apply(asset: dict, symbol: str, suggested_sl: float, suggested_tp: float,
                       atr_pct: float, bars_used: int, source: str, session) -> None:
    """Viz AUTO_APPLY_SYMBOLS docstring vyssie - LEN pre symboly v tomto
    zozname, LEN raz (guard cez existujuci RiskOverride.source), LEN ked je
    zdroj dat 'own_bars' (nie fallback - pri fallbacku by "MIN_OWN_BARS"
    podmienka ani nebola splnena, viz volajuci, ale explicitna kontrola
    nezaskodi ak sa niekedy zmeni get_price_history logika)."""
    if symbol not in AUTO_APPLY_SYMBOLS or source != "own_bars":
        return
    already_applied = (
        session.query(RiskOverride.symbol)
        .filter(RiskOverride.symbol == symbol, RiskOverride.source == AUTO_APPLY_SOURCE)
        .first()
    )
    if already_applied:
        return

    override = session.query(RiskOverride).filter(RiskOverride.symbol == symbol).first()
    if override is None:
        override = RiskOverride(symbol=symbol)
        session.add(override)
    override.sl_pct = suggested_sl
    override.tp_pct = suggested_tp
    override.source = AUTO_APPLY_SOURCE
    override.updated_at = datetime.now(timezone.utc)

    note = (
        f"Automaticky aplikovane (2026-08-20 mechanizmus, na ziadost pouzivatela) - "
        f"prvy raz, ked {asset['name']} dosiahol dost vlastnych barov ({bars_used}) na "
        f"spolahlivu ATR-based kalibraciu (ATR14={atr_pct:.3f}%). Ziadne "
        f"Claude/LLM volanie, cista deterministicka aritmetika z sl_calibration.py "
        f"grid-search sweepu."
    )
    session.add(RiskOverrideHistory(
        symbol=symbol, sl_pct=suggested_sl, tp_pct=suggested_tp,
        source=AUTO_APPLY_SOURCE, note=note, applied_at=datetime.now(timezone.utc),
    ))
    print(f"[sl_calibration] [{asset['name']}] AUTO-APLIKOVANE SL={suggested_sl:.3f}%/"
          f"TP={suggested_tp:.3f}% (prve dost dat, {bars_used} barov) + Discord notifikacia.")
    try:
        discord_client.notify_ready_for_production(asset["name"], suggested_sl, suggested_tp,
                                                     atr_pct, bars_used)
    except Exception as e:
        print(f"[sl_calibration] [{asset['name']}] Discord notifikacia zlyhala (neblokujuce): {e}")


def _compute_for_asset(asset: dict, session) -> None:
    name = asset["name"]
    symbol = asset["strike_symbol"]

    df = market_data.get_price_history(asset, session)
    if df.empty:
        print(f"[sl_calibration] [{name}] ziadne OHLC data, preskakujem.")
        return

    df = df.copy()
    df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["atr_pct"] = df["atr14"] / df["close"] * 100
    df = df.dropna(subset=["atr_pct"])
    if len(df) < _MIN_BARS_REQUIRED:
        print(f"[sl_calibration] [{name}] len {len(df)} pouzitelnych barov "
              f"(potrebných aspoň {_MIN_BARS_REQUIRED}), preskakujem.")
        return

    sl_pct, tp_pct = risk_overrides.get_effective_sl_tp(session, asset)
    ratio = (tp_pct / sl_pct) if sl_pct else 1.5

    best_k, stats = _sweep_k(df, ratio)
    # 2026-08-31 - median za posledne _ATR_ROBUST_WINDOW_BARS namiesto
    # jedneho posledneho riadku (viz konstanta vyssie pre plne zdovodnenie -
    # UNITREE #140/#145 incident). latest_atr_pct (jednobodova hodnota) sa
    # stale zapisuje do AtrCalibration.atr_pct nizsie len INFORMATIVNE (aby
    # dashboard vedel zobrazit "aktualny" odpocet), ale VYPOCET navrhu z nej
    # uz nevychadza.
    latest_atr_pct = float(df["atr_pct"].iloc[-1])
    robust_atr_pct = float(df["atr_pct"].tail(_ATR_ROBUST_WINDOW_BARS).median())
    suggested_sl = max(round(robust_atr_pct * best_k, 4), _MIN_SUGGESTED_SL_PCT)
    suggested_tp = round(suggested_sl * ratio, 4)

    # Priblizny odhad zdroja (get_price_history() sama o sebe zdroj nevracia) -
    # cisto informativne pole, na skutocnu logiku sweepu nema vplyv.
    source = "own_bars" if len(df) >= market_data.MIN_OWN_BARS else "fallback"

    print(f"[sl_calibration] [{name}] ATR%={latest_atr_pct:.4f} (posledny bod), "
          f"median{_ATR_ROBUST_WINDOW_BARS}h={robust_atr_pct:.4f} k={best_k} "
          f"(expectancy={stats.get('expectancy', 0):.3f}R, "
          f"tp_rate={stats.get('tp_rate', 0):.2f}, sl_rate={stats.get('sl_rate', 0):.2f}) "
          f"-> navrhovane SL={suggested_sl:.3f}%/TP={suggested_tp:.3f}% "
          f"(aktualne {sl_pct}%/{tp_pct}%)")

    session.add(AtrCalibration(
        symbol=symbol, lookback_days=30, bars_used=len(df), source=source,
        atr_pct=latest_atr_pct, k_multiple=best_k, ratio=ratio,
        configured_sl_pct=sl_pct, configured_tp_pct=tp_pct,
        suggested_sl_pct=suggested_sl, suggested_tp_pct=suggested_tp,
    ))

    _maybe_auto_apply(asset, symbol, suggested_sl, suggested_tp, robust_atr_pct, len(df), source, session)


def compute_all() -> None:
    """Vstupny bod scheduleru (main.py, denne) - prejde VSETKY assety (aj
    neaktivne, rovnaky vzor ako price_poller.py), pre kazdy zapise novy
    AtrCalibration riadok. Zlyhanie jedneho assetu nesmie zhodit ostatne."""
    print(f"\n=== [sl_calibration] {datetime.now(timezone.utc).isoformat()} ===")
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            try:
                _compute_for_asset(asset, session)
            except Exception as e:
                print(f"[sl_calibration] [{asset['name']}] neocakavana chyba, preskakujem: {e}")
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    compute_all()
