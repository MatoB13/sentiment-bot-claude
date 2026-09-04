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
NEAR_EFFORT = os.getenv("NEAR_EFFORT", "")
AAPL_EFFORT = os.getenv("AAPL_EFFORT", "")
ZHIPU_EFFORT = os.getenv("ZHIPU_EFFORT", "")
CRCL_EFFORT = os.getenv("CRCL_EFFORT", "")
PUMP_EFFORT = os.getenv("PUMP_EFFORT", "")

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
# 2026-09-04 (audit, schvalene pouzivatelom) - MINIMALNA HLBKA PRERAZENIA pre
# vstup V SMERE prerazenej watch urovne, v nasobkoch hodinoveho ATR14.
#
# Watch trigger vystreli na prvom minutovom ticku za urovnou - a zo 541 triggerov
# bolo 40 % knotov (cena sa do close hodiny vratila spat). Claude v momente
# triggeru knot od pohybu nerozozna: otvoril z knotu rovnako casto (18 %) ako z
# pohybu, ktory drzal. Vysledok na 94 watch obchodoch: cena drzala -> 45 % win,
# +2.6 R; knot -> 18 % win, -21.5 R. Cela strata watch mechanizmu su knoty.
#
# Trigger (pohlad) zostava minutovy - je to jediny rychly detektor, ze sa nieco
# deje. Brana je LEN na vstup v smere prerazenia (trade_cycle._watch_break_too_shallow):
# ak je cena v case rozhodnutia menej nez tolkoto ATR za urovnou (alebo uz spat
# dnu), obchod sa zamietne s reject_reason=watch_break_too_shallow. Skutocny
# pohyb (cislo, hack, tweet) prejde 0.3 ATR za minutu, takze pren to nepridava
# ziadnu latenciu; pomaly drift a knot to nedaju.
#
# Prah nie je z dat urcitelny presnejsie nez "niekde 0.25-0.35": zablokovana
# mnozina je -18 az -22 R pri kazdom prahu 0.2-0.5, ponechana ~0 +-3 R (s
# produkcnym Wilder atr14: 0.25 -> +2.3 R / 35 obchodov, 0.30 -> -0.4 / 28,
# 0.35 -> +3.2 / 23). Claude cislo NEPOZNA (viz claude_analyst watch blok) -
# inak by podla neho posuval samotne watch urovne.
#
# Po zamietnuti system SAM nastavi watch na uroven +- tolkoto ATR v smere
# prerazenia (trade_cycle._auto_watch_after_shallow_break), ak Claude ziadny
# nenastavil - inak by bol ticker po zamietnuti slepy az do planovaneho behu.
WATCH_BREAK_MIN_ATR = _float("WATCH_BREAK_MIN_ATR", 0.30)

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
# 2026-08-27 (na ziadost pouzivatela, po ADA #90 incidente - cooldown vyssie
# umlcal dve po sebe iduce mechanicke kontroly, kym pozicia dalej stracala
# hodnotu, cim bola realna strata vacsia nez musela byt) - VYNIMKA z cooldownu:
# ak sa unrealized_pnl_pct od POSLEDNEJ plnej eskalacie zhorsil o dalsich
# tolkoto podielu SL vzdialenosti asetu, cooldown sa OBIDE (viz trade_cycle.
# _run_position_health_check) - toto uz nie je opakovanie stareho signalu, ale
# NOVY, HORSI fakt, ktory si zasluzi vlastny Claude pohlad aj pocas cooldownu.
HEALTH_CHECK_COOLDOWN_BYPASS_WORSENING_FRACTION = _float(
    "HEALTH_CHECK_COOLDOWN_BYPASS_WORSENING_FRACTION", 0.3)
# 2026-08-30 (ZEC #141 incident) - vyssia VYNIMKA vyssie ("zhorsil sa OD
# POSLEDNEJ eskalacie") merala zly ukazovatel: pri #141 bola strata uz na
# 99.7% SL vzdialenosti (prakticky nalepena na SL), ale zhorsenie od
# poslednej eskalacie (1h dozadu) bolo tesne POD prahom, takze cooldown
# zablokoval Claude cyklus len 17 min pred tym, nez pozicia zasiahla SL.
# Toto je NEZAVISLA druha vynimka - ak nerealizovana strata dosiahne tolkoto
# podielu SL vzdialenosti (bez ohladu na to, ci/kolko sa "zhorsila od
# minula"), cooldown sa ignoruje NAOZAJ CELY - kazdy nasledujuci cyklus
# znova prebehne (nie len jednorazovo), kym sa pozicia bud neotoci pod tento
# prah, alebo sa nezavrie (SL/TP/AI-close). Vyslovna volba pouzivatela: "v
# takych pripadoch kaslem na cooldown".
HEALTH_CHECK_COOLDOWN_BYPASS_SL_PROXIMITY_FRACTION = _float(
    "HEALTH_CHECK_COOLDOWN_BYPASS_SL_PROXIMITY_FRACTION", 0.5)
# 2026-08-21 (na ziadost pouzivatela, po NAS100 SL incidente - Claude odporucil
# consider_closing s close_confidence=50 hodinu pred SL, nikdy sa nezasiahlo) -
# ked position health check vrati recommendation="consider_closing" A
# close_confidence >= tento prah, bot uz NIE JE len "opinion pre cloveka"
# (povodne spravanie), ale poziciu SAM zatvori (market order, viz
# trade_cycle._run_position_health_check) - JEDNORAZOVO, bez cakania na
# potvrdenie druhym cyklom (confidence cislo uz JE kalibrovana miera istoty).
# Plati pre VSETKY tickery rovnako (na ziadost pouzivatela).
AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD = _float("AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD", 50)

# --- 2026-09-04: DVOJFAZOVY CYKLUS (bod 6 auditu) --------------------------
# Problem: 95 % platenych cyklov konci `none`, planovane cykly su 74 % vsetkych
# a otvaraju obchod v 1.3 %. Kazdy stoji ~$0.17, pricom vacsinu kontextu tvoria
# web_search vysledky.
#
# Riesenie: pred plnym cyklom bezi LACNY SKEN (claude_analyst.triage) bez
# web_search a s kratkym vlastnym promptom - odpoveda LEN na otazku "zmenilo sa
# nieco, co stoji za plnu analyzu so spravami?". Plny cyklus potom bezi len ked
# sken povie ano.
#
# REZIMY (menit LEN cez ENV, default je bezpecny):
#   "off"    - sken vobec nebezi (spravanie pred 2026-09-04)
#   "shadow" - sken bezi, ale plny cyklus bezi VZDY; verdikt sa uklada do
#              CycleLog.triage vedla skutocneho vysledku. TOTO JE PRVY KROK -
#              az z nazbieranych dat sa da povedat, ci sken nezahadzuje
#              obchody (viz dashboard tab "Dvojfazovy cyklus").
#   "active" - sken realne rozhoduje; pri "nie" sa zapise CycleLog s
#              outcome="triage_skip" a plny cyklus sa nespusti.
#
# Sken sa NIKDY nepyta pri: watch/makro/post-close triggeri (to uz JE udalost),
# otvorenej pozicii (tam bezi health check, ktory je mechanicky by default),
# nespracovanom vcerajsku (retrospektiva stoji za plny pohlad raz denne) a ked
# od posledneho PLNEHO cyklu ubehlo viac nez TRIAGE_FORCE_FULL_HOURS - sken
# spravy necita, takze bot nesmie byt bez nich lubovolne dlho.
TRIAGE_MODE = os.getenv("TRIAGE_MODE", "off").strip().lower()
TRIAGE_FORCE_FULL_HOURS = _float("TRIAGE_FORCE_FULL_HOURS", 6)
# Model skenu - default rovnaky ako hlavny. Haiku sa da skusit az ked shadow
# data ukazu, ze sken rozhoduje spolahlivo.
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "") or CLAUDE_MODEL
TRIAGE_EFFORT = os.getenv("TRIAGE_EFFORT", "low")

# 2026-08-21 (na ziadost pouzivatela, pred cestou bez pocitaca) - "je bot
# nazivo?" Discord hlasenie, viz heartbeat_check.py pre plny kontext a
# DOLEZITE OBMEDZENIE (zachyti len "proces zije, ale zaseknuty", nie uplny
# pad procesu). HEARTBEAT_CHECK_ENABLED explicitny vypinac (na ziadost
# pouzivatela) - ak by davat vela false-positive alertov (napr. Strike API
# ma bezny kratky vypadok), da sa cez Railway env rychlo vypnut bez potreby
# menit kod.
HEARTBEAT_CHECK_ENABLED = _bool("HEARTBEAT_CHECK_ENABLED", "true")
HEARTBEAT_STALE_THRESHOLD_MINUTES = _float("HEARTBEAT_STALE_THRESHOLD_MINUTES", 15)
# 2026-08-21 (na ziadost pouzivatela, ZEC nalez) - TP je "take_profit_limit"
# (pasivna, plni sa postupne - viz strike_client.open_bracket_position), takze
# moze ostat DLHO ciastocne vyplnena, ak sa cena po ciastocnom naplneni stiahne
# spat pod TP uroven. Ked zostavajuca ziva velkost pozicie klesne pod tento
# podiel PÔVODNEJ velkosti (Trade.size), position_monitor ju rovno market-
# zatvori namiesto cakania na plny POSITION_MAX_HOURS timeout - inak by
# ekonomicky bezvyznamny "dust" zvysok blokoval symbol (max 1 pozicia naraz)
# potencialne cely zvysok 24h.
DUST_POSITION_MAX_REMAINING_PCT = _float("DUST_POSITION_MAX_REMAINING_PCT", 0.20)
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

# --- 2026-08-31: SLOTOVE ROZPRESTRETIE CYKLOV -------------------------------
# Problem (namerane na 7 dnoch produkcie): scheduler tikal na
# min(trade_interval_hours), takze VSETKY tickery sa vyhodnotili v jednej davke
# a potom bolo dlho ticho. Za tyzden: 73% desatminutovych okien bez jedineho
# cyklu, najdlhsie ticho 119 minut, a v spicke 10 cyklov naraz - presne na
# _DISPATCH_CONCURRENCY_LIMIT. Pri prechode na 2h baseline by to bolo horsie:
# simulacia dala 91% stvrthodin bez aktivity a ticho az 240 minut.
#
# Riesenie (navrh pouzivatela, overeny simulaciou 14 dni): kazdy ticker dostane
# slot 1..RUN_SLOT_COUNT. Slot k znamena "som due v k-tej dvanastine svojho
# intervalu" - pri 2h intervale su to 10-minutove okna, pri 1h 5-minutove.
# Scheduler tika na SCHEDULER_TICK_MINUTES a _is_due riesi zvysok.
#
# Simulacia (15 tickerov, krypto 2/3/3h, tradicne 2/4/6h, 14 dni):
#                          pokrytych 15-min okien   max ticho   p90 cyklov/tick
#   bez slotov (2h tick)              9%              240 min        15
#   so slotmi                        79%              105 min         2
# Zvysne diery su vikendove (tradicne tickery maju 360-min interval) - to sa
# fazovanim neda odstranit, len skratenim vikendoveho intervalu.
#
# CENA: mriezka sa pri prechode trading -> off-hours -> vikend prekotvi, co
# obcas prida beh navyse - simulacia ukazala +13% cyklov. Proti predcasnemu
# behu chrani RUN_SLOT_MIN_GAP_FRACTION nizsie.
RUN_SLOT_COUNT = _int("RUN_SLOT_COUNT", 12)
# 2026-09-02 (navrh pouzivatela) - slot je PEVNA CAST HODINY, nie zlomok
# vlastneho intervalu tickera. Pri 12 slotoch je to 5 minut: slot 1 = :00,
# slot 2 = :05, ... slot 12 = :55. Odvodene z RUN_SLOT_COUNT, takze zmena
# poctu slotov automaticky zmeni ich sirku.
RUN_SLOT_WIDTH_MINUTES = 60.0 / RUN_SLOT_COUNT
# Ako casto tika scheduler. MUSI delit interval/RUN_SLOT_COUNT bez zvysku,
# inak by sa slotove okno mohlo minut. Pri 12 slotoch a 2h intervale je okno
# 10 min, takze 5 min tick ho vzdy trafi.
SCHEDULER_TICK_MINUTES = _float("SCHEDULER_TICK_MINUTES", 5)
# Poistka: nikdy nespustit dalsi cyklus skor nez po tomto podiele intervalu od
# posledneho behu. Chrani pred tym, ze prekotvenie mriezky pri zmene intervalu
# (trading -> off-hours) spusti cyklus hned po predchadzajucom.
RUN_SLOT_MIN_GAP_FRACTION = _float("RUN_SLOT_MIN_GAP_FRACTION", 0.5)

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
# UNITREE pridany 2026-08-19, aktivovany 2026-08-29 (na ziadost pouzivatela,
# po nazbierani dost vlastnych barov - viz UNITREE sekcia nizsie pre plne
# zdovodnenie vratane prepocitaneho SL/TP z realnych dat).
ENABLE_UNITREE = _bool("ENABLE_UNITREE", "true")
# NEAR pridany 2026-08-21 na ziadost pouzivatela - rovnaky "aktivny hned" vzor
# ako ZEC/GOOGL (NIE "len zbiera historiu" ako AAOI/MINIMAX/UNITREE) - ma uz
# overene realne data (yfinance NEAR-USD + Binance NEARUSDT), viz NEAR sekcia
# nizsie pre plne zdovodnenie SL/TP.
ENABLE_NEAR = _bool("ENABLE_NEAR", "true")
# ZHIPU pridany 2026-08-29 (na ziadost pouzivatela - Strike pridal ZHIPU-USD/
# BNB-USD) - rovnaky "len zbiera historiu" vzor ako AAOI/MINIMAX/UNITREE pri
# ich pridani (ZIADNA cenova historia, korelaciu ani SL/TP kalibraciu zatial
# nemozno spocitat - viz ZHIPU sekcia nizsie). BNB sa NEPRIDAVA ako ticker (na
# rozdiel od ZHIPU) - otestovana korelacia cez CoinGecko ukazala silnu zhodu s
# uz aktivnym krypto kosom (ADA/NEAR/ZEC/BTC 0.5-0.7), zbytocna redundancia.
ENABLE_ZHIPU = _bool("ENABLE_ZHIPU", "false")
# CRCL (Circle Internet Group - vydavatel USDC stablecoinu, NYSE od 2025)
# pridany 2026-08-30 na ziadost pouzivatela - rovnaky "aktivny hned" vzor ako
# ZEC/GOOGL/NEAR (NIE "len zbiera historiu"), kedze ide o SKUTOCNU verejne
# obchodovanu akciu s bezne dostupnou yfinance historiou (na rozdiel od
# MINIMAX/UNITREE/ZHIPU synteticky trackovanych sukromnych firiem) - viz CRCL
# sekcia nizsie pre plne zdovodnenie SL/TP aj korelacnu analyzu.
ENABLE_CRCL = _bool("ENABLE_CRCL", "true")
ENABLE_PUMP = _bool("ENABLE_PUMP", "true")

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
# NEAR-USD overene naozivo v /v2/markets (2026-08-21).
STRIKE_NEAR_SYMBOL = os.getenv("STRIKE_NEAR_SYMBOL", "NEAR-USD")

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

# ============================== ZHIPU (NEAKTIVNE - zbiera historiu) ==============================
# Pridany 2026-08-29 na ziadost pouzivatela (Zhipu AI/Z.ai - sukromna cinska AI
# firma, tvorca GLM/ChatGLM modelov, jedna z "AI Tiger" startupov spolu s
# MiniMax/DeepSeek/Moonshot AI) - Strike pridal ZHIPU-USD ten isty den ako
# BNB-USD. Rovnaka kategoria ako MINIMAX (synteticky Strike tracker sukromnej
# firmy, ZIADNY realny burzovy trh/orderbook) - ENABLE_ZHIPU default FALSE,
# LEN zbiera historiu cez vlastny 1-min Strike poller (price_poller.py
# ALL_ASSETS), kym nenazbiera MIN_OWN_BARS (210) na TA aj ATR-based SL/TP
# kalibraciu (rovnaky postup ako pri UNITREE 2026-08-29 - viz ta sekcia).
# Overene naozivo (2026-08-29): ziadny CoinGecko coin ("zhipu" search prazdny
# vysledok) ani yfinance ticker - Zhipu AI nie je verejne obchodovana, presne
# ako MiniMax Group, takze korelaciu s ostatnymi tickermi zatial NEMOZNO
# spocitat (na rozdiel od BNB, ktore sa NEPRIDALO - jeho korelacia sa DALA
# spocitat cez CoinGecko a ukazala silnu zhodu s existujucim krypto kosom).
STRIKE_ZHIPU_SYMBOL = os.getenv("STRIKE_ZHIPU_SYMBOL", "ZHIPU-USD")
ZHIPU_MIN_CONFIDENCE = _int("ZHIPU_MIN_CONFIDENCE", MIN_CONFIDENCE)
ZHIPU_MARGIN_USD = _float("ZHIPU_MARGIN_USD", 50)
ZHIPU_LEVERAGE = _int("ZHIPU_LEVERAGE", 10)
ZHIPU_LIQUIDATION_CUSHION_MULTIPLE = _float("ZHIPU_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Najsirsi konzervativny odhad (6.0/9.0, rovnako ako NIGHT/povodny MINIMAX pri
# ich pridani) kvoli uplnej absencii cenovej historie - NIE empiricky
# backtestovane. PREHODNOTIT cez realny ATR14 z vlastnych PriceBar dat
# (rovnaky postup ako MINIMAX 2026-08-19 aj UNITREE 2026-08-29), akonahle
# ma dost vlastnej historie na aktivaciu - viz [[feedback_new_ticker_sl_tp_derivation]]
# politika.
ZHIPU_SL_PCT = _float("ZHIPU_SL_PCT", 6.0)
ZHIPU_TP_PCT = _float("ZHIPU_TP_PCT", 9.0)
ZHIPU_TRADE_INTERVAL_HOURS = _float("ZHIPU_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
ZHIPU_OFF_HOURS_INTERVAL_HOURS = _float("ZHIPU_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
ZHIPU_WEEKEND_INTERVAL_HOURS = _float("ZHIPU_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

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

# ============================== NEAR ==============================
# Pridany 2026-08-21 na ziadost pouzivatela - Near Protocol (L1 smart-contract
# platforma, 2026 AI-infra naratv; vybrany aj ako najnizsie-korelovany Strike
# ticker voci existujucemu portfoliu, viz korelacna analyza tej istej session).
# SL/TP odvodene z REALNYCH hodinovych OHLC dat (yfinance NEAR-USD, 60 dni /
# 1411 barov) rovnakou ATR metodikou ako ostatne tickery - NIE skopirovane z
# "podobneho" tickera (viz feedback_new_ticker_sl_tp_derivation policy).
# POZOR: posledny (v momente pridania) hodinovy ATR14 = 2.08% bol vyrazne NAD
# typickou hodnotou pre tento ticker (median rolling ATR14 za celych 60 dni =
# 1.01%, teda ~2x) - 21.8.2026 bol zjavne nezvycajne volatilny den naprieč
# celym krypto trhom (viz aj sucasny BTC risk-on rally kontext). Kotva preto
# ZAMERNE NIE posledna hodnota, ale 75. percentil ROLLING ATR14 (1.28%) -
# odolnejsi voci jednorazovemu dnesnemu vychylku, s primeranou rezervou nad
# median (nie najtesnejsie mozne cislo). 2.34x ATR (rovnaky pomer ako ostatne
# tickery) = SL~3.01%, zaokruhlene na 3.0/4.5 (1:1.5 pomer).
# Ide priamo do produkcie (NIE disabled/collecting ako MINIMAX/UNITREE pri ich
# pridani) - na ziadost pouzivatela, kedze uz ma overene realne data (yfinance
# aj Binance NEARUSDT, obe overene naozivo 2026-08-21).
NEAR_MIN_CONFIDENCE = _int("NEAR_MIN_CONFIDENCE", MIN_CONFIDENCE)
NEAR_MARGIN_USD = _float("NEAR_MARGIN_USD", 50)
NEAR_LEVERAGE = _int("NEAR_LEVERAGE", 6)
NEAR_LIQUIDATION_CUSHION_MULTIPLE = _float("NEAR_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
NEAR_SL_PCT = _float("NEAR_SL_PCT", 3.0)
NEAR_TP_PCT = _float("NEAR_TP_PCT", 4.5)
# 24/7 krypto - vsetky tri intervaly defaultne rovnake ako ADA/ZEC.
NEAR_TRADE_INTERVAL_HOURS = _float("NEAR_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
NEAR_OFF_HOURS_INTERVAL_HOURS = _float("NEAR_OFF_HOURS_INTERVAL_HOURS", NEAR_TRADE_INTERVAL_HOURS)
NEAR_WEEKEND_INTERVAL_HOURS = _float("NEAR_WEEKEND_INTERVAL_HOURS", NEAR_TRADE_INTERVAL_HOURS)

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

# ============================== UNITREE (AKTIVNY od 2026-08-29) ==============================
# Pridany 2026-08-19 na ziadost pouzivatela (Unitree Robotics - cinsky vyrobca
# humanoidnych/quadruped robotov) - IPO na sanghajskom STAR Markete PRESNE
# v den pridania (2026-08-19), akcia +460 az +542% v prvy den (viz CNBC).
# 2026-08-29: AKTIVOVANY (na ziadost pouzivatela) - 233 vlastnych hodinovych
# barov (> MIN_OWN_BARS=210), teda dost na plnohodnotne TA aj SL/TP kalibraciu
# (viz UNITREE_SL_PCT/TP_PCT nizsie). Predtym FALSE (rovnaky "len zbiera
# historiu" vzor ako AAOI/MINIMAX) - IPO bolo v den pridania (19.8.), takze
# nebola ziadna cenova historia na kalibraciu ani korelaciu.
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
# 2026-08-29 PREPOCITANE z realnych dat (233 vlastnych hodinovych barov) -
# povodnych 6.0%/9.0% bol len konzervativny ODHAD BEZ ziadnej cenovej historie
# (viz nizsie), zavedeny v den IPO. Realny hodinovy ATR14 sa za 9 dni USADIL
# z ~2.3% (den po IPO) na ~0.4-0.9% v poslednych dnoch (standardna new-listing
# volatility decay krivka) - povodny 6.0% odhad bol teda cca 3x sirsi, nez
# realne data ukazuju. Pouzity rovnaky pomer SL/ATR (~2.34x) ako pri
# GOOGL/NVDA kalibracii, na priemernom ATR% za poslednych 72h (0.86%, robustnejsie
# nez posledna sviecka samotna): 0.86 * 2.34 = 2.01% -> zaokruhlene na 2.0%/3.0%
# (standardny 1.5x SL/TP pomer, viz [[feedback_new_ticker_sl_tp_derivation]]
# politika - z REALNYCH dat, nie kopirovane od podobneho tickera).
# Povodny komentar pre kontext: sirka 6.0/9.0 kopirovala MINIMAX/NIGHT vzor
# ("genuinne nezname riziko bez historie" + zdokumentovana extremna IPO-den
# volatilita +460/542% v prvy den) - opodstatnene VTEDY, uz nie teraz.
UNITREE_SL_PCT = _float("UNITREE_SL_PCT", 2.0)
UNITREE_TP_PCT = _float("UNITREE_TP_PCT", 3.0)
UNITREE_TRADE_INTERVAL_HOURS = _float("UNITREE_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
UNITREE_OFF_HOURS_INTERVAL_HOURS = _float("UNITREE_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
UNITREE_WEEKEND_INTERVAL_HOURS = _float("UNITREE_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== AAPL (NEAKTIVNE - Strike este nema market) ==============================
# Pridany 2026-08-22 na ziadost pouzivatela (Strike mal Apple pridat "tento
# tyzden", pouzivatel odchadza a nechce cakat na aktivaciu naziva) - infra
# pripravena VOPRED presne ako GOOGL (rovnaky realny NASDAQ mega-cap titul,
# rovnaky spolahlivy yfinance zdroj), ale ENABLE_AAPL default FALSE, kedze
# Strike /v2/markets ESTE AAPL-USD nevracia (overene naozivo 2026-08-22,
# get_markets() ho nevratil). NA ROZDIEL od AAOI/MINIMAX/UNITREE (skutocne
# "zbiera historiu" tickery) tu nejde o cakanie na korelacnu historiu - Apple
# ma bohatu vlastnu yfinance historiu uz teraz, jedina prekazka je, ze Strike
# symbol proste este neexistuje. Kym nebude, price_poller/funding_tracker
# (oba iteruju ALL_ASSETS, nie len enabled_assets()) tento symbol jednoducho
# kazdy tik preskocia (uz existujuci "chyba v /v2/markets odpovedi" fallback,
# viz price_poller.poll_prices) - ZIADNY risk padu procesu. Ked Strike AAPL-USD
# prida, pouzivatel len nastavi ENABLE_AAPL=true na Railway (a STRIKE_AAPL_SYMBOL,
# ak by realny listing pouzival iny presny nazov symbolu nez predpokladany default).
STRIKE_AAPL_SYMBOL = os.getenv("STRIKE_AAPL_SYMBOL", "AAPL-USD")
ENABLE_AAPL = _bool("ENABLE_AAPL", "false")
AAPL_MIN_CONFIDENCE = _int("AAPL_MIN_CONFIDENCE", MIN_CONFIDENCE)
AAPL_MARGIN_USD = _float("AAPL_MARGIN_USD", 100)
AAPL_LEVERAGE = _int("AAPL_LEVERAGE", 10)  # DEAD - viz risk_manager._leverage_from_cushion, skutocna paka sa odvodzuje z cushion multiple nizsie
AAPL_LIQUIDATION_CUSHION_MULTIPLE = _float("AAPL_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
# Hodinovy ATR (60d, yfinance, MEDIAN cez cele okno - nie posledna hodnota,
# rovnaky dovod ako pri NEAR: posledna hodnota 0.66% bola v case vypoctu POD
# medianom 0.78%, teda by podhodnotila typicku volatilitu) = 0.78% z ceny.
# Rovnaky SL/ATR pomer (~2.34x) ako uz naladeny NVDA/GOOGL (rovnaky NASDAQ
# mega-cap profil). NIE empiricky backtestovane na vlastnych datach (ziadna
# historia, Strike market este neexistuje), prehodnotit po zozbierani realnych
# cyklov (rovnaky caveat ako GOOGL malo pri svojom zavedeni).
AAPL_SL_PCT = _float("AAPL_SL_PCT", 1.8)
AAPL_TP_PCT = _float("AAPL_TP_PCT", 2.7)
AAPL_TRADE_INTERVAL_HOURS = _float("AAPL_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
AAPL_OFF_HOURS_INTERVAL_HOURS = _float("AAPL_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
AAPL_WEEKEND_INTERVAL_HOURS = _float("AAPL_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== CRCL (AKTIVNY od 2026-08-30) ==============================
# Pridany 2026-08-30 na ziadost pouzivatela (Strike prave pridal CRCL-USD) -
# Circle Internet Group, vydavatel USDC stablecoinu, NYSE IPO 2025. SKUTOCNA
# verejne obchodovana akcia (nie synteticky tracker sukromnej firmy ako
# MINIMAX/ZHIPU) - realna yfinance historia dostupna, preto "aktivny hned"
# vzor ako GOOGL/NEAR/ZEC (NIE "len zbiera historiu" ako UNITREE/ZHIPU pri
# ich pridani).
# Korelacna analyza (2026-08-30, 30 dni hodinovych log-vynosov cez Yahoo
# Finance chart API, kedze lokalne yfinance malo SSL problem): najsilnejsie s
# NVDA (0.60) a BTC (0.55) - logicke, CRCL je rastova tech/AI-era akcia A
# jeho biznis (USDC) je priamo naviazany na krypto adopciu. Prakticky nulove/
# mierne opacne s GOOGL (-0.11) a WTI (-0.09). Ziadna korelacia nad 0.7,
# rozumny diverzifikacny kandidat, aj ked nie uplne nezavisly.
# SL/TP z REALNYCH dat (nie kopirovane) - hodinovy ATR14 (30 dni cez Yahoo
# chart API) bol pomerne stabilny naprieč celym oknom: median 2.40% (cele
# obdobie), 2.35-2.45% aj v uzsich 48h/72h oknach (na rozdiel od UNITREE tu
# NEBOLO potreba riesit "usadenie" volatility po IPO - CRCL uz obchoduje rok).
# Vysoka volatilita (momentum akcia, +40% za poslednych 30 dni) opodstatnuje
# sirsi SL nez GOOGL/NVDA. Rovnaky SL/ATR pomer (~2.34x) ako pri GOOGL/NVDA/
# UNITREE kalibracii: 2.40 * 2.34 = 5.62% -> zaokruhlene na 5.6%/8.4% (1.5x
# TP/SL pomer, viz [[feedback_new_ticker_sl_tp_derivation]] politika).
STRIKE_CRCL_SYMBOL = os.getenv("STRIKE_CRCL_SYMBOL", "CRCL-USD")
CRCL_MIN_CONFIDENCE = _int("CRCL_MIN_CONFIDENCE", MIN_CONFIDENCE)
CRCL_MARGIN_USD = _float("CRCL_MARGIN_USD", 50)
CRCL_LEVERAGE = _int("CRCL_LEVERAGE", 10)  # DEAD - viz risk_manager._leverage_from_cushion, skutocna paka sa odvodzuje z cushion multiple nizsie
CRCL_LIQUIDATION_CUSHION_MULTIPLE = _float("CRCL_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
CRCL_SL_PCT = _float("CRCL_SL_PCT", 5.6)
CRCL_TP_PCT = _float("CRCL_TP_PCT", 8.4)
CRCL_TRADE_INTERVAL_HOURS = _float("CRCL_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
CRCL_OFF_HOURS_INTERVAL_HOURS = _float("CRCL_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
CRCL_WEEKEND_INTERVAL_HOURS = _float("CRCL_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)



# --- SL/TP grid backtest: naklady na obchod (2026-09-01, po externom audite) ---
# Simulacia v sl_grid_backtest.py pocitala CISTY cenovy pohyb (margin * leverage
# * pct) a poplatky ani spread nezapocitavala vobec. To systematicky zvyhodnovalo
# kombinacie s tesnym SL: tesnejsi SL -> vyssia paka -> vacsi notional -> vacsie
# realne naklady, ktore ale simulacia nevidela.
#
# Sadzba je MEDIAN Z NASICH REALNYCH OBCHODOV (163 uzavretych, fees_usd/
# notional_usd), nie odhad z cennika - median 0.0769 %, priemer 0.0785 %,
# p90 0.0950 %. Je to za CELY round-trip, teda vstup aj vystup dokopy.
BACKTEST_FEE_PCT_OF_NOTIONAL = _float("BACKTEST_FEE_PCT_OF_NOTIONAL", 0.0769)
# Spread sa berie PER TICKER z jeho vlastnej historie (median ta.spread_pct);
# toto je len zaloha, ked ticker este ziadny zaznam nema. Rozpatie naprieč
# portfoliom je siroke - BTC 0.002 %, vacsina 0.06-0.08 %, NIGHT az 0.36 %.
BACKTEST_FALLBACK_SPREAD_PCT = _float("BACKTEST_FALLBACK_SPREAD_PCT", 0.08)
# ============================== PUMP (AKTIVNY od 2026-08-31) ==============================
# Pridany 2026-08-31 na ziadost pouzivatela (Strike pridal PUMP-USD ~27. 8.).
# Pump.fun - Solana launchpad pre meme tokeny, token PUMP (ICO jul 2025).
#
# KORELACIA (overena 2026-08-31, viz [[sentiment_bot_pump_correlation_check]]):
# konzistentne NIZSIA nez priemer krypta - 0.28 vs 0.44 na celej historii,
# 0.49 vs 0.63 na poslednych 200h. Voci tradicnym aktivam prakticky nula
# (GOOGL 0.02, WTI -0.06, UNITREE 0.00). Najnizsia dvojica je PUMP-NIGHT (0.13).
# Nie je to nekorelovany ticker, ale v ramci krypta je najnezavislejsi.
#
# ZDROJ DAT: Pump.fun na Yahoo Finance NEEXISTUJE (overenych 6 variantov
# tickera, vsetky prazdne), preto NEMA yf_symbol a pouziva sa Binance
# PUMPUSDT ako OHLC zdroj, kym sa nenazbiera MIN_OWN_BARS vlastnych price_bars
# (~9 dni) - viz market_data.fetch_ohlcv_binance a assets.py binance_ohlc_symbol.
# Binance cena bola overena proti Strike mark_price (pomer 1.002 = ten isty
# instrument, rovnaka kontrola ako po HYPE mismatchi pri NEAR).
#
# SL/TP z REALNYCH dat (politika [[feedback_new_ticker_sl_tp_derivation]]):
# hodinovy ATR14 z 1000 Binance barov. Median 2.775% za 14 dni (2.69% za 7 dni,
# 2.11% za 48h, 2.06% za cele 42-dnove okno) - volatilita sa upokojuje, zamerne
# beriem to KONZERVATIVNEJSIE 14-dnove cislo, nie najtichsie okno (presne ta
# chyba, ktora pri UNITREE dala 0.209% SL). Je to NAJVOLATILNEJSI ticker
# v portfoliu: 2.775% vs CRCL 2.35%, ZEC 1.64%, ADA 1.26%.
# Pomer SL/ATR 2.20x = median NASICH KRYPTO tickerov (ADA 2.55, BTC 2.56,
# NEAR 2.26, ZEC 2.14, NIGHT 1.66) - NIE akciovy 2.34x a NIE sweep k=1.0,
# ktory pri 59% SL hit rate za 24h vysiel absurdne tesne.
# 2.775 * 2.20 = 6.1% SL, TP 9.15% -> 9.2% (pomer 1.5, ako ostatne tickery).
STRIKE_PUMP_SYMBOL = os.getenv("STRIKE_PUMP_SYMBOL", "PUMP-USD")
PUMP_MIN_CONFIDENCE = _int("PUMP_MIN_CONFIDENCE", MIN_CONFIDENCE)
PUMP_MARGIN_USD = _float("PUMP_MARGIN_USD", 50)
PUMP_LEVERAGE = _int("PUMP_LEVERAGE", 5)  # DEAD - viz risk_manager._leverage_from_cushion, skutocna paka sa odvodzuje z cushion multiple nizsie
PUMP_LIQUIDATION_CUSHION_MULTIPLE = _float("PUMP_LIQUIDATION_CUSHION_MULTIPLE", LIQUIDATION_CUSHION_MULTIPLE)
PUMP_SL_PCT = _float("PUMP_SL_PCT", 6.1)
PUMP_TP_PCT = _float("PUMP_TP_PCT", 9.2)
# 24/7 krypto - rovnake intervaly ako ADA/ZEC/NEAR, ziadne off-hours/vikend rozlisenie.
PUMP_TRADE_INTERVAL_HOURS = _float("PUMP_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
PUMP_OFF_HOURS_INTERVAL_HOURS = _float("PUMP_OFF_HOURS_INTERVAL_HOURS", PUMP_TRADE_INTERVAL_HOURS)
PUMP_WEEKEND_INTERVAL_HOURS = _float("PUMP_WEEKEND_INTERVAL_HOURS", PUMP_TRADE_INTERVAL_HOURS)
