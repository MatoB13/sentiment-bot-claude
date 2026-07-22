"""
Ziskanie cenovych dat pre NAS100 a vypocet TA indikatorov.

NAS100 na Strike je syntetický perpetuál sledujúci index Nasdaq-100.
Pre historické OHLCV a TA pouzivame verejny proxy feed (^NDX index alebo
NQ=F futures) cez yfinance - realna vstupna/vystupna cena obchodu sa berie
z live ceny na Strike (strike_client.get_markets()), TA slúži len ako kontext
pre rozhodovanie.
"""
import pandas as pd
import pandas_ta as ta
import yfinance as yf


def fetch_ohlcv(symbol: str = "NQ=F", period: str = "30d", interval: str = "1h") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty:
        # fallback na index misto futures
        df = yf.download("^NDX", period=period, interval=interval, progress=False, auto_adjust=True)
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

    summary = {
        "last_price": round(float(last["close"]), 2),
        "change_24h_pct": round(float(change_24h_pct), 2),
        "rsi14": round(float(last["rsi14"]), 1) if pd.notna(last["rsi14"]) else None,
        "macd": round(float(last[macd_col]), 2) if pd.notna(last[macd_col]) else None,
        "macd_signal": round(float(last[macds_col]), 2) if pd.notna(last[macds_col]) else None,
        "ema20": round(float(last["ema20"]), 2) if pd.notna(last["ema20"]) else None,
        "ema50": round(float(last["ema50"]), 2) if pd.notna(last["ema50"]) else None,
        "ema200": round(float(last["ema200"]), 2) if pd.notna(last["ema200"]) else None,
        "bollinger_lower": round(float(last[bbl_col]), 2) if pd.notna(last[bbl_col]) else None,
        "bollinger_upper": round(float(last[bbu_col]), 2) if pd.notna(last[bbu_col]) else None,
        "atr14": round(float(last["atr14"]), 2) if pd.notna(last["atr14"]) else None,
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


def get_market_snapshot() -> dict:
    df = fetch_ohlcv()
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
    """S&P500/Russell/SOX/VIX/DXY/US10Y/US13W/ropa/zlato - cross-market konfirmacia."""
    return _fetch_snapshot(CROSS_MARKET_TICKERS)


def get_session_snapshot() -> dict:
    """Azia (Nikkei/HangSeng) -> Europa (DAX) -> US futures - session alignment."""
    return _fetch_snapshot(SESSION_TICKERS)


if __name__ == "__main__":
    import json
    print(json.dumps(get_market_snapshot(), indent=2))
    print(json.dumps(get_cross_market_snapshot(), indent=2))
    print(json.dumps(get_session_snapshot(), indent=2))
