from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, String, Boolean,
                         JSON, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

import config

Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, default=config.STRIKE_NAS100_SYMBOL)
    direction = Column(String)          # "Long" / "Short"
    confidence = Column(Integer)
    reasoning = Column(String)

    entry_price = Column(Float)
    stop_loss_price = Column(Float)
    take_profit_price = Column(Float)
    leverage = Column(Integer)
    size = Column(Float)              # pozicna velkost v base-asset jednotkach (napr. NAS100 kontrakty)
    notional_usd = Column(Float)
    margin_usd = Column(Float)        # pozadovana marza (notional / leverage)

    strategy_id = Column(String, nullable=True)  # Strike bracket-order strategy_id

    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime)
    closed_at = Column(DateTime, nullable=True)

    status = Column(String, default="open")  # open | closed_by_exchange | closed_by_timeout | closed_by_safety | dry_run
    close_reason = Column(String, nullable=True)
    pnl_usd = Column(Float, nullable=True)

    dry_run = Column(Boolean, default=False)


class CycleLog(Base):
    """Zaznam KAZDEHO analytickeho cyklu - aj tych, kde sa neotvorila pozicia
    (rejected risk managerom, direction=none, chyba, alebo skipped lebo uz bezi
    ina pozicia). Sluzi na spatnu kontrolu rozhodnuti (dashboard, buduca
    kalibracia) aj ked ziadny Trade nevznikol."""
    __tablename__ = "cycle_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    live_price = Column(Float, nullable=True)
    ta = Column(JSON, nullable=True)
    cross_market = Column(JSON, nullable=True)
    session_data = Column(JSON, nullable=True)
    config_snapshot = Column(JSON, nullable=True)  # aktivne trading/risk nastavenia v case cyklu

    direction = Column(String, nullable=True)       # long | short | none
    confidence = Column(Integer, nullable=True)
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    reasoning = Column(String, nullable=True)

    outcome = Column(String)            # opened | rejected | error | skipped
    reject_reason = Column(String, nullable=True)

    trade_id = Column(Integer, nullable=True)  # ak outcome=opened, id v `trades`


_engine = create_engine(config.DATABASE_URL, future=True)
Base.metadata.create_all(_engine)
SessionLocal = sessionmaker(bind=_engine, future=True)


def get_session():
    return SessionLocal()
