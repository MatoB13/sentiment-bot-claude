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

include_volume: NAS100/NVDA/GOLD cez yfinance (99-100% pokrytie, overene
2026-07-24). ADA/NIGHT cez Binance (binance_volume_symbol nizsie) namiesto
yfinance - Binance realne obchoduje tieto kryptomeny so spolahlivym objemom,
na rozdiel od yfinance riedkeho ~41% pokrytia pre ADA a takmer ziadneho pre
NIGHT (viz binance_client.py + market_data._merge_volume_from_binance,
pridane 2026-08-06). WTI zostava zamerne VYPNUTE - volume kompletnost pre
WTI (CL=F) cez yfinance nebola empiricky overena ako pri ostatnych, a na
Binance ropa nie je (nie krypto asset).

trade_interval_hours/off_hours_interval_hours/weekend_interval_hours: KAZDY
asset ma vsetky tri (2026-07-31 zjednotene - predtym mali ADA/NIGHT len jednu
plochu hodnotu bez trading-hours rozlisenia). Pre 24/7 krypto (ADA/NIGHT) su
defaultne vsetky tri rovnake (ziadne skutocne "off hours"/vikend rozlisenie
preň neexistuje), ale su NEZAVISLE nastavitelne cez config.py/Railway -
umoznuje to napr. neskor predlzit vikendovy interval aj pre ne bez zmeny kodu
(viz trade_cycle._required_interval_hours, jednotny mechanizmus pre vsetkych
6 tickerov).

marketaux_query (2026-07-31): presny dopyt pre marketaux_client.get_news_sentiment
pre kazdy asset - NIKDY nepouzivat holy ticker/nazov bez overenia (napr. "NIGHT"
samotne je bezne anglicke slovo a "ADA"/"BTC" davaju falosne zhody s
nesuvisiacimi ETF/tickermi - vsetko tu bolo naozivo overene 2026-07-31). WTI
navyse needs_eia_data=True (tyzdenne zasoby ropy priamo z eia_client, viz
trade_cycle.py).
"""
import config

NAS100 = {
    "name": "NAS100",
    "asset_class": "index",
    "strike_symbol": config.STRIKE_NAS100_SYMBOL,
    "yf_symbol": "NQ=F",
    "yf_fallback": "^NDX",
    "sl_pct": config.NAS100_SL_PCT,
    "tp_pct": config.NAS100_TP_PCT,
    "leverage": config.NAS100_LEVERAGE,
    "margin_usd": config.NAS100_MARGIN_USD,
    "min_confidence": config.NAS100_MIN_CONFIDENCE,
    "enabled": True,
    "needs_btc_proxy": False,
    "include_volume": True,
    "trade_interval_hours": config.NAS100_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NAS100_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NAS100_WEEKEND_INTERVAL_HOURS,
    "marketaux_query": {"symbols": "QQQ"},
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
    "trade_interval_hours": config.NVDA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NVDA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NVDA_WEEKEND_INTERVAL_HOURS,
    "marketaux_query": {"symbols": "NVDA"},
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
    "include_volume": True,
    "binance_volume_symbol": "ADAUSDT",
    "trade_interval_hours": config.ADA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.ADA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.ADA_WEEKEND_INTERVAL_HOURS,
    "marketaux_query": {"symbols": "ADAUSD"},
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
    "trade_interval_hours": config.GOLD_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.GOLD_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.GOLD_WEEKEND_INTERVAL_HOURS,
    "marketaux_query": {"symbols": "GLD"},
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
    "trade_interval_hours": config.WTI_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.WTI_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.WTI_WEEKEND_INTERVAL_HOURS,
    "marketaux_query": {"symbols": "USO"},
    "needs_eia_data": True,
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
    "include_volume": True,
    "binance_volume_symbol": "NIGHTUSDT",
    "trade_interval_hours": config.NIGHT_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NIGHT_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NIGHT_WEEKEND_INTERVAL_HOURS,
    # NIKDY holé "NIGHT" (bezne anglicke slovo, 87k+ falosnych zhod - overene
    # naozivo 2026-07-31). "Midnight" + entity_types=cryptocurrency davaju ciste
    # relevantne vysledky (Cardano Midnight sidechain, Wanchain bridge hack a pod).
    "marketaux_query": {"search": "Midnight", "entity_types": "cryptocurrency"},
}

ALL_ASSETS = [NAS100, NVDA, ADA, GOLD, WTI, NIGHT]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
