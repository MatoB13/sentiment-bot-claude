import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
# Volitelny per-ticker override output_config.effort (low/medium/high/xhigh/max) -
# prazdny retazec (default) = output_config sa vobec neposle, API pouzije svoj
# vlastny default ("high" na Sonnet 5). Pridane 2026-08-14 na obmedzeny test
# vyssej hlbky reasoningu LEN pre ADA (nastav ADA_EFFORT=xhigh na Railway).
# ROZSIRENE 2026-08-20 (na ziadost pouzivatela) na VSETKY tickery - predtym
# malo len ADA v assets.py "effort" kluc, takze napr. SKHYNIX_EFFORT=xhigh na
# Railway by nemalo ZIADNY ucinok (ziadny kod by ho vobec necital). Kazdy
# ticker teraz ma vlastnu {TICKER}_EFFORT premennu, defaultne prazdnu (=
# API default "high", ziadna zmena sucasneho spravania, kym niekto explicitne
# nenastavi Railway var).
ADA_EFFORT = os.getenv("ADA_EFFORT", "")
NAS100_EFFORT = os.getenv("NAS100_EFFORT", "")
NVDA_EFFORT = os.getenv("NVDA_EFFORT", "")
GOLD_EFFORT = os.getenv("GOLD_EFFORT", "")
WTI_EFFORT = os.getenv("WTI_EFFORT", "")
NIGHT_EFFORT = os.getenv("NIGHT_EFFORT", "")
BTC_EFFORT = os.getenv("BTC_EFFORT", "")
HYPE_EFFORT = os.getenv("HYPE_EFFORT", "")
SKHYNIX_EFFORT = os.getenv("SKHYNIX_EFFORT", "")
AAOI_EFFORT = os.getenv("AAOI_EFFORT", "")
MINIMAX_EFFORT = os.getenv("MINIMAX_EFFORT", "")
ZEC_EFFORT = os.getenv("ZEC_EFFORT", "")
GOOGL_EFFORT = os.getenv("GOOGL_EFFORT", "")
UNITREE_EFFORT = os.getenv("UNITREE_EFFORT", "")

# Strike
STRIKE_API_PRIVATE_KEY = os.getenv("STRIKE_API_PRIVATE_KEY", "")
STRIKE_API_PUBLIC_KEY = os.getenv("STRIKE_API_PUBLIC_KEY", "")
STRIKE_BASE_URL = os.getenv("STRIKE_BASE_URL", "https://api.strikefinance.org")
STRIKE_NAS100_SYMBOL = os.getenv("STRIKE_NAS100_SYMBOL", "NAS100-USD")

# Twitter
ENABLE_TWITTER = _bool("ENABLE_TWITTER", "false")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

# --- Doplnkove datove zdroje (2026-07-31) - vsetky volitelne (chybajuci kluc =
# proste sa ta sekcia promptu vynecha, nikdy neblokuje cyklus, viz *_client.py). ---
# EIA (US Energy Information Administration) - volne API, len pre WTI (tyzdenne
# zasoby ropy - presne cislo namiesto spolahnutia sa na web_search).
# Registracia: https://www.eia.gov/opendata/register.php
# CoinGecko - volny "Demo" API kluc (bez platobnej karty, dedikovana kvota
# ~30 req/min NEZDIELANA s inymi anonymnymi volajucimi) - len pre HYPE OHLC
# fallback (viz coingecko_client.py). Bez kluca funguje aj tak (anonymny
# pristup), ale Railway zdielana cloud IP adresa naraza na 429 Too Many
# Requests (overene naozivo 2026-08-08 - rovnaka trieda problemu ako Binance
# blokovanie US cloud IP). Registracia: https://www.coingecko.com/en/developers/dashboard
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
# FRED (St. Louis Fed) - volne API, zdielane pre vsetky assety (CPI/Core CPI/
# Fed funds rate - presne cislo namiesto web_search odhadu). Registracia:
# https://fredaccount.stlouisfed.org
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
# Marketaux - free tier (100 req/den) news+sentiment API, per-asset (viz
# assets.py marketaux_query). Registracia: https://www.marketaux.com
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY", "")
# CoinMarketCal - krypto-projektovy kalendar udalosti (burzove listingy,
# hlasovania, protokolove upgrady, token unlocky), per-asset (viz assets.py
# coinmarketcal_slug) - 2026-08-19. Free plan ma kreditovy kvoten (nie
# klasicky rate-limit), preto sa vola LEN raz denne z coinmarketcal_client.
# poll_events, nikdy zivo pocas obchodneho cyklu. Registracia:
# https://coinmarketcal.com/developer
COINMARKETCAL_API_KEY = os.getenv("COINMARKETCAL_API_KEY", "")
# Discord webhook (viz discord_client.py) - notifikacia pri kazdom otvoreni
# pozicie. Prazdne = vypnute (ticho no-op). Vytvorenie: Discord kanal ->
# Channel Settings -> Integrations -> Webhooks -> New Webhook -> skopirovat URL.
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# Vsetkych 9 tickerov ma teraz marketaux_query a vola sa 1x za KAZDY dokonceny
# cyklus - pri realnych produkcnych intervaloch to vychadzalo ~147 volani/den
# (overene naozivo 2026-08-08), teda uz nad volnym limitom aj bez HYPE/SKHYNIX
# (~114/den). Spravy sa realne nemenia kazdu hodinu, preto cache namiesto
# volania pri kazdom cykle (viz marketaux_client.py) - 3h je kompromis medzi
# cerstvostou a poctom volani.
MARKETAUX_CACHE_HOURS = _float("MARKETAUX_CACHE_HOURS", 3)
# Clanky starsie nez tolko hodin sa DO PROMPTU vobec nedaju (2026-08-19, na
# ziadost pouzivatela - live analyza ukazala, ze pre ADA/ZEC/NIGHT bol aj
# NAJNOVSI dostupny clanok 65-70+ dni stary, teda ziadny pocet-limit by to
# nevyriesil). 25h = tesne nad POSITION_MAX_HOURS (24h, najhorsi realny
# pripad - otvorena pozicia, ziadny trigger) a nad najdlhsim beznym cyklom
# (WEEKEND_INTERVAL_HOURS default 6h) - vsetko starsie uz bolo (alebo mohlo
# byt) videne v predchadzajucom cykle, nie je to "nova" informacia.
MARKETAUX_MAX_ARTICLE_AGE_HOURS = _float("MARKETAUX_MAX_ARTICLE_AGE_HOURS", 25)

# DB
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///trades.db")

# Trading / risk - skutocne zdielane pre VSETKY assety (nie per-ticker).
DRY_RUN = _bool("DRY_RUN", "true")
# POZOR (2026-08-16): pouziva uz LEN funding_tracker.poll_new (1 API volanie
# PER symbol - zdielanie tesnejsieho WATCH_INTERVAL_MINUTES by ho 10x zatazilo
# zbytocne, funding sa akumuluje priebezne a nepotrebuje rovnaku reaktivitu).
# position_monitor.check_open_trades uz od tejto zmeny zdiela WATCH_INTERVAL_MINUTES
# nizsie namiesto tejto premennej (viz main.py) - detekcia zatvorenia pozicie
# (a teda presny PnL/notifikacia/post-close review) predtym meskala az 10 min
# za skutocnym zatvorenim, kriticke pri prudkom pohybe.
MONITOR_INTERVAL_MINUTES = _float("MONITOR_INTERVAL_MINUTES", 10)
# Tesnejsi zdielany interval pre watch_monitor.py AJ position_monitor.py (viz
# main.py) - oba su lacne polly (1 bulk API volanie + DB, ziadne Claude/web_search
# v hlavnej ceste, eskaluje sa len pri realnej udalosti), takze castejsi tik nic
# nestoji navyse. Financna ochrana pozicie na tomto intervale NIKDY nezavisela
# (Strike vykonava SL/TP/likvidaciu sam v realnom case ako bracket objednavku) -
# ide tu o REAKTIVITU (detekcia watch-ceny / zatvorenia pozicie), nie o samotnu
# ochranu.
WATCH_INTERVAL_MINUTES = _float("WATCH_INTERVAL_MINUTES", 1)
# "Hot watch" okno (2026-08-16, na ziadost pouzivatela) - po zatvoreni pozicie
# (TP/SL/likvidacia/timeout, viz position_monitor.py) sa DANY symbol docasne
# sleduje omnoho castejsie (POST_CLOSE_HOT_WATCH_SECONDS) nez bezny
# WATCH_INTERVAL_MINUTES, po dobu POST_CLOSE_HOT_WATCH_MINUTES - zachyti
# rychlo pokracujuci pohyb/odraz ("mela") hned po zatvoreni, bez potreby drzat
# CELY system natrvalo na tesnom intervale. Ked nie je ziaden symbol "hot"
# (bezny stav), tento tik nestoji vobec nic (viz watch_monitor.check_hot_watch_triggers).
POST_CLOSE_HOT_WATCH_SECONDS = _int("POST_CLOSE_HOT_WATCH_SECONDS", 10)
POST_CLOSE_HOT_WATCH_MINUTES = _float("POST_CLOSE_HOT_WATCH_MINUTES", 5)
POSITION_MAX_HOURS = _float("POSITION_MAX_HOURS", 24)
# Bezpecnostna poistka pri zhluku makro udalosti (viz macro_calendar.py +
# watch_monitor._check_macro_events) - max. kolko mimoriadnych "vsetky assety"
# behov sa spusti za poslednu hodinu. "Pauza po poslednom" netreba samostatnu
# premennu - _is_due() uz prirodzene zablokuje dalsi bezny tik, kym neuplynie
# dany asset {TICKER}_TRADE_INTERVAL_HOURS/OFF_HOURS/WEEKEND.
MACRO_EVENT_MAX_TRIGGERS_PER_HOUR = _int("MACRO_EVENT_MAX_TRIGGERS_PER_HOUR", 3)
# Rovnaka bezpecnostna poistka, ale pre cenovy watch mechanizmus
# (watch_price/watch_direction - viz claude_analyst.py) - PER ASSET (na
# rozdiel od MACRO_EVENT_MAX_TRIGGERS_PER_HOUR vyssie, ktory je jeden
# zdielany rozpocet naprieč vsetkymi assetmi naraz, kedze makro udalosti su
# casto "vsetky assety" burst - cenovy watch je vzdy nezavisly per-symbol,
# preto kazdy asset ma VLASTNY rozpocet). Bez tejto poistky by sa mohol
# watch-trigger opakovat neobmedzene casto, ak by kazdy dalsi mimoriadny
# cyklus znova nastavil (aj mierne inu) blizku watch uroven.
WATCH_TRIGGER_MAX_PER_HOUR = _int("WATCH_TRIGGER_MAX_PER_HOUR", 5)
# Pridane 2026-08-15 - rozsirenie watch mechanizmu z direction="none" aj na
# direction=long/short, ktore risk_manager zamietol CISTO kvoli confidence
# (viz claude_analyst.py confidence_threshold_note + DECISION_TOOL). Ak
# Claude navrhne smer a jeho confidence padne do pasma
# [{TICKER}_MIN_CONFIDENCE - WATCH_CONFIDENCE_MARGIN, {TICKER}_MIN_CONFIDENCE),
# dostane v user prompte tento pasmo cislicami a MA sa VZDY explicitne
# vyjadrit, pri akej cene by (cisto technicky, nie plynutim casu) confidence
# prekrocila prah - tu cenu zapise do (uz existujucich) watch_price/
# watch_direction, ktore watch_monitor.py uz beztak sleduje bez ohladu na to,
# aky direction mal posledny CycleLog. Ziadna nova trieda rizika: spustenie
# watch-u vedie k rovnakemu mimoriadnemu cyklu s COMPLETNE cerstvou analyzou
# (nie mechanickemu vykonaniu povodneho navrhu), chranenemu tym istym
# WATCH_TRIGGER_MAX_PER_HOUR stropom vyssie. Zdielane (nie per-ticker) -
# jednoduchy relativny offset od min_confidence, ktory zostava spravny aj ak
# sa {TICKER}_MIN_CONFIDENCE niekedy zmeni.
WATCH_CONFIDENCE_MARGIN = _float("WATCH_CONFIDENCE_MARGIN", 5)

# Pridane 2026-08-15 - position health check (uz otvorena pozicia) je teraz
# defaultne MECHANICKY (bez Claude volania, zdarma) - viz trade_cycle.
# _mechanical_health_escalation. Plny Claude cyklus (s web_search, per-asset
# efortom) sa spusti LEN ked nerealizovana strata dosiahne tuto CAST
# konfigurovanej SL vzdialenosti danneho assetu (napr. 0.6 * sl_pct), alebo
# ked sa TA trend obrati proti pozicii. Dovod: predtym kazdy hodinovy health
# check bezal ako plny cyklus nezavisle od toho, ci sa vobec nieco zmenilo -
# pri viachodinovom drzani pozicie to bol hlavny naklad, nie samotne otvorenie.
HEALTH_CHECK_LOSS_TRIGGER_FRACTION = _float("HEALTH_CHECK_LOSS_TRIGGER_FRACTION", 0.6)
# Cooldown medzi dvoma PLNYMI (platenymi) eskalaciami PRE TU ISTU otvorenu
# poziciu (2026-08-17, produkcny nalez pouzivatela - ADA pozicia eskalovala 4x
# za sebou kazdu hodinu, lebo cena 5h ostala tesne pod SL bez toho, aby sa
# odrazila alebo ho zasiahla - kazdy trigger bol legitimny/spravny, len sa
# opakoval bez casoveho odstupu). Ak escalation trigger nastane skor nez
# tolkoto hodin od poslednej PLNEJ eskalacie TEJTO pozicie, zapise sa len
# mechanicky (bezplatny) zaznam s poznamkou o preskoceni - viz trade_cycle.
# _run_position_health_check. NEOVPLYVNUJE skutocnu ochranu (SL/TP na burze
# bezi nezavisle), len frekvenciu placenych Claude "opinion" volani.
HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS = _float("HEALTH_CHECK_ESCALATION_COOLDOWN_HOURS", 3)
# 2026-08-21 (na ziadost pouzivatela, po NAS100 SL incidente - Claude odporucil
# consider_closing s close_confidence=50 hodinu pred SL, nikdy sa nezasiahlo) -
# ked position health check vrati recommendation="consider_closing" A
# close_confidence >= tento prah, bot uz NIE JE len "opinion pre cloveka"
# (povodne spravanie), ale poziciu SAM zatvori (market order, viz
# trade_cycle._run_position_health_check) - JEDNORAZOVO, bez cakania na
# potvrdenie druhym cyklom (confidence cislo uz JE kalibrovana miera istoty).
# Plati pre VSETKY tickery rovnako (na ziadost pouzivatela).
AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD = _float("AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD", 50)
# Preventivna poistka proti scale-mismatch dat (2026-08-09, po SKHYNIX
# incidente - viz market_data.get_price_history/assets.py yf_volume_only) -
# porovnava TA last_price (z akehokolvek zdroja - vlastne price_bars alebo
# fallback) voci Strike live_price. Existujuci SL/TP safety cap uz chranil
# SKUTOCNE OBCHODY pred zlou skalou (klampovanie na 0.1x-5x cieloveho %), ale
# watch_price/watch_direction ZIADNU takuto ochranu nemali (preto watch
# "below 1400000" pri live ~1020 triggeroval na kazdom ticku) - tato kontrola
# zachyti problem HNED pri zbere dat, este PRED Claude volanim (usetri aj
# naklad), nezavisle od toho, KTORY konkretny zdroj/pricinu ma na svedomi -
# funguje aj pre buduce, este neexistujuce zdroje. NIE per-asset (je to fakt
# o datovej integrite, nie risk preferencia).
# 3.0 (nie 2.0 - povodny navrh) - overene naozivo na realnom 10.10.2025 krypto
# flash-crashi (likvidacna kaskada): ADA mala v jednej hodine skutocny knot
# (intra-hour low) 2.62x pod jej otvaracou cenou (Binance data), co by pri 2.0x
# prahu bol falosny poplach na genuinnom (aj ked extremnom) trhovom pohybe, nie
# na chybe dat. 3.0x dava rezervu aj nad tento realny extrem, pricom stale
# spolahlivo chyta vsetky doteraz najdene skutocne chyby (SKHYNIX 1373x,
# GOLD/GLD 10.9x - obe daleko nad 3x).
TA_LIVE_PRICE_MISMATCH_RATIO = _float("TA_LIVE_PRICE_MISMATCH_RATIO", 3.0)

# POZOR (2026-08-08): {TICKER}_LEVERAGE nizsie pri kazdom tickeri uz NIE JE
# skutocna pouzita paka a UZ NEOVPLYVNUJE ziaden realny obchod - zmena tejto
# hodnoty na Railway sa na zivych pozicach vobec neprejavi. Ostava zapisana
# LEN ako historicky/referencny udaj (dashboard "Konfiguracia" karta,
# retrospective.py hypoteticky PnL prepocet pre stare zaznamy spred tejto
# zmeny - viz asset["leverage"] pouzitie tam). Skutocna paka sa DOPOCITAVA z
# (Claudom navrhnutej) SL vzdialenosti tak, aby vzdialenost do teoretickej
# likvidacnej ceny bola PRESNE {TICKER}_LIQUIDATION_CUSHION_MULTIPLE-nasobkom
# SL vzdialenosti (napr. 1.5 = likvidacia je o 50% dalej nez SL) - viz
# risk_manager._leverage_from_cushion. Cielom (explicitne pouzivatelom
# 2026-08-08) je MAXIMALIZOVAT expoziciu pri zachovani bezpecneho odstupu od
# likvidacie, nie zachovat povodnu konzervativnu fixnu paku - vzdy orezane
# zhora na skutocny Strike-om povoleny strop pre danu marzu/tier (nikdy
# nepozadovat viac, nez burza vobec dovoli), nikdy dolu na povodnu {TICKER}_LEVERAGE.
# Ak chces skutocne zmenit realnu paku, uprav {TICKER}_LIQUIDATION_CUSHION_MULTIPLE
# (uzsi cushion = vyssia paka, sirsi = nizsia), NIE {TICKER}_LEVERAGE.
#
# LIQUIDATION_CUSHION_MULTIPLE nizsie (bezpredponova) je LEN interny
# fallback-cascade default (viz blok MIN_CONFIDENCE/MARGIN_USD/... nizsie) -
# VSETKYCH 12 tickerov uz ma svoju vlastnu explicitnu {TICKER}_LIQUIDATION_CUSHION_MULTIPLE,
# takze tento zdielany default sa v skutocnosti UZ NIKDY nepouzije. Rovnaky
# status ako MIN_CONFIDENCE a pod. nizsie - nenastavuj priamo na Railway
# (2026-08-16 zistene, ze tam bola nastavena bez akehokolvek efektu - odstranene).
LIQUIDATION_CUSHION_MULTIPLE = _float("LIQUIDATION_CUSHION_MULTIPLE", 1.5)

# --- NIZSIE (MIN_CONFIDENCE/MARGIN_USD/LEVERAGE/LIQUIDATION_CUSHION_MULTIPLE/
# DEFAULT_SL_PCT/DEFAULT_TP_PCT/TRADE_INTERVAL_HOURS/OFF_HOURS_INTERVAL_HOURS/
# WEEKEND_INTERVAL_HOURS) su od 2026-07-31 LEN interne fallback-cascade
# konstanty pre vypocet per-ticker defaultov nizsie (NAS100_MIN_CONFIDENCE a
# pod.) - VSETKYCH 12 tickerov uz ma svoju vlastnu explicitne nastavenu
# premennu na Railway, takze tieto uz NEOVPLYVNUJU ziadne skutocne
# rozhodnutie za behu. Nenastavuj ich uz priamo na Railway - uprav rovno
# konkretny {TICKER}_* ekvivalent nizsie.
MIN_CONFIDENCE = _int("MIN_CONFIDENCE", 65)
MARGIN_USD = _float("MARGIN_USD", 100)
LEVERAGE = _int("LEVERAGE", 40)
DEFAULT_SL_PCT = _float("DEFAULT_SL_PCT", 0.4)
DEFAULT_TP_PCT = _float("DEFAULT_TP_PCT", 0.6)
TRADE_INTERVAL_HOURS = _float("TRADE_INTERVAL_HOURS", 4)

# --- Devat tickerov celkovo: NAS100 (index), NVDA (akcia, POZASTAVENE),
# ADA (krypto), GOLD (komodita), WTI (ropa), NIGHT (krypto, Midnight/Cardano),
# BTC (krypto, Bitcoin), HYPE (krypto, Hyperliquid), SKHYNIX (akcia, Korea
# Exchange, SK Hynix) - posledne dve pridane 2026-08-07 ako najmenej
# korelovane assety z korelacnej analyzy celej Strike ponuky (diverzifikacia).
# Vsetky bezia v tom istom cykle a zdielaju cross-market/session makro fetch
# (viz assets.py, trade_cycle.run_all_cycles), ale kazdy ma uplne nezavisly
# risk/poziciu/rozhodnutie/frekvenciu - kazdy ma VLASTNU sadu 8 premennych
# ({TICKER}_MIN_CONFIDENCE/MARGIN_USD/LEVERAGE/SL_PCT/TP_PCT/
# TRADE_INTERVAL_HOURS/OFF_HOURS_INTERVAL_HOURS/WEEKEND_INTERVAL_HOURS),
# zoskupenu nizsie ticker-po-tickeri (2026-07-31 zjednotene - predtym mal
# NAS100 bezpredponove nazvy a ADA/NIGHT nemali off_hours/weekend vobec).
#
# POZOR (2026-08-08): {TICKER}_LEVERAGE uz NEOVPLYVNUJE skutocny position
# sizing - paka sa teraz DOPOCITAVA z LIQUIDATION_CUSHION_MULTIPLE (vyssie) a
# SL vzdialenosti (viz risk_manager._leverage_from_cushion). Tieto premenne
# ostavaju zapisane len ako referencny/historicky udaj (dashboard config karta,
# retrospective.py hypotetiske PnL vypocty pre stare zaznamy bez ulozenej
# skutocnej paky) - zmena hodnoty uz nema ziaden vplyv na skutocne obchody.
#
# GOLD je zamerne pridany ako protivietor k prevazne risk-on smerovaniu
# NAS100/NVDA/ADA (safe-haven, opacna VIX polarita). WTI pridany 2026-07-31 ako
# vyraznejsie odlisny ticker (ropa ma iny driver - OPEC+/geopolitika/dopyt, NIE
# safe-haven ako zlato). NIGHT pridany v tom istom kroku - vyrazne
# rizikovejsi/volatilnejsi mladý token (Wanchain bridge hack 2026-07-20).
# NVDA POZASTAVENE od 2026-07-31 (nahradzame ho WTI/NIGHT, cost-optimalizacia -
# 5 tickerov denne by bolo drahe zbiehat a zatial si na seba nezarobili).
# Historicke cycle_logs/trades ostavaju v DB/monitor-web, len sa nezapocitavaju
# do noveho web_search/Claude nakladu - viz trade_cycle._mark_disabled_assets.
#
# off_hours/weekend interval: mimo trading hours a cez vikend podkladovy trh
# (akcia/futures) realne stoji alebo je velmi ticho (NVDA sa cez vikend vobec
# neobchoduje), takze hodinova analyza tych istych zastaralych dat je zbytocny
# naklad. Pre 24/7 krypto (ADA/NIGHT) su vsetky tri hodnoty zvycajne rovnake
# (ziadne skutocne "off hours" preň neexistuju), ale mechanizmus je jednotny
# pre vsetkych 9 tickerov - dovoluje to napr. neskor predlzit vikendovy
# interval aj pre ADA/NIGHT bez zmeny kodu.
#
# TRADING_HOURS_START_UTC/END_UTC (13-21 = NYSE cash session 9:30-16:00 ET v
# oboch DST stavoch) je zdielany DEFAULT pre vsetky assety na americkych/
# 24-7 trhoch. SKHYNIX (Korea Exchange, viz nizsie) je JEDINY asset s inou
# skutocnou trhovou strukturou (iny kontinent/timezone), preto ma od
# 2026-08-07 VLASTNY per-asset override (viz assets.py trading_hours_start_utc/
# end_utc a trade_cycle._required_interval_hours) - bez toho by sa off_hours
# logika pre SKHYNIX obratila naopak (tiche hodiny NYSE = live KRX seansa).
TRADING_HOURS_START_UTC = _int("TRADING_HOURS_START_UTC", 13)
TRADING_HOURS_END_UTC = _int("TRADING_HOURS_END_UTC", 21)
# KRX regularna seansa je 09:00-15:30 KST (UTC+9, ziadny DST) = 00:00-06:30 UTC,
# zaokruhlene na cele hodiny rovnako hrubo ako NYSE default vyssie.
SKHYNIX_TRADING_HOURS_START_UTC = _int("SKHYNIX_TRADING_HOURS_START_UTC", 0)
SKHYNIX_TRADING_HOURS_END_UTC = _int("SKHYNIX_TRADING_HOURS_END_UTC", 7)

# Interne fallback-cascade konstanty pre {TICKER}_OFF_HOURS_INTERVAL_HOURS/
# {TICKER}_WEEKEND_INTERVAL_HOURS nizsie (rovnaky status ako MIN_CONFIDENCE a
# pod. vyssie - vsetkych 9 tickerov uz ma svoju vlastnu explicitnu premennu,
# tieto uz nic za behu neovplyvnuju, nenastavuj ich priamo na Railway).
OFF_HOURS_INTERVAL_HOURS = _float("OFF_HOURS_INTERVAL_HOURS", 2)
WEEKEND_INTERVAL_HOURS = _float("WEEKEND_INTERVAL_HOURS", 6)

ENABLE_NVDA = _bool("ENABLE_NVDA", "false")
ENABLE_ADA = _bool("ENABLE_ADA", "true")
ENABLE_GOLD = _bool("ENABLE_GOLD", "true")
ENABLE_WTI = _bool("ENABLE_WTI", "true")
ENABLE_NIGHT = _bool("ENABLE_NIGHT", "true")
ENABLE_BTC = _bool("ENABLE_BTC", "true")
ENABLE_HYPE = _bool("ENABLE_HYPE", "true")
ENABLE_SKHYNIX = _bool("ENABLE_SKHYNIX", "true")
# AAOI/MINIMAX pridane 2026-08-14, default FALSE (rovnaky "pozastaveny" vzor
# ako NVDA) - LEN zbieraju cenovu historiu cez price_poller.py, kym niekto
# rucne nezapne (viz per-asset sekcie nizsie pre kontext).
ENABLE_AAOI = _bool("ENABLE_AAOI", "false")
ENABLE_MINIMAX = _bool("ENABLE_MINIMAX", "false")
# ZEC pridany 2026-08-15, rovnaky "aktivny hned" vzor ako WTI/NIGHT/HYPE/SKHYNIX
# (na rozdiel od AAOI/MINIMAX, ktore vedome zacali len ako zber historie).
ENABLE_ZEC = _bool("ENABLE_ZEC", "true")
# GOOGL pridany 2026-08-18 (na ziadost pouzivatela, po korelacnej analyze -
# |korelacia| <= 0.5 voci vsetkym ostatnym tickerom v portfoliu) - rovnaky
# "aktivny hned" vzor ako ZEC/WTI/NIGHT/HYPE/SKHYNIX.
ENABLE_GOOGL = _bool("ENABLE_GOOGL", "true")
# UNITREE pridany 2026-08-19, default FALSE - rovnaky "len zbiera historiu"
# vzor ako AAOI/MINIMAX vyssie (viz UNITREE sekcia nizsie pre kontext).
ENABLE_UNITREE = _bool("ENABLE_UNITREE", "false")

# Presny symbol/asset identifikator zisti cez strike_client.get_markets() - toto
# su len predpoklady podla existujuceho NAS100-USD pomenovacieho vzoru, okrem
# WTI-USD/NIGHT-USD/HYPE-USD/SKHYNIX-USD ktore su priamo overene naozivo v
# /v2/markets (2026-07-31, resp. HYPE/SKHYNIX 2026-08-07).
STRIKE_NVDA_SYMBOL = os.getenv("STRIKE_NVDA_SYMBOL", "NVDA-USD")
STRIKE_ADA_SYMBOL = os.getenv("STRIKE_ADA_SYMBOL", "ADA-USD")
STRIKE_GOLD_SYMBOL = os.getenv("STRIKE_GOLD_SYMBOL", "XAU-USD")
STRIKE_WTI_SYMBOL = os.getenv("STRIKE_WTI_SYMBOL", "WTI-USD")
STRIKE_NIGHT_SYMBOL = os.getenv("STRIKE_NIGHT_SYMBOL", "NIGHT-USD")
STRIKE_BTC_SYMBOL = os.getenv("STRIKE_BTC_SYMBOL", "BTC-USD")
STRIKE_HYPE_SYMBOL = os.getenv("STRIKE_HYPE_SYMBOL", "HYPE-USD")
STRIKE_SKHYNIX_SYMBOL = os.getenv("STRIKE_SKHYNIX_SYMBOL", "SKHYNIX-USD")
# ZEC-USD overene naozivo v /v2/markets (2026-08-15).
STRIKE_ZEC_SYMBOL = os.getenv("STRIKE_ZEC_SYMBOL", "ZEC-USD")

# ============================== NAS100 ==============================
# Prve/povodne assety pred multi-asset refaktorom - tieto premenne su nove
# (2026-07-31), predtym NAS100 pouzival bezpredponove MIN_CONFIDENCE/
# MARGIN_USD/LEVERAGE/DEFAULT_SL_PCT/DEFAULT_TP_PCT/TRADE_INTERVAL_HOURS
# priamo. Defaulty nizsie z nich cascade-uju, takze spravanie ostava presne
# rovnake, kym nekto explicitne nastavi NAS100_* na Railway.
NAS100_MIN_CONFIDENCE = _int("NAS100_MIN_CONFIDENCE", MIN_CONFIDENCE)
NAS100_MARGIN_USD = _float("NAS100_MARGIN_USD", MARGIN_USD)
NAS100_LEVERAGE = _int("NAS100_LEVERAGE", LEVERAGE)
NAS100_LIQUIDATION_CUSHION_MULTIPLE = _float("NAS100_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
NAS100_SL_PCT = _float("NAS100_SL_PCT", DEFAULT_SL_PCT)
NAS100_TP_PCT = _float("NAS100_TP_PCT", DEFAULT_TP_PCT)
NAS100_TRADE_INTERVAL_HOURS = _float("NAS100_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
NAS100_OFF_HOURS_INTERVAL_HOURS = _float("NAS100_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
NAS100_WEEKEND_INTERVAL_HOURS = _float("NAS100_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== NVDA (POZASTAVENE) ==============================
NVDA_MIN_CONFIDENCE = _int("NVDA_MIN_CONFIDENCE", MIN_CONFIDENCE)
NVDA_MARGIN_USD = _float("NVDA_MARGIN_USD", MARGIN_USD)
# Nizsia paka nez NAS100 (40x) - vyssia vnutrodenna volatilita jednotlivej
# akcie nez indexu, takze rovnaka paka by pri bezneho pohybe znamenala vyssie
# riziko likvidacie.
NVDA_LEVERAGE = _int("NVDA_LEVERAGE", 10)
NVDA_LIQUIDATION_CUSHION_MULTIPLE = _float("NVDA_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Sirsie SL/TP % nez NAS100 (0.4/0.6), rovnaky risk:reward pomer 1:1.5.
NVDA_SL_PCT = _float("NVDA_SL_PCT", 1.5)
NVDA_TP_PCT = _float("NVDA_TP_PCT", 2.25)
NVDA_TRADE_INTERVAL_HOURS = _float("NVDA_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
NVDA_OFF_HOURS_INTERVAL_HOURS = _float("NVDA_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
NVDA_WEEKEND_INTERVAL_HOURS = _float("NVDA_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== ADA ==============================
ADA_MIN_CONFIDENCE = _int("ADA_MIN_CONFIDENCE", MIN_CONFIDENCE)
# Znizene z MARGIN_USD (100) na 50 - viz NIGHT/HYPE/SKHYNIX rovnaky dovod
# nizsie (viac tickerov teraz zdiela jednu penazenku bez koordinacie, viz
# trade_cycle.py preflight kontrola zostatku pridana 2026-08-08).
ADA_MARGIN_USD = _float("ADA_MARGIN_USD", 50)
# Najnizsia paka spomedzi povodnej trojice - najvyssia volatilita.
ADA_LEVERAGE = _int("ADA_LEVERAGE", 6)
ADA_LIQUIDATION_CUSHION_MULTIPLE = _float("ADA_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
ADA_SL_PCT = _float("ADA_SL_PCT", 3.5)
ADA_TP_PCT = _float("ADA_TP_PCT", 5.25)
# 24/7 krypto - vsetky tri intervaly su defaultne rovnake (1h), ale nezavisle
# nastavitelne (napr. neskorsie predlzenie vikendoveho intervalu).
ADA_TRADE_INTERVAL_HOURS = _float("ADA_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
ADA_OFF_HOURS_INTERVAL_HOURS = _float("ADA_OFF_HOURS_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
ADA_WEEKEND_INTERVAL_HOURS = _float("ADA_WEEKEND_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)

# ============================== GOLD ==============================
# Zamerne pridany ako protivietor k prevazne risk-on smerovaniu NAS100/NVDA/ADA
# (safe-haven, opacna VIX polarita - viz claude_analyst._COMMODITY_MACRO_RULES).
GOLD_MIN_CONFIDENCE = _int("GOLD_MIN_CONFIDENCE", MIN_CONFIDENCE)
GOLD_MARGIN_USD = _float("GOLD_MARGIN_USD", MARGIN_USD)
# Menej volatilne nez NVDA/ADA, volatilnejsie nez index -> paka medzi NAS100 a NVDA.
GOLD_LEVERAGE = _int("GOLD_LEVERAGE", 20)
GOLD_LIQUIDATION_CUSHION_MULTIPLE = _float("GOLD_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
GOLD_SL_PCT = _float("GOLD_SL_PCT", 0.8)
GOLD_TP_PCT = _float("GOLD_TP_PCT", 1.2)
GOLD_TRADE_INTERVAL_HOURS = _float("GOLD_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
GOLD_OFF_HOURS_INTERVAL_HOURS = _float("GOLD_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
GOLD_WEEKEND_INTERVAL_HOURS = _float("GOLD_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== WTI ==============================
# Pridany 2026-07-31 - vyraznejsie odlisny ticker od NAS100/ADA/GOLD (ropa ma
# iny driver: OPEC+/geopolitika/dopyt, NIE safe-haven ako zlato - viz
# claude_analyst._ENERGY_MACRO_RULES). Vsetky hodnoty su pociatocny odhad, nie
# empiricky backtestovane - prehodnotit po zozbierani realnych dat.
WTI_MIN_CONFIDENCE = _int("WTI_MIN_CONFIDENCE", MIN_CONFIDENCE)
WTI_MARGIN_USD = _float("WTI_MARGIN_USD", MARGIN_USD)
# Podobne ako GOLD, o niecoo nizsie (ropa byva vnutrodenne volatilnejsia nez zlato).
WTI_LEVERAGE = _int("WTI_LEVERAGE", 15)
WTI_LIQUIDATION_CUSHION_MULTIPLE = _float("WTI_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
WTI_SL_PCT = _float("WTI_SL_PCT", 1.2)
WTI_TP_PCT = _float("WTI_TP_PCT", 1.8)
# Defaultne rovnake ako GOLD (dohodnute) - nezavisle prestavitelne.
WTI_TRADE_INTERVAL_HOURS = _float("WTI_TRADE_INTERVAL_HOURS", GOLD_TRADE_INTERVAL_HOURS)
WTI_OFF_HOURS_INTERVAL_HOURS = _float("WTI_OFF_HOURS_INTERVAL_HOURS", GOLD_OFF_HOURS_INTERVAL_HOURS)
WTI_WEEKEND_INTERVAL_HOURS = _float("WTI_WEEKEND_INTERVAL_HOURS", GOLD_WEEKEND_INTERVAL_HOURS)

# ============================== NIGHT ==============================
# Pridany 2026-07-31 - Midnight (Cardano privacy/zero-knowledge sidechain).
# Vyrazne rizikovejsi/volatilnejsi mladý, nizko-kapitalizovany token s
# cerstvym bezpecnostnym incidentom (Wanchain bridge hack 2026-07-20, ~97%
# rezerv mostu odcerpanych).
NIGHT_MIN_CONFIDENCE = _int("NIGHT_MIN_CONFIDENCE", MIN_CONFIDENCE)
NIGHT_MARGIN_USD = _float("NIGHT_MARGIN_USD", 50)
# MAX povolena paka na Strike pre tento symbol (margin_tiers strop, overene
# naozivo cez get_market('NIGHT-USD') 2026-07-31) - vedome zvolena aj napriek
# cerstvemu bezpecnostnemu incidentu.
NIGHT_LEVERAGE = _int("NIGHT_LEVERAGE", 10)
NIGHT_LIQUIDATION_CUSHION_MULTIPLE = _float("NIGHT_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Najsirsie SL/TP zo vsetkych tickerov - najvyssia ocakavana volatilita.
NIGHT_SL_PCT = _float("NIGHT_SL_PCT", 6.0)
NIGHT_TP_PCT = _float("NIGHT_TP_PCT", 9.0)
# Defaultne rovnake ako ADA (dohodnute) - obe 24/7 krypto.
NIGHT_TRADE_INTERVAL_HOURS = _float("NIGHT_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
NIGHT_OFF_HOURS_INTERVAL_HOURS = _float("NIGHT_OFF_HOURS_INTERVAL_HOURS", NIGHT_TRADE_INTERVAL_HOURS)
NIGHT_WEEKEND_INTERVAL_HOURS = _float("NIGHT_WEEKEND_INTERVAL_HOURS", NIGHT_TRADE_INTERVAL_HOURS)

# ============================== BTC ==============================
# Pridany 2026-08-06 - najlikvidnejsi/najsledovanejsi market na Strike (tesny
# spread, hlboky orderbook - viz diskusia s pouzivatelom), navyse uz existujucu
# infrastrukturu ciastocne zdiela (get_btc_proxy_snapshot uz BTC pouziva ako
# krypto-makro proxy pre ADA/NIGHT). SL/TP nizsie su pociatocny odhad (BTC ma
# citelne nizsiu volatilitu nez ADA/NIGHT, preto tesnejsie nez obe) - NIE
# empiricky backtestovane, prehodnotit po zozbierani realnych dat (rovnaky
# vzor ako WTI/NIGHT pri ich zavedeni).
BTC_MIN_CONFIDENCE = _int("BTC_MIN_CONFIDENCE", MIN_CONFIDENCE)
BTC_MARGIN_USD = _float("BTC_MARGIN_USD", MARGIN_USD)
# Strike default_leverage pre BTC-USD je 10 (margin_tiers strop az 100x pri
# nizkom notional, ale 10 je konzervativnejsi, konzistentny s ostatnymi).
BTC_LEVERAGE = _int("BTC_LEVERAGE", 10)
BTC_LIQUIDATION_CUSHION_MULTIPLE = _float("BTC_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
BTC_SL_PCT = _float("BTC_SL_PCT", 1.5)
BTC_TP_PCT = _float("BTC_TP_PCT", 2.25)
# Rovnake ako ADA/NIGHT (dohodnute) - vsetky tri 24/7 krypto.
BTC_TRADE_INTERVAL_HOURS = _float("BTC_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
BTC_OFF_HOURS_INTERVAL_HOURS = _float("BTC_OFF_HOURS_INTERVAL_HOURS", BTC_TRADE_INTERVAL_HOURS)
BTC_WEEKEND_INTERVAL_HOURS = _float("BTC_WEEKEND_INTERVAL_HOURS", BTC_TRADE_INTERVAL_HOURS)

# ============================== HYPE ==============================
# Pridany 2026-08-07 - Hyperliquid (perpetual-DEX vlastny token), identifikovany
# ako jeden z 3 najmenej korelovanych assetov naprieč celou ponukou Strike
# (korelacna analyza s pouzivatelom, ~180d denne vynosy: priemerna |korelacia|
# 0.09 voci vsetkym ostatnym vratane BTC/ETH) - genuinne diverzifikacny pridavok,
# nie len dalsi krypto-beta ticker. POZOR: HYPE NIE JE na Binance (HYPEUSDT ani
# HYPEUSDC neexistuju, overene naozivo 2026-08-07) ani na yfinance ("HYPE-USD"
# nevracia ziadne data) - viz coingecko_client.py + assets.py coingecko_id
# pre fallback/backfill OHLC zdroj namiesto zvycajneho yfinance. include_volume
# preto zamerne FALSE (rovnaky dovod ako WTI - ziaden overeny spolahlivy
# volume zdroj). SL/TP/leverage su pociatocny odhad medzi ADA a NIGHT
# (likvidnejsi/etablovanejsi nez NIGHT, ale stale jeden-narrative altcoin) -
# NIE empiricky backtestovane.
HYPE_MIN_CONFIDENCE = _int("HYPE_MIN_CONFIDENCE", MIN_CONFIDENCE)
HYPE_MARGIN_USD = _float("HYPE_MARGIN_USD", 50)
HYPE_LEVERAGE = _int("HYPE_LEVERAGE", 8)
HYPE_LIQUIDATION_CUSHION_MULTIPLE = _float("HYPE_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
HYPE_SL_PCT = _float("HYPE_SL_PCT", 3.5)
HYPE_TP_PCT = _float("HYPE_TP_PCT", 5.25)
# Rovnake ako ADA/NIGHT/BTC (dohodnute) - 24/7 krypto.
HYPE_TRADE_INTERVAL_HOURS = _float("HYPE_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
HYPE_OFF_HOURS_INTERVAL_HOURS = _float("HYPE_OFF_HOURS_INTERVAL_HOURS", HYPE_TRADE_INTERVAL_HOURS)
HYPE_WEEKEND_INTERVAL_HOURS = _float("HYPE_WEEKEND_INTERVAL_HOURS", HYPE_TRADE_INTERVAL_HOURS)

# ============================== SKHYNIX ==============================
# Pridany 2026-08-07 - SK Hynix (Korea Exchange, hlavny HBM dodavatel pre
# Nvidia AI GPU) - druhy z 3 najmenej korelovanych assetov (priemerna
# |korelacia| 0.13, vratane takmer nulovej korelacie s krypto majors aj
# NAS100/NVDA/MU/TSLA napriek tomu, ze je to tiez "chip" nazov - iny
# kontinent/timezone/trh). JEDINY asset obchodovany mimo US/24-7 struktury -
# viz SKHYNIX_TRADING_HOURS_START_UTC/END_UTC vyssie (KRX seansa, NIE zdielany
# NYSE default). SL/TP/leverage su pociatocny odhad rovnaky ako NVDA (podobny
# profil - jednotlivy vysoko-volatilny polovodicovy titul), NIE empiricky
# backtestovane.
SKHYNIX_MIN_CONFIDENCE = _int("SKHYNIX_MIN_CONFIDENCE", MIN_CONFIDENCE)
SKHYNIX_MARGIN_USD = _float("SKHYNIX_MARGIN_USD", 50)
SKHYNIX_LEVERAGE = _int("SKHYNIX_LEVERAGE", 10)
SKHYNIX_LIQUIDATION_CUSHION_MULTIPLE = _float("SKHYNIX_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
SKHYNIX_SL_PCT = _float("SKHYNIX_SL_PCT", 1.5)
SKHYNIX_TP_PCT = _float("SKHYNIX_TP_PCT", 2.25)
SKHYNIX_TRADE_INTERVAL_HOURS = _float("SKHYNIX_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
SKHYNIX_OFF_HOURS_INTERVAL_HOURS = _float("SKHYNIX_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
SKHYNIX_WEEKEND_INTERVAL_HOURS = _float("SKHYNIX_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== AAOI (NEAKTIVNE - zbiera historiu) ==============================
# Pridany 2026-08-14 (Applied Optoelectronics, NASDAQ - opticke komponenty pre
# AI datacentra). ENABLE_AAOI default FALSE (rovnaky "pozastaveny" vzor ako
# NVDA) - zamerne LEN zbiera cenovu historiu cez price_poller.py (viz jeho
# zmena na ALL_ASSETS namiesto enabled_assets()), Claude analyza sa nespusta,
# kym niekto rucne ENABLE_AAOI=true nenastavi (viz spolocny ENABLE_* blok
# vyssie). Vsetko ostatne (ASSET_TEXT v claude_analyst.py, marketaux_query a
# pod.) je uz plne priprevene, aby zapnutie fungovalo bez dalsieho kodovania.
# Realny NASDAQ titul (nie synteticky pre-IPO tracker ako CXMT/SPCX/MINIMAX) -
# zdiela bezny TRADING_HOURS_START/END_UTC (rovnaky vzor ako NVDA).
STRIKE_AAOI_SYMBOL = os.getenv("STRIKE_AAOI_SYMBOL", "AAOI-USD")
AAOI_MIN_CONFIDENCE = _int("AAOI_MIN_CONFIDENCE", MIN_CONFIDENCE)
AAOI_MARGIN_USD = _float("AAOI_MARGIN_USD", 50)
AAOI_LEVERAGE = _int("AAOI_LEVERAGE", 10)
AAOI_LIQUIDATION_CUSHION_MULTIPLE = _float("AAOI_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Rovnaky profil ako NVDA/SKHYNIX (jednotlivy volatilny polovodicovy/opticky
# titul) - pociatocny odhad, NIE empiricky backtestovane (prehodnotit po
# zozbierani realnych dat, rovnaky vzor ako pri predoslych novych tickeroch).
AAOI_SL_PCT = _float("AAOI_SL_PCT", 1.9)
AAOI_TP_PCT = _float("AAOI_TP_PCT", 2.85)
AAOI_TRADE_INTERVAL_HOURS = _float("AAOI_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
AAOI_OFF_HOURS_INTERVAL_HOURS = _float("AAOI_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
AAOI_WEEKEND_INTERVAL_HOURS = _float("AAOI_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== MINIMAX (NEAKTIVNE - zbiera historiu) ==============================
# Pridany 2026-08-14 (MiniMax Group - sukromna/pre-IPO cinska AI firma,
# synteticky Strike tracker rovnakeho typu ako CXMT/SPCX). ENABLE_MINIMAX
# default FALSE z rovnakeho dovodu ako AAOI vyssie (viz spolocny ENABLE_*
# blok vyssie) - LEN zbiera historiu. NEMA ziadny realny burzovy trh (nie je
# verejne obchodovana), takze presne "trading hours" NEPOZNAME - ale to NIE
# JE dovod traktovat ju ako 24/7 krypto (2026-08-14, oprava po spatnej
# vazbe - povodny navrh hodinoveho behu cez cely vikend bol zly default pre
# neco, co krypto nie je). Namiesto toho pouziva ROVNAKY vzor ako AAOI/WTI/
# GOLD nizsie/vyssie - zdielane TRADING_HOURS_START/END_UTC ako pragmaticka
# aproximacia (rovnaky pristup, aky uz ma WTI/GOLD, kedze ani tie doslovne
# nekopiruju NYSE cash session, len ju pouzivaju ako rozumny default) a
# SKUTOCNE znizeny off_hours/weekend interval (nie rovnaky ako trade_interval).
STRIKE_MINIMAX_SYMBOL = os.getenv("STRIKE_MINIMAX_SYMBOL", "MINIMAX-USD")
MINIMAX_MIN_CONFIDENCE = _int("MINIMAX_MIN_CONFIDENCE", MIN_CONFIDENCE)
MINIMAX_MARGIN_USD = _float("MINIMAX_MARGIN_USD", 50)
MINIMAX_LEVERAGE = _int("MINIMAX_LEVERAGE", 10)
MINIMAX_LIQUIDATION_CUSHION_MULTIPLE = _float("MINIMAX_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Povodne (2026-08-14) najsirsi konzervativny odhad (6.0/9.0, rovnako ako
# NIGHT) kvoli uplnej absencii cenovej historie. Prepocitane 2026-08-19 (na
# ziadost pouzivatela, po SKHYNIX incidente kde sa presny opacny problem -
# SL prilis TESNY voci realnej volatilite - prejavil na zivych obchodoch)
# rovnakou metodou, teraz uz z 5 dni realnych PriceBar dat z vlastneho Strike
# pollera: hodinovy ATR14 = 1.386%, 90.percentil hodinoveho TR% = 2.403% ->
# 2.34x ATR (rovnaky pomer ako pri NVDA/GOOGL) = SL 3.24%. Povodne 6.0% bolo
# teda az 1.85x SIRSIE nez treba - opacny extrem od SKHYNIX, nie nebezpecny,
# len zbytocne konzervativny. Zaokruhlene na 3.2/4.8 (1:1.5 pomer). Tato
# ATR-based metodika (nie kopirovanie z "podobneho" tickera) sa teraz pouziva
# pri KAZDOM novom tickeri, akonahle ma dost vlastnej cenovej historie.
MINIMAX_SL_PCT = _float("MINIMAX_SL_PCT", 3.2)
MINIMAX_TP_PCT = _float("MINIMAX_TP_PCT", 4.8)
MINIMAX_TRADE_INTERVAL_HOURS = _float("MINIMAX_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
MINIMAX_OFF_HOURS_INTERVAL_HOURS = _float("MINIMAX_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
MINIMAX_WEEKEND_INTERVAL_HOURS = _float("MINIMAX_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== ZEC ==============================
# Pridany 2026-08-15 na ziadost pouzivatela - Zcash (krypto, opt-in "shielded"
# privacy transakcie cez zk-SNARKy). Rizikovy profil VEDOME nastaveny rovnako
# ako ADA (SL/TP/leverage/intervaly su identicke) - na rozdiel od NIGHT/HYPE
# NEIDE o pociatocny odhad kvoli chybajucim datam, ale o priamy pozadavok
# "podobne ako ADA". Vlastny Claude system prompt (macro_rules/news_focus) v
# claude_analyst.py je ale UPLNE samostatny - odlisne fundamenty (established
# privacy coin od 2016 vs. Cardano L1 smart-contract platforma) - viz tam.
ZEC_MIN_CONFIDENCE = _int("ZEC_MIN_CONFIDENCE", MIN_CONFIDENCE)
ZEC_MARGIN_USD = _float("ZEC_MARGIN_USD", 50)
ZEC_LEVERAGE = _int("ZEC_LEVERAGE", 6)
ZEC_LIQUIDATION_CUSHION_MULTIPLE = _float("ZEC_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
ZEC_SL_PCT = _float("ZEC_SL_PCT", 3.5)
ZEC_TP_PCT = _float("ZEC_TP_PCT", 5.25)
# 24/7 krypto - vsetky tri intervaly defaultne rovnake ako ADA.
ZEC_TRADE_INTERVAL_HOURS = _float("ZEC_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
ZEC_OFF_HOURS_INTERVAL_HOURS = _float("ZEC_OFF_HOURS_INTERVAL_HOURS", ZEC_TRADE_INTERVAL_HOURS)
ZEC_WEEKEND_INTERVAL_HOURS = _float("ZEC_WEEKEND_INTERVAL_HOURS", ZEC_TRADE_INTERVAL_HOURS)

# ============================== GOOGL (Alphabet) ==============================
# Pridany 2026-08-18 na ziadost pouzivatela - Strike pridal GOOGL-USD (Alphabet
# Class A) do /v2/markets. LIVE od zaciatku (rovnaky "aktivny hned" vzor ako
# ZEC/WTI/NIGHT/HYPE/SKHYNIX, NIE "len zbiera historiu" ako AAOI/MINIMAX) -
# korelacna analyza (Prehlad tab) ukazala |korelacia| <= 0.5 voci vsetkym
# ostatnym tickerom v portfoliu, dobry diverzifikacny kandidat. Realny NASDAQ
# titul (nie synteticky tracker) - zdiela bezny TRADING_HOURS_START/END_UTC
# (rovnaky vzor ako NVDA/AAOI).
STRIKE_GOOGL_SYMBOL = os.getenv("STRIKE_GOOGL_SYMBOL", "GOOGL-USD")
GOOGL_MIN_CONFIDENCE = _int("GOOGL_MIN_CONFIDENCE", MIN_CONFIDENCE)
GOOGL_MARGIN_USD = _float("GOOGL_MARGIN_USD", 100)
GOOGL_LEVERAGE = _int("GOOGL_LEVERAGE", 10)  # DEAD - viz risk_manager._leverage_from_cushion, skutocna paka sa odvodzuje z cushion multiple nizsie
GOOGL_LIQUIDATION_CUSHION_MULTIPLE = _float("GOOGL_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Hodinovy ATR (30d, yfinance) = 0.485% z ceny - kalibrovane rovnakym pomerom
# SL_PCT/ATR (~2.34x) ako uz naladeny NVDA (po jeho SL/TP re-evaluacii),
# NIE NAS100 (index, nizsia vola) ani AAOI (micro-cap, vyssia vola) - GOOGL je
# svojou trhovou kapitalizaciou/charakterom najblizsie k NVDA z uz odladenych
# tickerov. NIE empiricky backtestovane na vlastnych datach (ziadna historia),
# prehodnotit po zozbierani realnych cyklov.
GOOGL_SL_PCT = _float("GOOGL_SL_PCT", 1.2)
GOOGL_TP_PCT = _float("GOOGL_TP_PCT", 1.8)
GOOGL_TRADE_INTERVAL_HOURS = _float("GOOGL_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
GOOGL_OFF_HOURS_INTERVAL_HOURS = _float("GOOGL_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
GOOGL_WEEKEND_INTERVAL_HOURS = _float("GOOGL_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== UNITREE (NEAKTIVNE - zbiera historiu) ==============================
# Pridany 2026-08-19 na ziadost pouzivatela (Unitree Robotics - cinsky vyrobca
# humanoidnych/quadruped robotov) - IPO na sanghajskom STAR Markete PRESNE
# v den pridania (2026-08-19), akcia +460 az +542% v prvy den (viz CNBC).
# ENABLE_UNITREE default FALSE (rovnaky "len zbiera historiu" vzor ako AAOI/
# MINIMAX vyssie) - kedze IPO bolo doslova dnes, nemame ZIADNU cenovu historiu,
# takze aj korelacia s ostatnymi tickermi (predpoklad pre "aktivny hned" vzor
# ako GOOGL) je zatial nemoznenie vypocitat. Ked pribudne dost dat (min.
# CORR_MIN_OVERLAP prekryvajucich sa hodinovych barov voci VSETKYM aktivnym
# tickerom, viz index.html readinessBannerHtml), dashboard sam nahlasi v
# "Historia signalov" danho tickera, ze je pripraveny na rozhodnutie.
# STRIKE symbol overeny naozivo (2026-08-19, get_markets() vratil 'UNITREE-USD',
# status 'trading', mark_price ~119) - NA ROZDIEL od MINIMAX (sukromna firma
# bez trhu) je toto SKUTOCNY synteticky tracker realnej verejne obchodovanej
# akcie, ale na sanghajskej burze (STAR Market), nie US - TRADING_HOURS_START/
# END_UTC (NYSE-orientovany default) je preto len hruba aproximacia, rovnako
# ako uz MINIMAX pouziva pre neznamy trh (prehodnotit pri realnej aktivacii).
# yfinance nema ziadnu pouzitelnu historiu (IPO dnes + CNY nominal, nekompatibilna
# skala so Strike USD trackerom, rovnaky dovod ako SKHYNIX yf_volume_only) -
# jediny zdroj dat je vlastny 1-min Strike poller (price_poller.py, ALL_ASSETS).
STRIKE_UNITREE_SYMBOL = os.getenv("STRIKE_UNITREE_SYMBOL", "UNITREE-USD")
UNITREE_MIN_CONFIDENCE = _int("UNITREE_MIN_CONFIDENCE", MIN_CONFIDENCE)
UNITREE_MARGIN_USD = _float("UNITREE_MARGIN_USD", 50)
UNITREE_LEVERAGE = _int("UNITREE_LEVERAGE", 10)
UNITREE_LIQUIDATION_CUSHION_MULTIPLE = _float("UNITREE_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Rovnaka sirka ako MINIMAX/NIGHT pri ich zavedeni - genuinne nezname riziko
# BEZ ziadnej cenovej historie, navyse zdokumentovana extremna IPO-den
# volatilita (+460/542% v prvy den) potvrdzuje, ze konzervativnejsi odhad nez
# napr. AAOI/GOOGL je tu opodstatneny. NIE empiricky backtestovane.
UNITREE_SL_PCT = _float("UNITREE_SL_PCT", 6.0)
UNITREE_TP_PCT = _float("UNITREE_TP_PCT", 9.0)
UNITREE_TRADE_INTERVAL_HOURS = _float("UNITREE_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
UNITREE_OFF_HOURS_INTERVAL_HOURS = _float("UNITREE_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
UNITREE_WEEKEND_INTERVAL_HOURS = _float("UNITREE_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)
