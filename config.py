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

# --- Druhy, treti a stvrty asset: NVDA (akcia), ADA (krypto) a GOLD (komodita) -
# bezia v tom istom cykle ako NAS100 a zdielaju s nim cross-market/session makro
# fetch (viz assets.py, trade_cycle.run_all_cycles), ale maju uplne nezavisly
# risk/poziciu/rozhodnutie. GOLD je zamerne pridany ako protivietor k prevazne
# risk-on smerovaniu NAS100/NVDA/ADA (safe-haven, opacna VIX polarita).
ENABLE_NVDA = _bool("ENABLE_NVDA", "true")
ENABLE_ADA = _bool("ENABLE_ADA", "true")
ENABLE_GOLD = _bool("ENABLE_GOLD", "true")

# Presny symbol/asset identifikator zisti cez strike_client.get_markets() - toto
# su len predpoklady podla existujuceho NAS100-USD pomenovacieho vzoru.
STRIKE_NVDA_SYMBOL = os.getenv("STRIKE_NVDA_SYMBOL", "NVDA-USD")
STRIKE_ADA_SYMBOL = os.getenv("STRIKE_ADA_SYMBOL", "ADA-USD")
STRIKE_GOLD_SYMBOL = os.getenv("STRIKE_GOLD_SYMBOL", "XAU-USD")

# Min. confidence pre otvorenie obchodu - defaultne rovnake ako NAS100, ale
# nastavitelne zvlast (napr. ak by sa niektory asset ukazal menej/viac predikovatelny).
NVDA_MIN_CONFIDENCE = _int("NVDA_MIN_CONFIDENCE", MIN_CONFIDENCE)
ADA_MIN_CONFIDENCE = _int("ADA_MIN_CONFIDENCE", MIN_CONFIDENCE)
GOLD_MIN_CONFIDENCE = _int("GOLD_MIN_CONFIDENCE", MIN_CONFIDENCE)

NVDA_MARGIN_USD = _float("NVDA_MARGIN_USD", MARGIN_USD)
ADA_MARGIN_USD = _float("ADA_MARGIN_USD", MARGIN_USD)
GOLD_MARGIN_USD = _float("GOLD_MARGIN_USD", MARGIN_USD)

# Nizsia paka nez NAS100 (40x) - vsetky tri maju vyssiu vnutrodennu volatilitu
# nez index, takze rovnaka paka by pri bezneho pohybe trhu znamenala vyssie
# riziko likvidacie. GOLD je menej volatilne nez NVDA/ADA, ale volatilnejsie nez
# index NAS100, takze paka je medzi NAS100 a NVDA.
NVDA_LEVERAGE = _int("NVDA_LEVERAGE", 10)
ADA_LEVERAGE = _int("ADA_LEVERAGE", 6)
GOLD_LEVERAGE = _int("GOLD_LEVERAGE", 20)

# Sirsie SL/TP % nez NAS100 (0.4/0.6) - kalibrovane na typicku dennu volatilitu
# jednotlivej megacap akcie (NVDA), krypto assetu (ADA) a komodity (GOLD), pri
# zachovani rovnakeho risk:reward pomeru (SL:TP = 1:1.5) ako pri NAS100.
NVDA_SL_PCT = _float("NVDA_SL_PCT", 1.5)
NVDA_TP_PCT = _float("NVDA_TP_PCT", 2.25)
ADA_SL_PCT = _float("ADA_SL_PCT", 3.5)
ADA_TP_PCT = _float("ADA_TP_PCT", 5.25)
GOLD_SL_PCT = _float("GOLD_SL_PCT", 0.8)
GOLD_TP_PCT = _float("GOLD_TP_PCT", 1.2)
