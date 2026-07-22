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
