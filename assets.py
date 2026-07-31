"""
Registry vsetkych obchodovanych assetov (NAS100 + NVDA + ADA + GOLD + WTI + NIGHT).

Kazdy asset je nezavisly "bot" - vlastna poziciu, vlastny risk (SL/TP%, leverage,
margin, min_confidence), vlastne rozhodnutie od Claude - ale vsetky bezia v tom
istom scheduler cykle a zdielaju cross-market/session (a pripadne BTC proxy)
makro fetch, aby sa nefetchovalo to iste 6x (viz trade_cycle.run_all_cycles).

GOLD je zamerne pridany ako protivietor k prevazne risk-on smerovaniu
NAS100/NVDA/ADA (safe-haven asset, VIX naň posobi opacne nez na risk-on aktiva -
viz claude_analyst._COMMODITY_MACRO_RULES). WTI (ropa) pridany 2026-07-31 ako
vyraznejsie odlisny ticker od NAS100/ADA/GOLD - iny driver (OPEC+/geopolitika/
dopyt), NIE safe-haven ako zlato (viz claude_analyst._ENERGY_MACRO_RULES). NIGHT
(Midnight, Cardano privacy sidechain) pridany v tom istom kroku - vyrazne
rizikovejsi/volatilnejsi mladý krypto token (nedavny Wanchain bridge hack
2026-07-20), preto najnizsia paka a najsirsie SL/TP zo vsetkych.

include_volume: zapnute len pre NAS100/NVDA/GOLD, kde ma yfinance kompletne
(99-100%) volume data (overene 2026-07-24). Pre ADA je cez yfinance len ~41%
barov s nenulovym volume (a aj tak je to iny trh nez Strike-ov vlastny
order-book) - zamerne VYPNUTE, aby chybajuce/nulove hodnoty neskreslovali
priemer a nevytvarali falosne "objemove spike" signaly. WTI/NIGHT su tiez
zamerne VYPNUTE - volume kompletnost pre WTI (CL=F) nebola empiricky overena
ako pri ostatnych, a NIGHT je pravdepodobne mimo yfinance pokrytia celkom
(velmi mlady/nizko-kapitalizovany token).

variable_interval: zapnute len pre NAS100/NVDA/GOLD/WTI - mimo trading hours a
cez vikend bezia rjadsie (viz trade_cycle._required_interval_hours), kedze
podkladovy trh v tom case realne stoji/je tichy. ADA/NIGHT (24/7 krypto) maju
toto VYPNUTE - beria vzdy na zakladnom trade_interval_hours, ziadne realne "off
hours" pre ne neexistuju.
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
    "trade_interval_hours": config.NAS100_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NAS100_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NAS100_WEEKEND_INTERVAL_HOURS,
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
    "trade_interval_hours": config.NVDA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NVDA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NVDA_WEEKEND_INTERVAL_HOURS,
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
    "trade_interval_hours": config.ADA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.ADA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.ADA_WEEKEND_INTERVAL_HOURS,
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
    "trade_interval_hours": config.GOLD_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.GOLD_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.GOLD_WEEKEND_INTERVAL_HOURS,
}

WTI = {
    "name": "WTI",
    "asset_class": "commodity",
    "strike_symbol": config.STRIKE_WTI_SYMBOL,
    "yf_symbol": "CL=F",
    "yf_fallback": "USO",
    "sl_pct": config.WTI_SL_PCT,
    "tp_pct": config.WTI_TP_PCT,
    "leverage": config.WTI_LEVERAGE,
    "margin_usd": config.WTI_MARGIN_USD,
    "min_confidence": config.WTI_MIN_CONFIDENCE,
    "enabled": config.ENABLE_WTI,
    "needs_btc_proxy": False,
    "include_volume": False,
    "variable_interval": True,
    "trade_interval_hours": config.WTI_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.WTI_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.WTI_WEEKEND_INTERVAL_HOURS,
}

NIGHT = {
    "name": "NIGHT",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_NIGHT_SYMBOL,
    "yf_symbol": "NIGHT-USD",
    "yf_fallback": None,
    "sl_pct": config.NIGHT_SL_PCT,
    "tp_pct": config.NIGHT_TP_PCT,
    "leverage": config.NIGHT_LEVERAGE,
    "margin_usd": config.NIGHT_MARGIN_USD,
    "min_confidence": config.NIGHT_MIN_CONFIDENCE,
    "enabled": config.ENABLE_NIGHT,
    "needs_btc_proxy": True,
    "include_volume": False,
    "variable_interval": False,
    "trade_interval_hours": config.NIGHT_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NIGHT_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NIGHT_WEEKEND_INTERVAL_HOURS,
}

ALL_ASSETS = [NAS100, NVDA, ADA, GOLD, WTI, NIGHT]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
