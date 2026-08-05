from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, String, Boolean,
                         JSON, UniqueConstraint, create_engine, inspect, text)
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

    # Presne (nie odhadovane) hodnoty z burzy - viz position_monitor._lookup_exact_close.
    # entry_fill_price/close_fill_price su velkostou-vazeny priemer VSETKYCH
    # fills danej strany (order sa moze vykonat po castiach). fees_usd = sucet
    # poplatkov (commission) za VSETKY fills (entry aj exit) daneho obchodu.
    entry_fill_price = Column(Float, nullable=True)
    close_fill_price = Column(Float, nullable=True)
    fees_usd = Column(Float, nullable=True)

    dry_run = Column(Boolean, default=False)

    # Kill-switch: monitor-web "Zavriet" tlacidlo zapise sem timestamp namiesto
    # priameho volania Strike (API kluce zostavaju LEN tu vo worker-i, nikdy vo
    # verejne dostupnom Vercel/monitor-web). watch_monitor.py (1x/min) takuto
    # ziadost najde a zatvori rovnakym mechanizmom ako existujuci
    # POSITION_MAX_HOURS timeout force-close - viz watch_monitor._check_manual_close_requests.
    manual_close_requested_at = Column(DateTime, nullable=True)


class CycleLog(Base):
    """Zaznam KAZDEHO analytickeho cyklu - aj tych, kde sa neotvorila pozicia
    (rejected risk managerom, direction=none, chyba, alebo skipped lebo uz bezi
    ina pozicia). Sluzi na spatnu kontrolu rozhodnuti (dashboard, buduca
    kalibracia) aj ked ziadny Trade nevznikol."""
    __tablename__ = "cycle_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    symbol = Column(String, nullable=True)  # ktory asset (NAS100-USD/NVDA-USD/ADA-USD)
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
    web_search_log = Column(JSON, nullable=True)  # [{"query", "sources": [{"title","url","page_age"}]}]
    key_assumptions = Column(String, nullable=True)  # kluc. predpoklady tohto rozhodnutia - overuju sa dalsi cyklus
    # Volitelne - Claude sem napise strucny popis, ak vstupne data pre tento
    # cyklus vyzeraju podozrivo/nekonzistentne (zastarana cena, chybajuci/nulovy
    # TA udaj, protichodny cross-market snapshot a pod.) - nezavisle od
    # obchodneho rozhodnutia (dostane sa aj pri direction=none). Zobrazuje sa
    # v Historii signalov, aby taketo problemy nezanikli v strohom reasoning.
    data_issue = Column(String, nullable=True)

    # Ak direction=none, ale Claude vidi konkretnu uroven cakajucu na potvrdenie
    # (napr. retest), sem si ulozi cenu + smer na sledovanie. watch_monitor.py
    # kazdych MONITOR_INTERVAL_MINUTES kontroluje live cenu voci TOMUTO
    # (najnovsiemu pre dany symbol) zaznamu - ak sa splni, spusti mimoriadny
    # (platny) Claude cyklus len pre tento asset. Novy CycleLog z takeho behu sa
    # automaticky stane najnovsim zaznamom, cim stary watch prirodzene "zanikne"
    # (poller vzdy pozera len na najnovsi riadok pre dany symbol).
    watch_price = Column(Float, nullable=True)
    watch_direction = Column(String, nullable=True)  # "above" | "below"

    # Ak outcome=position_check (uz otvorena pozicia - viz
    # trade_cycle._run_position_health_check), Claudeho odporucanie: "hold"
    # (predpoklady drzia) alebo "consider_closing" (pouzivatel by mal zvazit
    # rucne zatvorenie cez kill-switch - bot sam pozicie nezatvara).
    health_recommendation = Column(String, nullable=True)
    # "favorable" | "unfavorable" | "uncertain" - ci Claude ocakava, ze sa cena
    # bude dalej hybat V PROSPECH otvorenej pozicie alebo PROTI nej.
    health_expected_direction = Column(String, nullable=True)

    outcome = Column(String)            # opened | rejected | error | skipped | disabled | position_check
    reject_reason = Column(String, nullable=True)

    trade_id = Column(Integer, nullable=True)  # ak outcome=opened, id v `trades`


class DailyRetrospective(Base):
    """Denny AUDIT zaznam - vygenerovany RAZ za den (pri prvom cykle po polnoci
    UTC pre dany asset), pokryva PREDCHADZAJUCI (uz uplynuly) den. Ziadne extra
    Claude volanie navyse: stats sa vypocitaju cisto v kode (zdarma, cez
    yfinance - viz retrospective.py) a Claude k nim napise izolovanu poznamku
    LEN k tomuto konkretnemu dnu (volitelny 'daily_reflection' vystup na
    submit_trade_decision nastroji), v ramci uz aj tak planovaneho prveho
    cyklu dna. Tato poznamka sama o sebe NEVSTUPUJE do promptov (na rozdiel od
    predchadzajuceho navrhu) - slúži len ako historicky zaznam v UI. Do
    promptov vstupuje priebezne aktualizovane RollingRetrospective.summary
    nizsie - viz trade_cycle._get_retrospective_context."""
    __tablename__ = "daily_retrospectives"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    for_date = Column(String, nullable=False)  # 'YYYY-MM-DD' (UTC) - den, ktory stats pokryvaju
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stats = Column(JSON, nullable=True)         # surove vypocitane cisla (viz retrospective.py)
    reflection = Column(String, nullable=True)  # Claude-ova izolovana poznamka LEN k tomuto dnu


class RollingRetrospective(Base):
    """Priebezne (vzdy AKTUALNE) zhrnutie skusenosti bota - JEDEN riadok na
    asset, ktory sa kazdy den PREPISUJE (nie pripaja) v ramci toho isteho
    bezplatneho prveho cyklu dna ako DailyRetrospective vyssie. Claude dostane
    aktualne 'summary' + vcerajsie cerstve stats a vrati AKTUALIZOVANE zhrnutie
    (potvrdi pretrvavajuce vzory, uprav tie vyvratene novymi datami, zahod uz
    nepodstatne detaily) - viz submit_trade_decision 'summary_reflection'.
    Tento (nie DailyRetrospective.reflection) je to, co sa realne prenasa do
    VSETKYCH cyklov ako 'Priebezne zhrnutie' - viz
    trade_cycle._get_retrospective_context a claude_analyst._build_user_prompt.
    Zamerne ohranicene (system prompt instruuje Claude drzat to strucne), aby
    to casom nenarastalo donekonecna a nezvysovalo token cost kazdeho cyklu."""
    __tablename__ = "rolling_retrospectives"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, unique=True)
    summary = Column(String, nullable=True)
    based_through_date = Column(String, nullable=True)  # 'YYYY-MM-DD' - posledny den zapracovany do summary
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PriceBar(Base):
    """Vlastne hodinove OHLC sviecky zostavene zo Strike mark_price (poller
    kazdu minutu - viz price_poller.py). Primarny zdroj TA dat namiesto
    yfinance: yfinance pre NAS100/NVDA/GOLD futures/akciu mimo obchodnych
    hodin a cez vikend proste zamrzne (trh je zatvoreny), zatial co Strike
    perpy obchoduju nonstop - takze Claude by inak videl 'plochy' graf presne
    vtedy, ked sa realna obchodovatelna cena hybe. yfinance ostava fallback
    (viz market_data.get_price_history), ak vlastne data chybaju/su zastarale
    (napr. poller bol dlhsie mimo prevadzky)."""
    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("symbol", "hour_start", name="uq_price_bars_symbol_hour"),)

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    hour_start = Column(DateTime, nullable=False, index=True)  # UTC, zaokruhlene na celu hodinu
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)


def _ensure_columns(engine) -> None:
    """create_all() vytvori len chybajuce TABULKY, nikdy nepridá stlpec do uz
    existujucej tabulky. Toto je poor-man's migration: pri kazdom starte
    porovna model so skutocnou DB a chybajuce stlpce dopichne cez ALTER TABLE.
    Bezpecne pre pridavanie novych nullable stlpcov (nas jediny use-case)."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # cela tabulka je nova - tu uz vytvoril create_all()
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            col_type = column.type.compile(engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
            print(f"[db] Pridany chybajuci stlpec {table.name}.{column.name} ({col_type})")


_engine = create_engine(config.DATABASE_URL, future=True)
Base.metadata.create_all(_engine)
_ensure_columns(_engine)
SessionLocal = sessionmaker(bind=_engine, future=True)


def get_session():
    return SessionLocal()
