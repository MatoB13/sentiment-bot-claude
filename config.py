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
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
# FRED (St. Louis Fed) - volne API, zdielane pre vsetky assety (CPI/Core CPI/
# Fed funds rate - presne cislo namiesto web_search odhadu). Registracia:
# https://fredaccount.stlouisfed.org
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
# Marketaux - free tier (100 req/den) news+sentiment API, per-asset (viz
# assets.py marketaux_query). Registracia: https://www.marketaux.com
MARKETAUX_API_KEY = os.getenv("MARKETAUX_API_KEY", "")

# DB
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///trades.db")

# Trading / risk - skutocne zdielane pre VSETKY assety (nie per-ticker).
DRY_RUN = _bool("DRY_RUN", "true")
MONITOR_INTERVAL_MINUTES = _float("MONITOR_INTERVAL_MINUTES", 10)
# Samostatny (tesnejsi) interval len pre watch_monitor.py - cenova podmienka sa
# oplati kontrolovat castejsie ako otvorene pozicie (tie uz chrani Strike-ov
# vlastny SL/TP bracket order v realnom case, nas position_monitor len
# dodatocne synchronizuje DB zaznam).
WATCH_INTERVAL_MINUTES = _float("WATCH_INTERVAL_MINUTES", 1)
POSITION_MAX_HOURS = _float("POSITION_MAX_HOURS", 24)

# --- NIZSIE (MIN_CONFIDENCE/MARGIN_USD/LEVERAGE/DEFAULT_SL_PCT/DEFAULT_TP_PCT/
# TRADE_INTERVAL_HOURS/OFF_HOURS_INTERVAL_HOURS/WEEKEND_INTERVAL_HOURS) su od
# 2026-07-31 LEN interne fallback-cascade konstanty pre vypocet per-ticker
# defaultov nizsie (NAS100_MIN_CONFIDENCE a pod.) - VSETKYCH 7 tickerov uz ma
# svoju vlastnu explicitne nastavenu premennu na Railway, takze tieto uz
# NEOVPLYVNUJU ziadne skutocne rozhodnutie za behu. Nenastavuj ich uz priamo
# na Railway - uprav rovno konkretny {TICKER}_* ekvivalent nizsie.
MIN_CONFIDENCE = _int("MIN_CONFIDENCE", 65)
MARGIN_USD = _float("MARGIN_USD", 100)
LEVERAGE = _int("LEVERAGE", 40)
DEFAULT_SL_PCT = _float("DEFAULT_SL_PCT", 0.4)
DEFAULT_TP_PCT = _float("DEFAULT_TP_PCT", 0.6)
TRADE_INTERVAL_HOURS = _float("TRADE_INTERVAL_HOURS", 4)

# --- Sedem tickerov celkovo: NAS100 (index), NVDA (akcia, POZASTAVENE),
# ADA (krypto), GOLD (komodita), WTI (ropa), NIGHT (krypto, Midnight/Cardano),
# BTC (krypto, Bitcoin).
# Vsetky bezia v tom istom cykle a zdielaju cross-market/session makro fetch
# (viz assets.py, trade_cycle.run_all_cycles), ale kazdy ma uplne nezavisly
# risk/poziciu/rozhodnutie/frekvenciu - kazdy ma VLASTNU sadu 8 premennych
# ({TICKER}_MIN_CONFIDENCE/MARGIN_USD/LEVERAGE/SL_PCT/TP_PCT/
# TRADE_INTERVAL_HOURS/OFF_HOURS_INTERVAL_HOURS/WEEKEND_INTERVAL_HOURS),
# zoskupenu nizsie ticker-po-tickeri (2026-07-31 zjednotene - predtym mal
# NAS100 bezpredponove nazvy a ADA/NIGHT nemali off_hours/weekend vobec).
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
# pre vsetkych 7 tickerov - dovoluje to napr. neskor predlzit vikendovy
# interval aj pre ADA/NIGHT bez zmeny kodu.
#
# TRADING_HOURS_START_UTC/END_UTC (13-21 = NYSE cash session 9:30-16:00 ET v
# oboch DST stavoch) ostava jedina skutocne ZDIELANA (nie per-ticker) hodnota -
# je to fakt o trhovej strukture, nie o preferencii jednotlivého assetu.
TRADING_HOURS_START_UTC = _int("TRADING_HOURS_START_UTC", 13)
TRADING_HOURS_END_UTC = _int("TRADING_HOURS_END_UTC", 21)

# Interne fallback-cascade konstanty pre {TICKER}_OFF_HOURS_INTERVAL_HOURS/
# {TICKER}_WEEKEND_INTERVAL_HOURS nizsie (rovnaky status ako MIN_CONFIDENCE a
# pod. vyssie - vsetkych 7 tickerov uz ma svoju vlastnu explicitnu premennu,
# tieto uz nic za behu neovplyvnuju, nenastavuj ich priamo na Railway).
OFF_HOURS_INTERVAL_HOURS = _float("OFF_HOURS_INTERVAL_HOURS", 2)
WEEKEND_INTERVAL_HOURS = _float("WEEKEND_INTERVAL_HOURS", 6)

ENABLE_NVDA = _bool("ENABLE_NVDA", "false")
ENABLE_ADA = _bool("ENABLE_ADA", "true")
ENABLE_GOLD = _bool("ENABLE_GOLD", "true")
ENABLE_WTI = _bool("ENABLE_WTI", "true")
ENABLE_NIGHT = _bool("ENABLE_NIGHT", "true")
ENABLE_BTC = _bool("ENABLE_BTC", "true")

# Presny symbol/asset identifikator zisti cez strike_client.get_markets() - toto
# su len predpoklady podla existujuceho NAS100-USD pomenovacieho vzoru, okrem
# WTI-USD/NIGHT-USD ktore su priamo overene naozivo v /v2/markets (2026-07-31).
STRIKE_NVDA_SYMBOL = os.getenv("STRIKE_NVDA_SYMBOL", "NVDA-USD")
STRIKE_ADA_SYMBOL = os.getenv("STRIKE_ADA_SYMBOL", "ADA-USD")
STRIKE_GOLD_SYMBOL = os.getenv("STRIKE_GOLD_SYMBOL", "XAU-USD")
STRIKE_WTI_SYMBOL = os.getenv("STRIKE_WTI_SYMBOL", "WTI-USD")
STRIKE_NIGHT_SYMBOL = os.getenv("STRIKE_NIGHT_SYMBOL", "NIGHT-USD")
STRIKE_BTC_SYMBOL = os.getenv("STRIKE_BTC_SYMBOL", "BTC-USD")

# ============================== NAS100 ==============================
# Prve/povodne assety pred multi-asset refaktorom - tieto premenne su nove
# (2026-07-31), predtym NAS100 pouzival bezpredponove MIN_CONFIDENCE/
# MARGIN_USD/LEVERAGE/DEFAULT_SL_PCT/DEFAULT_TP_PCT/TRADE_INTERVAL_HOURS
# priamo. Defaulty nizsie z nich cascade-uju, takze spravanie ostava presne
# rovnake, kym nekto explicitne nastavi NAS100_* na Railway.
NAS100_MIN_CONFIDENCE = _int("NAS100_MIN_CONFIDENCE", MIN_CONFIDENCE)
NAS100_MARGIN_USD = _float("NAS100_MARGIN_USD", MARGIN_USD)
NAS100_LEVERAGE = _int("NAS100_LEVERAGE", LEVERAGE)
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
# Sirsie SL/TP % nez NAS100 (0.4/0.6), rovnaky risk:reward pomer 1:1.5.
NVDA_SL_PCT = _float("NVDA_SL_PCT", 1.5)
NVDA_TP_PCT = _float("NVDA_TP_PCT", 2.25)
NVDA_TRADE_INTERVAL_HOURS = _float("NVDA_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
NVDA_OFF_HOURS_INTERVAL_HOURS = _float("NVDA_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
NVDA_WEEKEND_INTERVAL_HOURS = _float("NVDA_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# ============================== ADA ==============================
ADA_MIN_CONFIDENCE = _int("ADA_MIN_CONFIDENCE", MIN_CONFIDENCE)
ADA_MARGIN_USD = _float("ADA_MARGIN_USD", MARGIN_USD)
# Najnizsia paka spomedzi povodnej trojice - najvyssia volatilita.
ADA_LEVERAGE = _int("ADA_LEVERAGE", 6)
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
NIGHT_MARGIN_USD = _float("NIGHT_MARGIN_USD", MARGIN_USD)
# MAX povolena paka na Strike pre tento symbol (margin_tiers strop, overene
# naozivo cez get_market('NIGHT-USD') 2026-07-31) - vedome zvolena aj napriek
# cerstvemu bezpecnostnemu incidentu.
NIGHT_LEVERAGE = _int("NIGHT_LEVERAGE", 10)
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
BTC_SL_PCT = _float("BTC_SL_PCT", 1.5)
BTC_TP_PCT = _float("BTC_TP_PCT", 2.25)
# Rovnake ako ADA/NIGHT (dohodnute) - vsetky tri 24/7 krypto.
BTC_TRADE_INTERVAL_HOURS = _float("BTC_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
BTC_OFF_HOURS_INTERVAL_HOURS = _float("BTC_OFF_HOURS_INTERVAL_HOURS", BTC_TRADE_INTERVAL_HOURS)
BTC_WEEKEND_INTERVAL_HOURS = _float("BTC_WEEKEND_INTERVAL_HOURS", BTC_TRADE_INTERVAL_HOURS)
