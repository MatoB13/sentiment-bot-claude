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
    "effort": config.NAS100_EFFORT,
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
    "effort": config.NVDA_EFFORT,
}

ADA = {
    "name": "ADA",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_ADA_SYMBOL,
    "yf_symbol": "ADA-USD",
    # CoinMarketCal slug (2026-08-19, overene naozivo - "tracked": true) - viz
    # coinmarketcal_client.py.
    "coinmarketcal_slug": "cardano",
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
    # Volitelny effort test (viz config.ADA_EFFORT) - prazdne = bez zmeny.
    "effort": config.ADA_EFFORT,
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
    "effort": config.GOLD_EFFORT,
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
    "effort": config.WTI_EFFORT,
    "needs_eia_data": True,
}

NIGHT = {
    "name": "NIGHT",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_NIGHT_SYMBOL,
    "yf_symbol": "NIGHT-USD",
    # CoinMarketCal slug (2026-08-19, overene naozivo) - projekt sa tam vola
    # "Midnight" (symbol "night"), nie "NIGHT" - viz coinmarketcal_client.py.
    "coinmarketcal_slug": "midnight-3",
    # CoinGecko fallback/backfill (2026-08-19, overene naozivo - realne
    # aktualne OHLC dostupne zadarmo na Demo kluci, rovnaky id ako
    # coinmarketcal_slug vyssie) - rovnaky vzor ako HYPE, viz
    # market_data.get_price_history/fetch_ohlcv_coingecko. Na rozdiel od
    # HYPE ma NIGHT aj binance_volume_symbol nizsie (funguje v hlavnej,
    # vlastnej price_bars ceste - tento fallback sa pouzije len ak vlastne
    # data chybaju/su zastarale, vtedy sa volume jednoducho nedoplni).
    "coingecko_id": "midnight-3",
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
    "effort": config.NIGHT_EFFORT,
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
    "effort": config.BTC_EFFORT,
}

HYPE = {
    "name": "HYPE",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_HYPE_SYMBOL,
    # CoinMarketCal slug (2026-08-19, overene naozivo) - "hyperliquid",
    # napriek tomu, ze yfinance/Binance nemaju spolahlivy zdroj cien nizsie -
    # viz coinmarketcal_client.py.
    "coinmarketcal_slug": "hyperliquid",
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
    "effort": config.HYPE_EFFORT,
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
    "effort": config.SKHYNIX_EFFORT,
}

AAOI = {
    "name": "AAOI",
    "asset_class": "stock",
    "strike_symbol": config.STRIKE_AAOI_SYMBOL,
    "yf_symbol": "AAOI",
    "yf_fallback": None,
    "sl_pct": config.AAOI_SL_PCT,
    "tp_pct": config.AAOI_TP_PCT,
    "leverage": config.AAOI_LEVERAGE,
    "liquidation_cushion_multiple": config.AAOI_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.AAOI_MARGIN_USD,
    "min_confidence": config.AAOI_MIN_CONFIDENCE,
    "enabled": config.ENABLE_AAOI,
    "needs_btc_proxy": False,
    # Realny NASDAQ titul cez yfinance - volume pokrytie by malo byt spolahlive
    # (rovnaky zdroj/vzor ako NVDA/TSLA), overit naozivo po prvom zbere dat.
    "include_volume": True,
    "trade_interval_hours": config.AAOI_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.AAOI_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.AAOI_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "AAOI"},
    "effort": config.AAOI_EFFORT,
}

MINIMAX = {
    "name": "MINIMAX",
    # Sukromna/pre-IPO firma (MiniMax Group) - synteticky Strike tracker,
    # NIE realna verejne obchodovana akcia (na rozdiel od AAOI). Rovnaka
    # kategoria ako CXMT/SPCX na Strike, ziadny z troch je (zatial) v tomto
    # registri sledovany.
    "asset_class": "private_equity_synthetic",
    "strike_symbol": config.STRIKE_MINIMAX_SYMBOL,
    # Ziadny verejny zdroj cenovych dat neexistuje (nie je na yfinance/Binance/
    # CoinGecko - overene naozivo 2026-08-14) - yf_symbol ostava len ako
    # NEPOUZITY fallback (rovnaky vzor ako HYPE pred pridanim coingecko_id),
    # v praxi vzdy vrati prazdny DataFrame. Historia sa DA ZBIERAT LEN cez
    # vlastny 1-min Strike poller (viz price_poller.py zmena na ALL_ASSETS).
    "yf_symbol": "MINIMAX",
    "yf_fallback": None,
    "sl_pct": config.MINIMAX_SL_PCT,
    "tp_pct": config.MINIMAX_TP_PCT,
    "leverage": config.MINIMAX_LEVERAGE,
    "liquidation_cushion_multiple": config.MINIMAX_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.MINIMAX_MARGIN_USD,
    "min_confidence": config.MINIMAX_MIN_CONFIDENCE,
    "enabled": config.ENABLE_MINIMAX,
    "needs_btc_proxy": False,
    # FALSE - ziaden zdroj vobec (viz yf_symbol komentar vyssie), na rozdiel
    # od WTI/HYPE kde aspon cena/OHLC ma zdroj a len volume chyba.
    "include_volume": False,
    "trade_interval_hours": config.MINIMAX_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.MINIMAX_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.MINIMAX_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    # "minimax" je bezny CS/teoria hier pojem (minimax algoritmus) - holy
    # "symbols"/"search" dopyt by davat rovnaky typ falosnych zhod ako NIGHT
    # pred opravou. Viacslovna fraza znizuje riziko kolizie; entity_types sa
    # nedava (MiniMax nie je cryptocurrency ani listovana equity, ziadna
    # Marketaux kategoria nesedi presne).
    "marketaux_query": {"search": "MiniMax Group"},
    "effort": config.MINIMAX_EFFORT,
}

ZEC = {
    "name": "ZEC",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_ZEC_SYMBOL,
    "yf_symbol": "ZEC-USD",
    # CoinMarketCal slug (2026-08-19, overene naozivo) - viz coinmarketcal_client.py.
    "coinmarketcal_slug": "zcash",
    "yf_fallback": None,
    "sl_pct": config.ZEC_SL_PCT,
    "tp_pct": config.ZEC_TP_PCT,
    "leverage": config.ZEC_LEVERAGE,
    "liquidation_cushion_multiple": config.ZEC_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.ZEC_MARGIN_USD,
    "min_confidence": config.ZEC_MIN_CONFIDENCE,
    "enabled": config.ENABLE_ZEC,
    # Rovnaky dovod ako ADA/NIGHT/BTC (24/7 krypto, dava zmysel porovnavat sa
    # voci sirsiemu krypto trhu).
    "needs_btc_proxy": True,
    # Overene naozivo 2026-08-15 (yfinance ZEC-USD + Binance ZECUSDT obe
    # funguju bez problemov) - rovnaky spolahlivy volume zdroj ako ADA/NIGHT.
    "include_volume": True,
    "binance_volume_symbol": "ZECUSDT",
    "trade_interval_hours": config.ZEC_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.ZEC_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.ZEC_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "ZECUSD"},
    "effort": config.ZEC_EFFORT,
}

GOOGL = {
    "name": "GOOGL",
    "asset_class": "stock",
    "strike_symbol": config.STRIKE_GOOGL_SYMBOL,
    "yf_symbol": "GOOGL",
    "yf_fallback": None,
    "sl_pct": config.GOOGL_SL_PCT,
    "tp_pct": config.GOOGL_TP_PCT,
    "leverage": config.GOOGL_LEVERAGE,
    "liquidation_cushion_multiple": config.GOOGL_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.GOOGL_MARGIN_USD,
    "min_confidence": config.GOOGL_MIN_CONFIDENCE,
    "enabled": config.ENABLE_GOOGL,
    "needs_btc_proxy": False,
    # Realny NASDAQ mega-cap titul, rovnaky spolahlivy yfinance zdroj ako NVDA/AAOI.
    "include_volume": True,
    "trade_interval_hours": config.GOOGL_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.GOOGL_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.GOOGL_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "GOOGL"},
    "effort": config.GOOGL_EFFORT,
}

UNITREE = {
    "name": "UNITREE",
    # IPO na sanghajskom STAR Markete presne v den pridania (2026-08-19), akcia
    # +460 az +542% v prvy den. NA ROZDIEL od MINIMAX ide o realnu verejne
    # obchodovanu akciu (nie sukromnu pre-IPO firmu) - ale na cinskej burze,
    # nie US, preto vlastna kategoria namiesto proste "stock".
    "asset_class": "stock_cn_star_market",
    "strike_symbol": config.STRIKE_UNITREE_SYMBOL,
    # Ziadna pouzitelna yfinance historia (IPO doslova dnes + CNY nominal,
    # nekompatibilna skala so Strike USD trackerom, rovnaky dovod ako SKHYNIX
    # yf_volume_only) - yf_symbol ostava len ako NEPOUZITY fallback (rovnaky
    # vzor ako MINIMAX). Jediny realny zdroj historie je vlastny 1-min Strike
    # poller (viz price_poller.py ALL_ASSETS).
    "yf_symbol": "UNITREE",
    "yf_fallback": None,
    "sl_pct": config.UNITREE_SL_PCT,
    "tp_pct": config.UNITREE_TP_PCT,
    "leverage": config.UNITREE_LEVERAGE,
    "liquidation_cushion_multiple": config.UNITREE_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.UNITREE_MARGIN_USD,
    "min_confidence": config.UNITREE_MIN_CONFIDENCE,
    "enabled": config.ENABLE_UNITREE,
    "needs_btc_proxy": False,
    # FALSE - ziaden spolahlivy zdroj (viz yf_symbol komentar vyssie), rovnaky
    # dovod ako MINIMAX.
    "include_volume": False,
    "trade_interval_hours": config.UNITREE_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.UNITREE_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.UNITREE_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    # "Unitree" nie je bezne anglicke slovo (na rozdiel od "night"/"minimax") -
    # zive overene 2026-08-19 (holy aj viacslovny dopyt), oba cisto relevantne,
    # nulove falosne zhody, viacero clankov mladsich nez 25h (IPO prave dnes).
    "marketaux_query": {"search": "Unitree"},
    "effort": config.UNITREE_EFFORT,
}

NEAR = {
    "name": "NEAR",
    "asset_class": "crypto",
    "strike_symbol": config.STRIKE_NEAR_SYMBOL,
    "yf_symbol": "NEAR-USD",
    "yf_fallback": None,
    "sl_pct": config.NEAR_SL_PCT,
    "tp_pct": config.NEAR_TP_PCT,
    "leverage": config.NEAR_LEVERAGE,
    "liquidation_cushion_multiple": config.NEAR_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.NEAR_MARGIN_USD,
    "min_confidence": config.NEAR_MIN_CONFIDENCE,
    "enabled": config.ENABLE_NEAR,
    # Rovnaky dovod ako ADA/NIGHT/BTC/ZEC (24/7 krypto, dava zmysel porovnavat
    # sa voci sirsiemu krypto trhu).
    "needs_btc_proxy": True,
    # Overene naozivo 2026-08-21 (yfinance NEAR-USD + Binance NEARUSDT obe
    # funguju bez problemov) - rovnaky spolahlivy volume zdroj ako ADA/NIGHT/ZEC.
    "include_volume": True,
    "binance_volume_symbol": "NEARUSDT",
    "trade_interval_hours": config.NEAR_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.NEAR_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.NEAR_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "NEARUSD"},
    "effort": config.NEAR_EFFORT,
}

AAPL = {
    "name": "AAPL",
    "asset_class": "stock",
    "strike_symbol": config.STRIKE_AAPL_SYMBOL,
    "yf_symbol": "AAPL",
    "yf_fallback": None,
    "sl_pct": config.AAPL_SL_PCT,
    "tp_pct": config.AAPL_TP_PCT,
    "leverage": config.AAPL_LEVERAGE,
    "liquidation_cushion_multiple": config.AAPL_LIQUIDATION_CUSHION_MULTIPLE,
    "margin_usd": config.AAPL_MARGIN_USD,
    "min_confidence": config.AAPL_MIN_CONFIDENCE,
    "enabled": config.ENABLE_AAPL,
    "needs_btc_proxy": False,
    # Realny NASDAQ mega-cap titul, rovnaky spolahlivy yfinance zdroj ako NVDA/GOOGL/AAOI.
    "include_volume": True,
    "trade_interval_hours": config.AAPL_TRADE_INTERVAL_HOURS,
    "off_hours_interval_hours": config.AAPL_OFF_HOURS_INTERVAL_HOURS,
    "weekend_interval_hours": config.AAPL_WEEKEND_INTERVAL_HOURS,
    "trading_hours_start_utc": config.TRADING_HOURS_START_UTC,
    "trading_hours_end_utc": config.TRADING_HOURS_END_UTC,
    "marketaux_query": {"symbols": "AAPL"},
    "effort": config.AAPL_EFFORT,
}

ALL_ASSETS = [NAS100, NVDA, ADA, GOLD, WTI, NIGHT, BTC, HYPE, SKHYNIX, AAOI, MINIMAX, ZEC, GOOGL, UNITREE, NEAR, AAPL]


def enabled_assets() -> list[dict]:
    return [a for a in ALL_ASSETS if a["enabled"]]


def by_symbol(strike_symbol: str) -> dict | None:
    for a in ALL_ASSETS:
        if a["strike_symbol"] == strike_symbol:
            return a
    return None
