"""
Ziskanie cenovych dat pre obchodovane assety (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/
BTC/HYPE/SKHYNIX) a vypocet TA indikatorov.

Primarny zdroj hodinovych OHLC sviecok je VLASTNY poller Strike mark_price
(price_bars tabulka - viz price_poller.py): na rozdiel od yfinance (futures/
akcia zatvorene mimo obchodnych hodin/cez vikend -> zamrznuty graf) Strike
perpy obchoduju nonstop, takze Claude vidi skutocny pohyb aj vtedy, ked su
TradFi trhy zatvorene. yfinance ostava FALLBACK - ak vlastne data chybaju
alebo su zastarale (napr. poller bol mimo prevadzky) - viz get_price_history().
Realna vstupna/vystupna cena obchodu sa vzdy berie z live ceny na Strike
(strike_client.get_markets()) nezavisle od tohto, TA slúži len ako kontext.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pandas_ta as ta
import yfinance as yf

import binance_client
import coingecko_client
from db import FundingRateBar, PriceBar

# Kolko poslednych hodinovych sviecok posielame Claude ako surovy podklad na
# posudenie strukturu (support/resistance, breakout, swing high/low) - viz
# _recent_candles(). 48 = ~2 dni, kompromis medzi uzitocnym kontextom a
# tokenmi/cenou (kazda sviecka pridava ~4 cisla do promptu).
RECENT_CANDLES_BARS = 48

# Kolko vlastnych sviecok minimalne potrebujeme, nez zacneme dovervat vlastnym
# price_bars namiesto yfinance - 210 = rezerva nad ema200 (200-periodovy EMA
# potrebuje 200 barov, inak vychadza NaN).
MIN_OWN_BARS = 210
# Ak je najnovsia vlastna sviecka starsia nez tolko hodin, poller
# pravdepodobne dlhsie nebezal (vypadok/redeploy) - radsej yfinance fallback
# nez tichy diera v najcerstvejsich datach.
OWN_DATA_STALE_HOURS = 3


def fetch_ohlcv(symbol: str = "NQ=F", fallback: str | None = "^NDX",
                 period: str = "30d", interval: str = "1h") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty and fallback:
        df = yf.download(fallback, period=period, interval=interval, progress=False, auto_adjust=True)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    return df.dropna()


def fetch_ohlcv_coingecko(coin_id: str, days: int = 30) -> pd.DataFrame:
    """Alternativny fallback/backfill zdroj namiesto fetch_ohlcv() (yfinance) -
    LEN pre assety bez yfinance/Binance pokrytia (viz coingecko_client.py a
    assets.py coingecko_id, momentalne len HYPE). Ziadny volume stlpec
    (endpoint ho neposkytuje) - volajuci si ho v tom pripade doplni ako NaN
    rovnako ako pri chybajucom yfinance/Binance zapase."""
    try:
        rows = coingecko_client.get_ohlc(coin_id, days=days)
    except Exception as e:
        print(f"[market_data] CoinGecko OHLC fetch pre {coin_id} zlyhal: {e}")
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    idx = pd.to_datetime([r["open_time"] for r in rows], unit="ms", utc=True).tz_localize(None)
    df = pd.DataFrame(
        [{"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]} for r in rows],
        index=idx,
    )
    return df.sort_index()


def compute_indicators(df: pd.DataFrame, include_volume: bool = False) -> dict:
    if df.empty:
        # Vsetky zdroje (vlastne price_bars, yfinance/CoinGecko fallback) zlyhali
        # alebo su prazdne - bez tejto kontroly by df["close"] nizsie zhodilo cyklus
        # s neprehladnym KeyError('close') namiesto jasnej pricinnej spravy (viz
        # produkcny incident 2026-08-08 - CoinGecko 429 z Railway cloud IP).
        raise ValueError("ziadne OHLC data k dispozicii (prazdny DataFrame z vsetkych zdrojov)")
    df = df.copy()
    df["rsi14"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df = df.join(macd)
    df["ema20"] = ta.ema(df["close"], length=20)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)
    bb = ta.bbands(df["close"], length=20)
    df = df.join(bb)
    df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    last = df.iloc[-1]
    prev_24h = df.iloc[-24] if len(df) > 24 else df.iloc[0]

    change_24h_pct = (last["close"] - prev_24h["close"]) / prev_24h["close"] * 100

    macd_col = [c for c in df.columns if c.startswith("MACD_")][0]
    macds_col = [c for c in df.columns if c.startswith("MACDs_")][0]
    bbl_col = [c for c in df.columns if c.startswith("BBL_")][0]
    bbu_col = [c for c in df.columns if c.startswith("BBU_")][0]

    # 6 desatinnych miest namiesto 2 - NAS100/NVDA sa 2 desatinami nepokazi, ale
    # ADA sa obchoduje pod $1 (napr. 0.4523), kde by zaokruhlenie na 2 miesta
    # znamenalo strate presnosti porovnatelnu s celou SL/TP vzdialenostou.
    summary = {
        "last_price": round(float(last["close"]), 6),
        "change_24h_pct": round(float(change_24h_pct), 2),
        "rsi14": round(float(last["rsi14"]), 1) if pd.notna(last["rsi14"]) else None,
        "macd": round(float(last[macd_col]), 6) if pd.notna(last[macd_col]) else None,
        "macd_signal": round(float(last[macds_col]), 6) if pd.notna(last[macds_col]) else None,
        "ema20": round(float(last["ema20"]), 6) if pd.notna(last["ema20"]) else None,
        "ema50": round(float(last["ema50"]), 6) if pd.notna(last["ema50"]) else None,
        "ema200": round(float(last["ema200"]), 6) if pd.notna(last["ema200"]) else None,
        "bollinger_lower": round(float(last[bbl_col]), 6) if pd.notna(last[bbl_col]) else None,
        "bollinger_upper": round(float(last[bbu_col]), 6) if pd.notna(last[bbu_col]) else None,
        "atr14": round(float(last["atr14"]), 6) if pd.notna(last["atr14"]) else None,
        "trend": _trend_label(last),
        "recent_candles_note": (
            f"posledných {RECENT_CANDLES_BARS} hodinových sviečok "
            + ("[open,high,low,close,volume]" if include_volume else "[open,high,low,close]")
            + ", od najstaršej po najnovšiu (posledná = aktuálna)"
        ),
        "recent_candles": _recent_candles(df, RECENT_CANDLES_BARS, include_volume),
    }
    return summary


def _recent_candles(df: pd.DataFrame, bars: int, include_volume: bool = False) -> list[list]:
    """Kompaktny zoznam [open,high,low,close(,volume)] za poslednych `bars`
    hodinovych sviecok - Claude na zaklade toho sam posudi strukturu trhu
    (support/resistance, breakout, swing high/low, a ak je volume prítomný aj
    objemovu divergenciu) ako skuseny analytik pozerajuci sa na graf, namiesto
    kodovania konkretnych pomenovanych formacii (cup&handle, diamanty a pod. -
    maju slabu a nekonzistentnu empiricku oporu naprieč studiami, na rozdiel od
    matematicky presne definovanych indikatorov vyssie). Bez timestampov -
    poradie + aktualny cas z user promptu stacia, a setria to tokeny.

    include_volume: zapni LEN pre assety s kompletnymi volume datami z
    yfinance (NAS100/NVDA/GOLD - overene 99-100% pokrytie). ADA-USD ma cez
    yfinance len ~41% barov s nenulovym volume (aj ine agregovane zdroje, nie
    Strike-ovo vlastne order-book volume) - nespolahlive na stavanie signalu."""
    recent = df.tail(bars)
    if include_volume:
        # volume moze byt NaN (chybajuci yfinance match pre tuto hodinu - viz
        # _merge_volume) - serializujeme ako null (genuinne chybajuce), nie
        # ako 0.0 (co by vyzeralo ako konkretne nameraný nulovy objem).
        return [
            [round(float(r.open), 6), round(float(r.high), 6),
             round(float(r.low), 6), round(float(r.close), 6),
             round(float(r.volume), 2) if pd.notna(r.volume) else None]
            for r in recent.itertuples()
        ]
    return [
        [round(float(r.open), 6), round(float(r.high), 6),
         round(float(r.low), 6), round(float(r.close), 6)]
        for r in recent.itertuples()
    ]


# RSI pasmo povazovane za "neutralne" (ziadne potvrdene momentum ani jednym
# smerom) - pouzite v _trend_label nizsie na odlisenie skutocne SILNEHO trendu
# od EMA struktury, ktora len este "dobieha" po skorsom prudkom pohybe (2026-08-16,
# viz nizsie).
_NEUTRAL_RSI_LOW, _NEUTRAL_RSI_HIGH = 40, 60


def _trend_label(last_row) -> str:
    """POZOR (2026-08-16): toto je CISTO STRUKTURALNY signal (poradie
    EMA20/50/200 voci cene) - NIKDY sa nepozera na RSI/MACD momentum samo o
    sebe. EMA su spomalene priemery, takze po prudkom pohybe zostanu
    "zoradene" v smere povodneho pohybu ESTE DLHO potom, co sa cena realne
    upokoji/momentum vyprchá (RSI sa vrati do neutralu) - Claude to opakovane
    spravne postrehol v reasoning texte ("label neodpoveda momentum
    indikatorom"), co viedlo k tejto oprave. "strong_*" preto teraz navyse
    vyzaduje, aby RSI NEBOL v neutralnom pasme (potvrdenie, ze ide o skutocne
    aktivny pohyb, nie len zotrvacnost EMA structury) - inak sa vrati
    "*_stalling" (struktura este bear/bull-formacia, ale momentum vyprchalo).
    DOLEZITE pre trade_cycle._ADVERSE_TREND: "*_stalling" zamerne NIE JE v tej
    mnozine (na rozdiel od strong_*/mild_*), takze uz samo o sebe nespusti
    plateny health-check eskalaciu otvorenej pozicie - len skutocne
    potvrdeny/momentum-backed obrat trendu (alebo dosiahnutie stratoveho
    prahu, nezavisle) to sposobi."""
    price = last_row["close"]
    ema20, ema50, ema200 = last_row.get("ema20"), last_row.get("ema50"), last_row.get("ema200")
    rsi = last_row.get("rsi14")
    if pd.isna(ema200):
        return "insufficient_data"

    rsi_neutral = pd.notna(rsi) and _NEUTRAL_RSI_LOW <= rsi <= _NEUTRAL_RSI_HIGH

    if price > ema20 > ema50 > ema200:
        return "uptrend_stalling" if rsi_neutral else "strong_uptrend"
    if price < ema20 < ema50 < ema200:
        return "downtrend_stalling" if rsi_neutral else "strong_downtrend"
    if price > ema200:
        return "mild_uptrend"
    return "mild_downtrend"


def _load_own_bars(symbol: str, session, lookback_days: int = 30) -> pd.DataFrame:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    bars = (
        session.query(PriceBar)
        .filter(PriceBar.symbol == symbol, PriceBar.hour_start >= cutoff)
        .order_by(PriceBar.hour_start)
        .all()
    )
    if not bars:
        return pd.DataFrame()
    return pd.DataFrame(
        [{"open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in bars],
        index=[b.hour_start for b in bars],
    )


def _own_data_is_fresh(df: pd.DataFrame) -> bool:
    if df.empty or len(df) < MIN_OWN_BARS:
        return False
    last_bar_age = datetime.now(timezone.utc).replace(tzinfo=None) - df.index[-1]
    return last_bar_age <= timedelta(hours=OWN_DATA_STALE_HOURS)


def _merge_volume(df: pd.DataFrame, yf_symbol: str, yf_fallback: str | None) -> pd.DataFrame:
    """Vlastne price_bars nemaju volume (Strike mark_price je len cena, ziadny
    order-book/trade objem) - pre assety, ktore volume-price divergenciu
    vyuzivaju (NAS100/NVDA/GOLD), ho sem doplnime z yfinance ako obohatenie
    (nie ako primarny zdroj ceny). Zlyhanie tu nesmie zhodit cely TA fetch -
    v najhoršom pripade len chyba volume stlpec (NaN, viz nizsie preco NIE 0.0).

    POZOR (2026-08 produkcny incident): yfinance intradenne 1h data pre
    kontinualne futures (napr. NQ=F) bezne zaostavaju za skutocnostou o
    niekolko hodin (na rozdiel od akcii) - nas vlastny price_bars index je
    vsak takmer real-time (poller kazdu minutu). Pre najnovsie hodiny preto
    reindex s toleranciou nizsie nenajde ZIADNU zhodu (chybajuci udaj), NIE
    ze bol objem naozaj nulovy. Predtym sa to cez fillna(0.0) tichy zmenilo
    na FALOSNU nulu, ktora vyzerala ako skutocne namerany udaj a Claude ju
    opakovane vyhodnocoval ako podozrive/nekonzistentne data (data_issue).
    Teraz chybajuci match ostava NaN -> _recent_candles ho seriaizuje ako
    null (genuinne chybajuce), nie 0 (konkretne, zavadzajuce tvrdenie)."""
    try:
        yf_df = fetch_ohlcv(yf_symbol, yf_fallback)
    except Exception as e:
        print(f"[market_data] Volume enrichment z yfinance zlyhal, pokracujem bez volume: {e}")
        df["volume"] = float("nan")
        return df
    if yf_df.empty or "volume" not in yf_df.columns:
        df["volume"] = float("nan")
        return df

    idx = yf_df.index.tz_convert("UTC").tz_localize(None) if yf_df.index.tz is not None else yf_df.index
    vol = pd.Series(yf_df["volume"].values, index=idx)
    df["volume"] = vol.reindex(df.index, method="nearest", tolerance=pd.Timedelta("90min"))
    return df


def _merge_volume_from_binance(df: pd.DataFrame, binance_symbol: str) -> pd.DataFrame:
    """Ako _merge_volume vyssie, ale zdrojom je Binance namiesto yfinance -
    pouziva sa pre ADA/NIGHT (viz assets.py binance_volume_symbol), kde su
    tieto kryptomeny skutocne obchodovane so spolahlivym objemom (na rozdiel
    od yfinance riedkeho/chybajuceho pokrytia pre krypto). Rovnaky
    graceful-degradation vzor: zlyhanie alebo chybajuci match necha volume NaN
    (nie 0.0 - viz komentar v _merge_volume o falosnej nule)."""
    try:
        klines = binance_client.get_hourly_klines(binance_symbol, limit=500)
    except Exception as e:
        print(f"[market_data] Binance volume enrichment zlyhal, pokracujem bez volume: {e}")
        df["volume"] = float("nan")
        return df
    if not klines:
        df["volume"] = float("nan")
        return df

    idx = pd.to_datetime([k["open_time"] for k in klines], unit="ms", utc=True).tz_localize(None)
    vol = pd.Series([k["volume"] for k in klines], index=idx)
    df["volume"] = vol.reindex(df.index, method="nearest", tolerance=pd.Timedelta("90min"))
    return df


def get_price_history(asset: dict, session) -> pd.DataFrame:
    """Primarny zdroj OHLC pre TA vyhodnotenie assetu: vlastne hodinove
    sviecky z price_bars (viz price_poller.py), ktore na rozdiel od yfinance
    zostavaju zive aj mimo obchodnych hodin/cez vikend (Strike perpy
    obchoduju nonstop). Padne spat na yfinance (fetch_ohlcv), ak vlastne data
    chybaju alebo su zastarale (poller nebezal)."""
    symbol = asset["strike_symbol"]
    df = _load_own_bars(symbol, session)
    if _own_data_is_fresh(df):
        if asset.get("binance_volume_symbol"):
            df = _merge_volume_from_binance(df, asset["binance_volume_symbol"])
        elif asset.get("include_volume"):
            df = _merge_volume(df, asset["yf_symbol"], asset.get("yf_fallback"))
        return df

    if asset.get("coingecko_id"):
        print(f"[market_data] {symbol}: vlastne price_bars chybaju/su zastarale, padam spat na CoinGecko.")
        return fetch_ohlcv_coingecko(asset["coingecko_id"])

    if asset.get("yf_volume_only"):
        # yf_symbol ma NEKOMPATIBILNU cenovu skalu s tymto Strike syntetickym
        # trackerom (viz assets.py komentar pri SKHYNIX) - pouzitelny LEN pre
        # _merge_volume vyssie, nikdy ako plnohodnotny OHLC fallback. Radsej
        # prazdne data (cyklus sa preskoci, viz compute_indicators guard) nez
        # tiche zaplnenie zlou skalou.
        print(f"[market_data] {symbol}: vlastne price_bars chybaju/su zastarale a yfinance "
              f"({asset['yf_symbol']}) ma nekompatibilnu cenovu skalu - preskakujem, ziadny fallback.")
        return pd.DataFrame()

    print(f"[market_data] {symbol}: vlastne price_bars chybaju/su zastarale, padam spat na yfinance.")
    return fetch_ohlcv(asset["yf_symbol"], asset.get("yf_fallback"))


# Kolko poslednych hodinovych FundingRateBar zaznamov pouzivame na "priemernu"
# hodnotu v get_funding_snapshot - 24 = posledny den, rozumny kompromis medzi
# aktualnostou a vyhladenim jednorazoveho vykyvu.
FUNDING_RECENT_HOURS = 24


def get_funding_snapshot(symbol: str, session, recent_hours: int = FUNDING_RECENT_HOURS) -> dict | None:
    """Aktualna trhova funding rate + kratky nedavny priemer (viz FundingRateBar,
    zbierana v price_poller.poll_prices() z /v2/markets['funding_rate'] - ZIADNY
    extra naklad, ten istý bulk call uz beztak beží kvoli cene). None, ak este
    nemame ziadny zaznam (napr. tesne po nasadeni tejto funkcie) - volajuci v
    tom pripade jednoducho vynecha 'funding' kluc z TA snapshotu."""
    latest = (
        session.query(FundingRateBar)
        .filter(FundingRateBar.symbol == symbol)
        .order_by(FundingRateBar.hour_start.desc())
        .first()
    )
    if latest is None:
        return None
    recent = (
        session.query(FundingRateBar)
        .filter(FundingRateBar.symbol == symbol)
        .order_by(FundingRateBar.hour_start.desc())
        .limit(recent_hours)
        .all()
    )
    avg_rate = sum(r.funding_rate for r in recent) / len(recent)
    return {
        "current_rate_pct_per_hour": round(latest.funding_rate * 100, 5),
        "avg_rate_pct_per_hour_recent": round(avg_rate * 100, 5),
        "hours_available": len(recent),
    }


def get_market_snapshot(asset: dict, session) -> dict:
    df = get_price_history(asset, session)
    snapshot = compute_indicators(df, include_volume=asset.get("include_volume", False))
    funding = get_funding_snapshot(asset["strike_symbol"], session)
    if funding is not None:
        snapshot["funding"] = funding
    return snapshot


# Cross-market konfirmacia + VIX regime + bond market (viz Market State & Sentiment
# Framework: Cross-Market Confirmation, VIX Regime, Bond Market). Vsetko su bezplatne
# yfinance tickery - ziadny extra platny data feed netreba.
CROSS_MARKET_TICKERS = {
    "sp500": "^GSPC",
    "russell2000": "^RUT",
    "sox_semiconductors": "^SOX",
    "vix": "^VIX",
    "dxy_dollar_index": "DX-Y.NYB",
    "us10y_yield": "^TNX",
    "us13w_yield": "^IRX",
    "oil_wti": "CL=F",
    "gold": "GC=F",
}

# Global Session Alignment: Azia -> Europa -> US futures.
SESSION_TICKERS = {
    "nikkei_asia": "^N225",
    "hangseng_asia": "^HSI",
    "dax_europe": "^GDAXI",
    "nas100_us_futures": "NQ=F",
}


def _fetch_snapshot(tickers: dict, period: str = "10d", interval: str = "1d") -> dict:
    symbols = list(tickers.values())
    df = yf.download(symbols, period=period, interval=interval, progress=False,
                      auto_adjust=True, group_by="ticker")

    result = {}
    for name, symbol in tickers.items():
        try:
            closes = df[symbol]["Close"].dropna()
            if closes.empty:
                result[name] = None
                continue
            last = float(closes.iloc[-1])
            change_1d_pct = (
                round(float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100), 2)
                if len(closes) > 1 else None
            )
            change_5d_pct = (
                round(float((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100), 2)
                if len(closes) > 5 else None
            )
            result[name] = {
                "last": round(last, 2),
                "change_1d_pct": change_1d_pct,
                "change_5d_pct": change_5d_pct,
            }
        except Exception:
            result[name] = None
    return result


def get_cross_market_snapshot() -> dict:
    """S&P500/Russell/SOX/VIX/DXY/US10Y/US13W/ropa/zlato - cross-market konfirmacia.
    Denne sviecky su tu zamerne: tento blok ma overovat SIRSI trendovu konfirmaciu,
    nie vnutrodenny sum, a "vcerajsia uzavierka" je pre tento ucel dostatocna."""
    return _fetch_snapshot(CROSS_MARKET_TICKERS)


def _pct_change_since(closes: pd.Series, ref_ts, hours: float) -> float | None:
    """Najde bar najblizsie k (ref_ts - hours) a vrati % zmenu k poslednemu baru.
    Casovo zalozene hladanie namiesto pevneho poctu riadkov - rozne trhy maju rôzny
    pocet hodinovych barov za den (NAS100 futures obchoduje takmer 24h/den, Nikkei
    len ~6.5h/den), takze "pred 5 dnami" by pri fixnom riadkovom posune znamenalo
    pre kazdy ticker inu skutocnu casovu vzdialenost."""
    target_ts = ref_ts - pd.Timedelta(hours=hours)
    idx = closes.index.get_indexer([target_ts], method="nearest")[0]
    if idx < 0 or idx >= len(closes) - 1:
        return None
    base = float(closes.iloc[idx])
    if base == 0:
        return None
    return round(float((closes.iloc[-1] - base) / base * 100), 2)


def _fetch_session_snapshot(tickers: dict, period: str = "10d", interval: str = "1h") -> dict:
    """Ako _fetch_snapshot, ale na hodinovych svieckach s casovo zalozenym vyhladavanim
    (_pct_change_since) namiesto dennej uzavierky. Session alignment ma zachytit
    POSLEDNY skutocny pohyb danej relacie (Azia/Europa/US), nie vcerajsiu uzavierku,
    ktora uz moze byt o cely obchodny den stara."""
    symbols = list(tickers.values())
    df = yf.download(symbols, period=period, interval=interval, progress=False,
                      auto_adjust=True, group_by="ticker")

    result = {}
    for name, symbol in tickers.items():
        try:
            closes = df[symbol]["Close"].dropna()
            if closes.empty:
                result[name] = None
                continue
            ref_ts = closes.index[-1]
            result[name] = {
                "last": round(float(closes.iloc[-1]), 2),
                "change_24h_pct": _pct_change_since(closes, ref_ts, hours=24),
                "change_5d_pct": _pct_change_since(closes, ref_ts, hours=5 * 24),
            }
        except Exception:
            result[name] = None
    return result


def get_session_snapshot() -> dict:
    """Azia (Nikkei/HangSeng) -> Europa (DAX) -> US futures - session alignment.
    Hodinove sviecky + casovo zalozeny vypocet (nie denna uzavierka), aby to
    zachytilo skutocny posledny pohyb kazdej relacie, nie zastaraly denny close."""
    return _fetch_session_snapshot(SESSION_TICKERS)


def get_btc_proxy_snapshot() -> dict | None:
    """Volny krypto-makro proxy pre krypto assety (BTC beta) - rovnaky yfinance
    feed ako cross-market/session bloky vyssie, ziadny novy platony zdroj
    netreba. Pouziva sa v ADA aj NIGHT prompte (viz assets.py
    needs_btc_proxy=True a claude_analyst._build_user_prompt)."""
    snap = _fetch_session_snapshot({"btc": "BTC-USD"})
    return snap.get("btc")


if __name__ == "__main__":
    import json

    import assets
    from db import get_session

    _session = get_session()
    print(json.dumps(get_market_snapshot(assets.NAS100, _session), indent=2))
    print(json.dumps(get_cross_market_snapshot(), indent=2))
    print(json.dumps(get_session_snapshot(), indent=2))
    print(json.dumps(get_btc_proxy_snapshot(), indent=2))
    _session.close()
