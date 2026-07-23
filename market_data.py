"""
Ziskanie cenovych dat pre obchodovane assety (NAS100/NVDA/ADA) a vypocet TA
indikatorov.

NAS100 na Strike je syntetický perpetuál sledujúci index Nasdaq-100 - pre
historické OHLCV a TA pouzivame verejny proxy feed (^NDX index alebo NQ=F
futures) cez yfinance. NVDA a ADA-USD su na yfinance dostupne priamo (ziadny
proxy netreba). Realna vstupna/vystupna cena obchodu sa vzdy berie z live ceny
na Strike (strike_client.get_markets()), TA slúži len ako kontext pre
rozhodovanie.
"""
import pandas as pd
import pandas_ta as ta
import yfinance as yf


def fetch_ohlcv(symbol: str = "NQ=F", fallback: str | None = "^NDX",
                 period: str = "30d", interval: str = "1h") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty and fallback:
        df = yf.download(fallback, period=period, interval=interval, progress=False, auto_adjust=True)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    return df.dropna()


def compute_indicators(df: pd.DataFrame) -> dict:
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
    }
    return summary


def _trend_label(last_row) -> str:
    price = last_row["close"]
    ema20, ema50, ema200 = last_row.get("ema20"), last_row.get("ema50"), last_row.get("ema200")
    if pd.isna(ema200):
        return "insufficient_data"
    if price > ema20 > ema50 > ema200:
        return "strong_uptrend"
    if price < ema20 < ema50 < ema200:
        return "strong_downtrend"
    if price > ema200:
        return "mild_uptrend"
    return "mild_downtrend"


def get_market_snapshot(symbol: str = "NQ=F", fallback: str | None = "^NDX") -> dict:
    df = fetch_ohlcv(symbol, fallback)
    return compute_indicators(df)


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
    """Volny krypto-makro proxy pre ADA (BTC beta) - rovnaky yfinance feed ako
    cross-market/session bloky vyssie, ziadny novy platony zdroj netreba.
    Pouziva sa len v ADA prompte (viz assets.ADA['needs_btc_proxy'] a
    claude_analyst._build_user_prompt)."""
    snap = _fetch_session_snapshot({"btc": "BTC-USD"})
    return snap.get("btc")


if __name__ == "__main__":
    import json
    print(json.dumps(get_market_snapshot(), indent=2))
    print(json.dumps(get_cross_market_snapshot(), indent=2))
    print(json.dumps(get_session_snapshot(), indent=2))
    print(json.dumps(get_btc_proxy_snapshot(), indent=2))
