"""
Registry vsetkych obchodovanych assetov (NAS100 + NVDA + ADA).

Kazdy asset je nezavisly "bot" - vlastna poziciu, vlastny risk (SL/TP%, leverage,
margin, min_confidence), vlastne rozhodnutie od Claude - ale vsetky bezia v tom
istom scheduler cykle a zdielaju cross-market/session (a pripadne BTC proxy)
makro fetch, aby sa nefetchovalo to iste 3x (viz trade_cycle.run_all_cycles).
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
}

ALL_ASSETS = [NAS100, NVDA, ADA]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
