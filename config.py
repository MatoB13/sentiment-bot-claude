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

# DB
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///trades.db")

# Trading / risk
DRY_RUN = _bool("DRY_RUN", "true")
TRADE_INTERVAL_HOURS = _float("TRADE_INTERVAL_HOURS", 4)
MONITOR_INTERVAL_MINUTES = _float("MONITOR_INTERVAL_MINUTES", 10)
# Samostatny (tesnejsi) interval len pre watch_monitor.py - cenova podmienka sa
# oplati kontrolovat castejsie ako otvorene pozicie (tie uz chrani Strike-ov
# vlastny SL/TP bracket order v realnom case, nas position_monitor len
# dodatocne synchronizuje DB zaznam).
WATCH_INTERVAL_MINUTES = _float("WATCH_INTERVAL_MINUTES", 1)
POSITION_MAX_HOURS = _float("POSITION_MAX_HOURS", 24)
MIN_CONFIDENCE = _int("MIN_CONFIDENCE", 65)

# Fixny position sizing: kazdy obchod pouzije rovnaky margin a leverage
# (napr. $100 margin x 40x leverage = $4000 notional/buying power).
MARGIN_USD = _float("MARGIN_USD", 100)
LEVERAGE = _int("LEVERAGE", 40)

# Cielove SL/TP ako % od live ceny - Claude navrhuje konkretnu cenu v ramci
# tolerancie okolo tychto hodnot (viz risk_manager.py). Pri danom leverage
# to zodpoveda DEFAULT_SL_PCT*LEVERAGE % / DEFAULT_TP_PCT*LEVERAGE % pohybu na marzi.
DEFAULT_SL_PCT = _float("DEFAULT_SL_PCT", 0.4)
DEFAULT_TP_PCT = _float("DEFAULT_TP_PCT", 0.6)

# --- Druhy az siesty asset: NVDA (akcia), ADA (krypto), GOLD (komodita),
# WTI (ropa) a NIGHT (krypto, Midnight/Cardano) - bezia v tom istom cykle ako
# NAS100 a zdielaju s nim cross-market/session makro fetch (viz assets.py,
# trade_cycle.run_all_cycles), ale maju uplne nezavisly risk/poziciu/rozhodnutie.
# GOLD je zamerne pridany ako protivietor k prevazne risk-on smerovaniu
# NAS100/NVDA/ADA (safe-haven, opacna VIX polarita). WTI pridany 2026-07-31 ako
# vyraznejsie odlisny ticker od zvysnych troch (ropa ma iny driver - OPEC+/
# geopolitika/dopyt, NIE safe-haven ako zlato). NIGHT pridany v tom istom kroku.
#
# NVDA POZASTAVENE (2026-07-31, default zmeneny na false) - nahradzame ho
# WTI/NIGHT (cost-optimalizacia, 5 tickerov denne by bolo drahe zbiehat a
# zatial si na seba nezarobili). Historicke cycle_logs/trades ostavaju v DB a
# v monitor-web dashboarde, len sa nezapocitavaju do noveho web_search/Claude
# nakladu - viz trade_cycle._mark_disabled_assets pre "Pozastavene" oznacenie.
ENABLE_NVDA = _bool("ENABLE_NVDA", "false")
ENABLE_ADA = _bool("ENABLE_ADA", "true")
ENABLE_GOLD = _bool("ENABLE_GOLD", "true")
ENABLE_WTI = _bool("ENABLE_WTI", "true")
ENABLE_NIGHT = _bool("ENABLE_NIGHT", "true")

# Presny symbol/asset identifikator zisti cez strike_client.get_markets() - toto
# su len predpoklady podla existujuceho NAS100-USD pomenovacieho vzoru, okrem
# WTI-USD/NIGHT-USD ktore su priamo overene naozivo v /v2/markets (2026-07-31).
STRIKE_NVDA_SYMBOL = os.getenv("STRIKE_NVDA_SYMBOL", "NVDA-USD")
STRIKE_ADA_SYMBOL = os.getenv("STRIKE_ADA_SYMBOL", "ADA-USD")
STRIKE_GOLD_SYMBOL = os.getenv("STRIKE_GOLD_SYMBOL", "XAU-USD")
STRIKE_WTI_SYMBOL = os.getenv("STRIKE_WTI_SYMBOL", "WTI-USD")
STRIKE_NIGHT_SYMBOL = os.getenv("STRIKE_NIGHT_SYMBOL", "NIGHT-USD")

# Min. confidence pre otvorenie obchodu - defaultne rovnake ako NAS100, ale
# nastavitelne zvlast (napr. ak by sa niektory asset ukazal menej/viac predikovatelny).
NVDA_MIN_CONFIDENCE = _int("NVDA_MIN_CONFIDENCE", MIN_CONFIDENCE)
ADA_MIN_CONFIDENCE = _int("ADA_MIN_CONFIDENCE", MIN_CONFIDENCE)
GOLD_MIN_CONFIDENCE = _int("GOLD_MIN_CONFIDENCE", MIN_CONFIDENCE)
WTI_MIN_CONFIDENCE = _int("WTI_MIN_CONFIDENCE", MIN_CONFIDENCE)
NIGHT_MIN_CONFIDENCE = _int("NIGHT_MIN_CONFIDENCE", MIN_CONFIDENCE)

NVDA_MARGIN_USD = _float("NVDA_MARGIN_USD", MARGIN_USD)
ADA_MARGIN_USD = _float("ADA_MARGIN_USD", MARGIN_USD)
GOLD_MARGIN_USD = _float("GOLD_MARGIN_USD", MARGIN_USD)
WTI_MARGIN_USD = _float("WTI_MARGIN_USD", MARGIN_USD)
NIGHT_MARGIN_USD = _float("NIGHT_MARGIN_USD", MARGIN_USD)

# Nizsia paka nez NAS100 (40x) - vsetky maju vyssiu vnutrodennu volatilitu nez
# index, takze rovnaka paka by pri bezneho pohybe trhu znamenala vyssie riziko
# likvidacie. GOLD je menej volatilne nez NVDA/ADA, ale volatilnejsie nez index
# NAS100, takze paka je medzi NAS100 a NVDA. WTI podobne ako GOLD, o niecoo
# nizsie (ropa byva vnutrodenne volatilnejsia nez zlato). NIGHT_LEVERAGE=10 je
# MAX povolena paka na Strike pre tento symbol (margin_tiers strop, overene
# naozivo cez get_market('NIGHT-USD') 2026-07-31) - vedome zvolena aj napriek
# cerstvemu bezpecnostnemu incidentu (Wanchain bridge hack 2026-07-20).
NVDA_LEVERAGE = _int("NVDA_LEVERAGE", 10)
ADA_LEVERAGE = _int("ADA_LEVERAGE", 6)
GOLD_LEVERAGE = _int("GOLD_LEVERAGE", 20)
WTI_LEVERAGE = _int("WTI_LEVERAGE", 15)
NIGHT_LEVERAGE = _int("NIGHT_LEVERAGE", 10)

# Sirsie SL/TP % nez NAS100 (0.4/0.6) - kalibrovane na typicku dennu volatilitu
# jednotlivej megacap akcie (NVDA), krypto assetu (ADA), komodity (GOLD/WTI) a
# mladeho volatilneho krypto tokenu (NIGHT), pri zachovani rovnakeho
# risk:reward pomeru (SL:TP = 1:1.5) ako pri NAS100. Hodnoty pre WTI/NIGHT su
# (rovnako ako povodne NVDA/ADA/GOLD) len pociatocny odhad, nie empiricky
# backtestovane - prehodnotit po zozbierani realnych dat.
NVDA_SL_PCT = _float("NVDA_SL_PCT", 1.5)
NVDA_TP_PCT = _float("NVDA_TP_PCT", 2.25)
ADA_SL_PCT = _float("ADA_SL_PCT", 3.5)
ADA_TP_PCT = _float("ADA_TP_PCT", 5.25)
GOLD_SL_PCT = _float("GOLD_SL_PCT", 0.8)
GOLD_TP_PCT = _float("GOLD_TP_PCT", 1.2)
WTI_SL_PCT = _float("WTI_SL_PCT", 1.2)
WTI_TP_PCT = _float("WTI_TP_PCT", 1.8)
NIGHT_SL_PCT = _float("NIGHT_SL_PCT", 6.0)
NIGHT_TP_PCT = _float("NIGHT_TP_PCT", 9.0)

# --- Variabilny interval pre NAS100/NVDA/GOLD/WTI (NIE ADA/NIGHT - tie sa
# obchoduju 24/7, ziadne realne "off hours" pre ne neexistuju). Mimo trading
# hours a cez vikend podkladovy trh (akcia/futures) realne stoji alebo je
# velmi ticho (NVDA sa cez vikend vobec neobchoduje), takze hodinova analyza
# tych istych zastaralych dat je zbytocny naklad.
OFF_HOURS_INTERVAL_HOURS = _float("OFF_HOURS_INTERVAL_HOURS", 2)
WEEKEND_INTERVAL_HOURS = _float("WEEKEND_INTERVAL_HOURS", 6)
# 13-21 UTC pokryva NYSE cash session (9:30-16:00 ET) v oboch DST stavoch
# (EDT 13:30-20:00 UTC aj EST 14:30-21:00 UTC) bez potreby rieist DST prechody.
TRADING_HOURS_START_UTC = _int("TRADING_HOURS_START_UTC", 13)
TRADING_HOURS_END_UTC = _int("TRADING_HOURS_END_UTC", 21)

# --- Frekvencia dotazovania PER TICKER (2026-07-31) - predtym zdielane globalne
# (OFF_HOURS_INTERVAL_HOURS/WEEKEND_INTERVAL_HOURS vyssie) rovnako pre vsetky
# variable_interval assety. Kazdy ticker teraz ma vlastnu trojicu, nezavisle
# nastavitelnu, defaultne vsak zachovava PRESNE povodne (zdielane) spravanie -
# kazdy default sa odvija od globalnej hodnoty vyssie (alebo pre WTI/NIGHT od
# GOLD/ADA, ako bolo dohodnute). trade_interval_hours = interval POCAS trading
# hours (alebo VZDY pre 24/7 assety ADA/NIGHT, kde variable_interval=False).
NAS100_TRADE_INTERVAL_HOURS = _float("NAS100_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
NAS100_OFF_HOURS_INTERVAL_HOURS = _float("NAS100_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
NAS100_WEEKEND_INTERVAL_HOURS = _float("NAS100_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

NVDA_TRADE_INTERVAL_HOURS = _float("NVDA_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
NVDA_OFF_HOURS_INTERVAL_HOURS = _float("NVDA_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
NVDA_WEEKEND_INTERVAL_HOURS = _float("NVDA_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

ADA_TRADE_INTERVAL_HOURS = _float("ADA_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
ADA_OFF_HOURS_INTERVAL_HOURS = _float("ADA_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
ADA_WEEKEND_INTERVAL_HOURS = _float("ADA_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

GOLD_TRADE_INTERVAL_HOURS = _float("GOLD_TRADE_INTERVAL_HOURS", TRADE_INTERVAL_HOURS)
GOLD_OFF_HOURS_INTERVAL_HOURS = _float("GOLD_OFF_HOURS_INTERVAL_HOURS", OFF_HOURS_INTERVAL_HOURS)
GOLD_WEEKEND_INTERVAL_HOURS = _float("GOLD_WEEKEND_INTERVAL_HOURS", WEEKEND_INTERVAL_HOURS)

# WTI defaultne rovnake ako GOLD (dohodnute) - nezavisle prestavitelne cez
# vlastne WTI_* premenne, ak sa neskor ukaze potreba ineho intervalu.
WTI_TRADE_INTERVAL_HOURS = _float("WTI_TRADE_INTERVAL_HOURS", GOLD_TRADE_INTERVAL_HOURS)
WTI_OFF_HOURS_INTERVAL_HOURS = _float("WTI_OFF_HOURS_INTERVAL_HOURS", GOLD_OFF_HOURS_INTERVAL_HOURS)
WTI_WEEKEND_INTERVAL_HOURS = _float("WTI_WEEKEND_INTERVAL_HOURS", GOLD_WEEKEND_INTERVAL_HOURS)

# NIGHT defaultne rovnake ako ADA (dohodnute) - obe su 24/7 krypto bez trading-hours rozlisenia.
NIGHT_TRADE_INTERVAL_HOURS = _float("NIGHT_TRADE_INTERVAL_HOURS", ADA_TRADE_INTERVAL_HOURS)
NIGHT_OFF_HOURS_INTERVAL_HOURS = _float("NIGHT_OFF_HOURS_INTERVAL_HOURS", ADA_OFF_HOURS_INTERVAL_HOURS)
NIGHT_WEEKEND_INTERVAL_HOURS = _float("NIGHT_WEEKEND_INTERVAL_HOURS", ADA_WEEKEND_INTERVAL_HOURS)
