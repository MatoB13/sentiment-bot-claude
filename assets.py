"""
Registry vsetkych obchodovanych assetov (NAS100 + NVDA + ADA + GOLD).

Kazdy asset je nezavisly "bot" - vlastna poziciu, vlastny risk (SL/TP%, leverage,
margin, min_confidence), vlastne rozhodnutie od Claude - ale vsetky bezia v tom
istom scheduler cykle a zdielaju cross-market/session (a pripadne BTC proxy)
makro fetch, aby sa nefetchovalo to iste 4x (viz trade_cycle.run_all_cycles).

GOLD je zamerne pridany ako protivietor k prevazne risk-on smerovaniu
NAS100/NVDA/ADA (safe-haven asset, VIX naň posobi opacne nez na risk-on aktiva -
viz claude_analyst._COMMODITY_MACRO_RULES).

include_volume: zapnute len pre NAS100/NVDA/GOLD, kde ma yfinance kompletne
(99-100%) volume data (overene 2026-07-24). Pre ADA je cez yfinance len ~41%
barov s nenulovym volume (a aj tak je to iny trh nez Strike-ov vlastny
order-book) - zamerne VYPNUTE, aby chybajuce/nulove hodnoty neskreslovali
priemer a nevytvarali falosne "objemove spike" signaly.

variable_interval: zapnute len pre NAS100/NVDA/GOLD - mimo trading hours a cez
vikend bezia rjadsie (viz trade_cycle._required_interval_hours), kedze
podkladovy trh v tom case realne stoji/je tichy. ADA (24/7 krypto) ma toto
VYPNUTE - beri vzdy na zakladnom TRADE_INTERVAL_HOURS, ziadne realne "off
hours" pre nu neexistuju.
"""
import config

NAS100 = {
    "name": "NAS100",
    "asset_class": "index",
    "strike_symbol": config.STRIKE_NAS100_SYMBOL,
    "yf_symbol": "NQ=F",
    "yf_fallback": "^NDX",
    "sl_pct": config.DEFAULT_SL_PCT,
    "tp_pct": config.DEFAULT_TP_PCT,
    "leverage": config.LEVERAGE,
    "margin_usd": config.MARGIN_USD,
    "min_confidence": config.MIN_CONFIDENCE,
    "enabled": True,
    "needs_btc_proxy": False,
    "include_volume": True,
    "variable_interval": True,
}

NVDA = {
    "name": "NVDA",
    "asset_class": "stock",
    "strike_symbol": config.STRIKE_NVDA_SYMBOL,
    "yf_symbol": "NVDA",
    "yf_fallback": None,
    "sl_pct": config.NVDA_SL_PCT,
    "tp_pct": config.NVDA_TP_PCT,
    "leverage": config.NVDA_LEVERAGE,
    "margin_usd": config.NVDA_MARGIN_USD,
    "min_confidence": config.NVDA_MIN_CONFIDENCE,
    "enabled": config.ENABLE_NVDA,
    "needs_btc_proxy": False,
    "include_volume": True,
    "variable_interval": True,
}

ADA = {
    "name": "ADA",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_ADA_SYMBOL,
    "yf_symbol": "ADA-USD",
    "yf_fallback": None,
    "sl_pct": config.ADA_SL_PCT,
    "tp_pct": config.ADA_TP_PCT,
    "leverage": config.ADA_LEVERAGE,
    "margin_usd": config.ADA_MARGIN_USD,
    "min_confidence": config.ADA_MIN_CONFIDENCE,
    "enabled": config.ENABLE_ADA,
    "needs_btc_proxy": True,
    "include_volume": False,
    "variable_interval": False,
}

GOLD = {
    "name": "GOLD",
    "asset_class": "commodity",
    "strike_symbol": config.STRIKE_GOLD_SYMBOL,
    "yf_symbol": "GC=F",
    "yf_fallback": "GLD",
    "sl_pct": config.GOLD_SL_PCT,
    "tp_pct": config.GOLD_TP_PCT,
    "leverage": config.GOLD_LEVERAGE,
    "margin_usd": config.GOLD_MARGIN_USD,
    "min_confidence": config.GOLD_MIN_CONFIDENCE,
    "enabled": config.ENABLE_GOLD,
    "needs_btc_proxy": False,
    "include_volume": True,
    "variable_interval": True,
}

ALL_ASSETS = [NAS100, NVDA, ADA, GOLD]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
