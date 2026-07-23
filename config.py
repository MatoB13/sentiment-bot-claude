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

# --- Druhy a treti asset: NVDA (akcia) a ADA (krypto) - bezia v tom istom cykle
# ako NAS100 a zdielaju s nim cross-market/session makro fetch (viz assets.py,
# trade_cycle.run_all_cycles), ale maju uplne nezavisly risk/poziciu/rozhodnutie.
ENABLE_NVDA = _bool("ENABLE_NVDA", "true")
ENABLE_ADA = _bool("ENABLE_ADA", "true")

# Presny symbol/asset identifikator zisti cez strike_client.get_markets() - toto
# su len predpoklady podla existujuceho NAS100-USD pomenovacieho vzoru.
STRIKE_NVDA_SYMBOL = os.getenv("STRIKE_NVDA_SYMBOL", "NVDA-USD")
STRIKE_ADA_SYMBOL = os.getenv("STRIKE_ADA_SYMBOL", "ADA-USD")

# Min. confidence pre otvorenie obchodu - defaultne rovnake ako NAS100, ale
# nastavitelne zvlast (napr. ak by sa ADA/NVDA ukazali menej/viac predikovatelne).
NVDA_MIN_CONFIDENCE = _int("NVDA_MIN_CONFIDENCE", MIN_CONFIDENCE)
ADA_MIN_CONFIDENCE = _int("ADA_MIN_CONFIDENCE", MIN_CONFIDENCE)

NVDA_MARGIN_USD = _float("NVDA_MARGIN_USD", MARGIN_USD)
ADA_MARGIN_USD = _float("ADA_MARGIN_USD", MARGIN_USD)

# Nizsia paka nez NAS100 (40x) - NVDA aj ADA maju vyssiu vnutrodennu volatilitu,
# takze rovnaka paka by pri bezneho pohybe trhu znamenala vyssie riziko likvidacie.
NVDA_LEVERAGE = _int("NVDA_LEVERAGE", 10)
ADA_LEVERAGE = _int("ADA_LEVERAGE", 6)

# Sirsie SL/TP % nez NAS100 (0.4/0.6) - kalibrovane na typicku dennu volatilitu
# jednotlivej megacap akcie (NVDA) a krypto assetu (ADA), pri zachovani rovnakeho
# risk:reward pomeru (SL:TP = 1:1.5) ako pri NAS100. Priblizne 2.5x-3x sirsie SL/TP
# pre NVDA (jednotliva akcia je volatilnejsia nez index) a 4x-9x sirsie pre ADA
# (krypto je volatilnejsie nez akcie), s primerane nizsou pakou pre bezpecny buffer
# do likvidacie (~1/leverage).
NVDA_SL_PCT = _float("NVDA_SL_PCT", 1.5)
NVDA_TP_PCT = _float("NVDA_TP_PCT", 2.25)
ADA_SL_PCT = _float("ADA_SL_PCT", 3.5)
ADA_TP_PCT = _float("ADA_TP_PCT", 5.25)
