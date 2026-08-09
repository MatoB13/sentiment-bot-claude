"""
Registry vsetkych obchodovanych assetov (NAS100 + NVDA + ADA + GOLD + WTI +
NIGHT + BTC + HYPE + SKHYNIX).

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
Binance ropa nie je (nie krypto asset). HYPE (2026-08-07) je z rovnakeho
dovodu VYPNUTE - nie je na Binance ani inom overenom zdroji. SKHYNIX
(2026-08-07) je ZAPNUTE cez yfinance - pokrytie overene naozivo (~97%).

coingecko_id (2026-08-07, len HYPE): alternativny OHLC fallback/backfill
zdroj namiesto yfinance pre assety, ktore na Yahoo Finance nemaju data
(viz coingecko_client.py + market_data.fetch_ohlcv_coingecko).

trading_hours_start_utc/end_utc (2026-08-07): KAZDY asset ma tuto dvojicu
teraz explicitne (predtym implicitne zdielana cez config.TRADING_HOURS_*)
- vsetky okrem SKHYNIX (Korea Exchange, iny kontinent/timezone) pouzivaju
rovnaky zdielany NYSE default (viz trade_cycle._required_interval_hours).

trade_interval_hours/off_hours_interval_hours/weekend_interval_hours: KAZDY
asset ma vsetky tri (2026-07-31 zjednotene - predtym mali ADA/NIGHT len jednu
plochu hodnotu bez trading-hours rozlisenia). Pre 24/7 krypto (ADA/NIGHT) su
defaultne vsetky tri rovnake (ziadne skutocne "off hours"/vikend rozlisenie
preň neexistuje), ale su NEZAVISLE nastavitelne cez config.py/Railway -
umoznuje to napr. neskor predlzit vikendovy interval aj pre ne bez zmeny kodu
(viz trade_cycle._required_interval_hours, jednotny mechanizmus pre vsetkych
9 tickerov).

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
    "liquidation_cushion_multiple": config.NAS100_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.NAS100_MARGIN_USD,
    "min_confidence": config.NAS100_MIN_CONFIDENCE,
    "enabled": True,
    "needs_btc_proxy": False,
    "include_volume": True,
    "trade_interval_hours": config.NAS100_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NAS100_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NAS100_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
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
    "liquidation_cushion_multiple": config.NVDA_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.NVDA_MARGIN_USD,
    "min_confidence": config.NVDA_MIN_CONFIDENCE,
    "enabled": config.ENABLE_NVDA,
    "needs_btc_proxy": False,
    "include_volume": True,
    "trade_interval_hours": config.NVDA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NVDA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NVDA_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
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
    "liquidation_cushion_multiple": config.ADA_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.ADA_MARGIN_USD,
    "min_confidence": config.ADA_MIN_CONFIDENCE,
    "enabled": config.ENABLE_ADA,
    "needs_btc_proxy": True,
    "include_volume": True,
    "binance_volume_symbol": "ADAUSDT",
    "trade_interval_hours": config.ADA_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.ADA_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.ADA_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "ADAUSD"},
}

GOLD = {
    "name": "GOLD",
    "asset_class": "commodity",
    "strike_symbol": config.STRIKE_GOLD_SYMBOL,
    "yf_symbol": "GC=F",
    # POZOR (2026-08-09, viz SKHYNIX incident nizsie): GLD ETF NIE JE v rovnakej
    # skale ako spot/futures zlato (GLD ~1/10 unce na akciu) - overene naozivo,
    # Strike live ~4349 vs GC=F ~4400 (OK) vs GLD ~398 (10.9x mimo). GC=F ako
    # primarny zdroj je spolahlivy, preto radsej ZIADEN fallback (prazdne data,
    # cyklus sa preskoci) nez skodlivo zle skalovany.
    "yf_fallback": None,
    "sl_pct": config.GOLD_SL_PCT,
    "tp_pct": config.GOLD_TP_PCT,
    "leverage": config.GOLD_LEVERAGE,
    "liquidation_cushion_multiple": config.GOLD_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.GOLD_MARGIN_USD,
    "min_confidence": config.GOLD_MIN_CONFIDENCE,
    "enabled": config.ENABLE_GOLD,
    "needs_btc_proxy": False,
    "include_volume": True,
    "trade_interval_hours": config.GOLD_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.GOLD_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.GOLD_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "GLD"},
}

WTI = {
    "name": "WTI",
    "asset_class": "commodity",
    "strike_symbol": config.STRIKE_WTI_SYMBOL,
    "yf_symbol": "CL=F",
    # POZOR (2026-08-09, viz SKHYNIX incident nizsie): USO ETF NIE JE 1:1 s
    # cenou WTI (historicke reverse-splity/roll-costy skreslili pomer) -
    # overene naozivo, Strike live ~77.6 vs CL=F ~78.2 (OK) vs USO ~118.0
    # (1.5x mimo). CL=F ako primarny zdroj je spolahlivy, preto radsej ZIADEN
    # fallback (prazdne data, cyklus sa preskoci) nez zle skalovany.
    "yf_fallback": None,
    "sl_pct": config.WTI_SL_PCT,
    "tp_pct": config.WTI_TP_PCT,
    "leverage": config.WTI_LEVERAGE,
    "liquidation_cushion_multiple": config.WTI_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.WTI_MARGIN_USD,
    "min_confidence": config.WTI_MIN_CONFIDENCE,
    "enabled": config.ENABLE_WTI,
    "needs_btc_proxy": False,
    "include_volume": False,
    "trade_interval_hours": config.WTI_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.WTI_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.WTI_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
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
    "liquidation_cushion_multiple": config.NIGHT_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.NIGHT_MARGIN_USD,
    "min_confidence": config.NIGHT_MIN_CONFIDENCE,
    "enabled": config.ENABLE_NIGHT,
    "needs_btc_proxy": True,
    "include_volume": True,
    "binance_volume_symbol": "NIGHTUSDT",
    "trade_interval_hours": config.NIGHT_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NIGHT_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NIGHT_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    # NIKDY holé "NIGHT" (bezne anglicke slovo, 87k+ falosnych zhod - overene
    # naozivo 2026-07-31). "Midnight" + entity_types=cryptocurrency davaju ciste
    # relevantne vysledky (Cardano Midnight sidechain, Wanchain bridge hack a pod).
    "marketaux_query": {"search": "Midnight", "entity_types": "cryptocurrency"},
}

BTC = {
    "name": "BTC",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_BTC_SYMBOL,
    "yf_symbol": "BTC-USD",
    "yf_fallback": None,
    "sl_pct": config.BTC_SL_PCT,
    "tp_pct": config.BTC_TP_PCT,
    "leverage": config.BTC_LEVERAGE,
    "liquidation_cushion_multiple": config.BTC_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.BTC_MARGIN_USD,
    "min_confidence": config.BTC_MIN_CONFIDENCE,
    "enabled": config.ENABLE_BTC,
    # BTC je uz SAMO tou proxy referenciou pre ADA/NIGHT (viz
    # market_data.get_btc_proxy_snapshot) - nepotrebuje sam seba ako kontext.
    "needs_btc_proxy": False,
    "include_volume": True,
    "binance_volume_symbol": "BTCUSDT",
    "trade_interval_hours": config.BTC_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.BTC_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.BTC_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "BTCUSD"},
}

HYPE = {
    "name": "HYPE",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_HYPE_SYMBOL,
    # Ziadny spolahlivy yfinance ticker (HYPE-USD nevracia data) ani Binance
    # par (HYPEUSDT/HYPEUSDC oba neplatne, overene naozivo 2026-08-07) - preto
    # yf_symbol ostava len ako NEPOUZITY fallback pre pripad, ze coingecko_id
    # zlyha (fetch_ohlcv naň aj tak vrati prazdny DataFrame, graceful no-op).
    # Skutocny fallback/backfill zdroj je coingecko_id nizsie.
    "yf_symbol": "HYPE-USD",
    "yf_fallback": None,
    "coingecko_id": "hyperliquid",
    "sl_pct": config.HYPE_SL_PCT,
    "tp_pct": config.HYPE_TP_PCT,
    "leverage": config.HYPE_LEVERAGE,
    "liquidation_cushion_multiple": config.HYPE_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.HYPE_MARGIN_USD,
    "min_confidence": config.HYPE_MIN_CONFIDENCE,
    "enabled": config.ENABLE_HYPE,
    "needs_btc_proxy": True,
    # FALSE zamerne - ziaden overeny spolahlivy volume zdroj (nie je na
    # Binance, CoinGecko OHLC endpoint volume neposkytuje) - rovnaky dovod
    # ako WTI.
    "include_volume": False,
    "trade_interval_hours": config.HYPE_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.HYPE_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.HYPE_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    # NIKDY holé "HYPE" (bezne anglicke slovo) - viz social_sentiment.py/
    # marketaux_client rovnaky vzor ako NIGHT.
    "marketaux_query": {"search": "Hyperliquid", "entity_types": "cryptocurrency"},
}

SKHYNIX = {
    "name": "SKHYNIX",
    "asset_class": "stock",
    "strike_symbol": config.STRIKE_SKHYNIX_SYMBOL,
    "yf_symbol": "000660.KS",
    "yf_fallback": None,
    # POZOR (2026-08-09, produkcny incident): "000660.KS" je REALNA KRX
    # burzova cena SK Hynix v KRW (~1 400 000+) - Strike-ove SKHYNIX-USD je
    # ale SYNTETICKY USD tracker uplne inej skaly (~1000-1100), NIE 1:1 s
    # realnou akciou (na rozdiel od CXMT-USD/SPCX, ktore su na Yahoo Finance
    # ako ROVNAKY synteticky nastroj - overene ziadny "SKHYNIX-USD" ekvivalent
    # neexistuje). Pouzitie "000660.KS" ako OHLC fallback (ked vlastne
    # price_bars chybaju/su zastarale) zaplnilo price_bars mesiac KRW-skalych
    # dat (2026-06-26 az 2026-08-07), co viedlo Claude k SL/TP/watch_price v
    # uplne zlej skale (napr. watch "below 1400000" pri live cene ~1020,
    # ktore je VZDY pravda -> watch_monitor spustal cyklus na kazdom tiku).
    # yf_volume_only=True preto zakazuje pouzitie "000660.KS" ako OHLC/cena
    # zdroj (viz market_data.get_price_history + price_poller.backfill_if_empty) -
    # ostava dovolene LEN pre _merge_volume (pocet obchodovanych akcii je
    # skalovo nezavisly udaj, na rozdiel od ceny). Kontaminovane riadky v
    # price_bars boli rucne vymazane (2026-08-09) - do nazbierania
    # MIN_OWN_BARS (210) vlastnych hodinovych barov (~7 dni) bude SKHYNIX bez
    # OHLC fallback preskakovat cykly (rovnaka situacia ako HYPE bez
    # coingecko_id - ziaden kompatibilny nahradny zdroj neexistuje).
    "yf_volume_only": True,
    "sl_pct": config.SKHYNIX_SL_PCT,
    "tp_pct": config.SKHYNIX_TP_PCT,
    "leverage": config.SKHYNIX_LEVERAGE,
    "liquidation_cushion_multiple": config.SKHYNIX_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.SKHYNIX_MARGIN_USD,
    "min_confidence": config.SKHYNIX_MIN_CONFIDENCE,
    "enabled": config.ENABLE_SKHYNIX,
    "needs_btc_proxy": False,
    # Overene naozivo 2026-08-07 (yfinance hodinove sviecky, 10 dni): 58/60
    # neprazdnych volume barov (~97% pokrytie) - spolahlive, na rozdiel od
    # povodneho ADA/NIGHT problemu, ktory viedol k Binance volume zdroju.
    # POZOR: volume merge je skalovo nezavisly (pocet akcii, nie cena) -
    # yf_volume_only vyssie preto NEOVPLYVNUJE toto pole.
    "include_volume": True,
    "trade_interval_hours": config.SKHYNIX_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.SKHYNIX_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.SKHYNIX_WEEKEND_INTERVAL_HOURS,
    # JEDINY asset s inou nez zdielanou NYSE trhovou strukturou - viz
    # config.SKHYNIX_TRADING_HOURS_START_UTC/END_UTC (KRX seansa).
    "trading_hours_start_utc": config.SKHYNIX_TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.SKHYNIX_TRADING_HOURS_END_UTC,
    "marketaux_query": {"search": "SK Hynix"},
}

ALL_ASSETS = [NAS100, NVDA, ADA, GOLD, WTI, NIGHT, BTC, HYPE, SKHYNIX]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
