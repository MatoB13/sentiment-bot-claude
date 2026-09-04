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
    # 2026-09-04 - TP je odteraz Claudov (viz risk_manager.resolve_sl_tp_distances).
    # Sem sa uklada TP podla STAREHO vzorca (SL x pomer tp_pct/sl_pct tickera),
    # aby sa po ~30 obchodoch dalo zmerat, ci Claudov TP porazil pomer tickera.
    # NULL pre obchody spred tejto zmeny (tam bol take_profit_price = mechanicky).
    take_profit_price_mechanical = Column(Float, nullable=True)
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

    # Nastavene, ked bol pre toto zatvorenie uz spusteny mimoriadny "post-close
    # review" cyklus (viz position_monitor._check_and_queue_review) - zabrani
    # opakovanemu spusteniu pri kazdom dalsom position_monitor tiku, kedze
    # close_reason raz vyrieseny uz zostava v DB navzdy.
    post_close_review_triggered_at = Column(DateTime, nullable=True)

    # Nastavene, ked bola pre toto zatvorenie uz odoslana Discord notifikacia
    # (viz position_monitor._check_and_queue_close_notification) - rovnaky
    # dedup vzor ako post_close_review_triggered_at vyssie, nezavisly od neho
    # (iny filter dovodov - viz _NOTIFY_CLOSE_REASONS, manual_kill_switch je
    # tu ZAMERNE vynechany, na rozdiel od review-triggeru).
    close_notified_at = Column(DateTime, nullable=True)

    # 2026-08-31 (UNITREE #155 incident, na ziadost pouzivatela) - rovnaky
    # dedup vzor pre notifikaciu o OTVORENI (predtym ziadny - discord_client.
    # notify_trade_opened sa volalo priamo, bez perzistovaneho stavu, takze
    # jednorazove zlyhanie webhooku bolo navzdy neviditelne stratene). NA
    # ROZDIEL od close_notified_at (a povodnej verzie post_close_review_
    # triggered_at) sa toto nastavuje AZ PO potvrdenom uspesnom odoslani
    # (discord_client.notify_trade_opened teraz vracia True/False) - nie
    # hned pri pokuse - takze self-heal (position_monitor.
    # _backfill_missing_open_notifications) vie spolahlivo rozlisit
    # "uspesne odoslane" od "zlyhalo, skus znova".
    open_notified_at = Column(DateTime, nullable=True)

    # KEDY sa ma spustit event-driven SL/TP grid-search prepocet pre tento
    # ticker (viz position_monitor._check_and_queue_recompute +
    # sl_grid_backtest.recompute_symbol) - nastavene HNED pri zatvoreni na
    # opened_at + POSITION_MAX_HOURS + buffer, NIE na cas zatvorenia (2026-08-19,
    # oprava po naslednej diere: okamzity prepocet HNED po skorsom zatvoreni
    # - napr. SL po 2h - by pre SIRSIE hypoteticke SL/TP kombinacie pouzival
    # LEN neuplnu cast 24h cenovej historie, viz sl_grid_backtest._prepare_trade
    # guard). position_monitor._fire_due_recomputes kontroluje na KAZDOM tiku,
    # ci uz tento cas nastal, nezavisle od toho, ci prave teraz nieco zatvara.
    recompute_due_at = Column(DateTime, nullable=True)

    # Nastavene, KED bol tento naplanovany prepocet (vyssie) uz skutocne
    # odpaleny - dedup, aby sa pri kazdom dalsom tiku po due_at neopakoval.
    # NEZAVISLY od close_reason filtra (kazde uzavretie pocita do vzorky, bez
    # ohladu na to, ci ten isty dovod spusta aj review).
    post_close_recompute_triggered_at = Column(DateTime, nullable=True)

    # Kedy naposledy plny (plateny) Claude position-health-check cyklus pre
    # TUTO otvorenu poziciu naozaj prebehol (nie len mechanicka kontrola) - viz
    # trade_cycle._run_position_health_check cooldown, 2026-08-17. Perzistovane
    # v DB (nie len v pamati) zamerne - na rozdiel od watch_monitor "hot" okna
    # tu chceme, aby cooldown prezil aj restart workera pocas drzania pozicie.
    last_health_escalation_at = Column(DateTime, nullable=True)

    # 2026-08-27 (na ziadost pouzivatela, po ADA #90 incidente) - unrealized_pnl_pct
    # V CASE poslednej eskalacie vyssie. Cooldown zabranuje OPAKOVANEJ eskalacii
    # na ten isty, uz raz posudeny signal - ale pri #90 pozicia MEDZI eskalaciami
    # dalej stratila hodnotu (2h bez Claude pohladu, kym cooldown nevyprsal) a
    # strata bola vacsia, nez musela byt. Ak sa P&L od tohto ulozeneho bodu
    # zhorsi o dalsich config.HEALTH_CHECK_COOLDOWN_BYPASS_WORSENING_FRACTION
    # podielu SL vzdialenosti, cooldown sa obide (viz trade_cycle.
    # _run_position_health_check) - novy, HORSI fakt, nie opakovanie stareho.
    last_health_escalation_pnl_pct = Column(Float, nullable=True)

    # 2026-08-31 - DRUH triggeru poslednej platenej eskalacie: "macro"/"trend"/"loss"
    # (viz trade_cycle._mechanical_health_escalation). Cooldown bol dovtedy
    # per-POZICIA, takze eskalacia kvoli strate umlcala aj eskalaciu kvoli obratu
    # trendu o hodinu neskor - hoci to je NOVY fakt. Teraz cooldown potlaca len
    # OPAKOVANIE TOHO ISTEHO druhu, co bol vzdy jeho zamer (ADA incident 2026-08-17
    # bol o opakovanej blizkosti SL, nie o miesani roznych signalov).
    last_health_escalation_kind = Column(String, nullable=True)
    # 2026-08-31 (na ziadost pouzivatela) - stav cenoveho pasma V CASE VSTUPU
    # (viz price_range.compute_price_range). Ulozene priamo na trade, nie
    # dohladavane spatne z cycle_logs.ta, aby to prezilo aj self-heal review
    # spusteny o hodiny neskor, ked uz je aktualne pasmo uplne ine.
    # Bez tohto polia by post-close review ani retrospektiva nevedeli odlisit
    # fade vstup (na okraji pasma, proti pohybu) od bezneho momentum vstupu -
    # a teda by sa z fade obchodov nemali ako poucit.
    entry_price_range = Column(JSON, nullable=True)


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

    # Zdrojova telemetria (2026-08-19, na ziadost pouzivatela) - "Zdroje pre
    # rozhodovanie" tab v dashboarde z toho pocita % vyuzitia kazdeho zdroja
    # za poslednych 24h. web_search_log vyssie uz ma vsetko potrebne pre
    # domeny navstivene cez web_search (Bloomberg/CNBC/atd.) - tieto tri
    # stlpce doplnaju STRUKTUROVANE zdroje, ktore sa doteraz po pouziti v
    # prompte jednoducho zahodili (Marketaux/social/CoinMarketCal), takze
    # spatne sa da tento matica pocitat LEN odteraz dopredu, nie retroaktivne.
    # None = zdroj sa pre tento asset vobec nekonfiguruje (napr. marketaux_query
    # chyba) - odlisuje sa od False (nakonfigurovany, ale zlyhal/prazdny).
    # 2026-09-03 (na ziadost pouzivatela) - CO tento cyklus vyvolalo. Doteraz
    # sa to dalo len ODHADOVAT z triggered_by_watch/triggered_by_macro_event a
    # zvysok sa rataľ ako "planovany", co bolo nepresne: health check spusteny
    # minutovym pollerom vyzeral rovnako ako bezny planovany cyklus (MINIMAX
    # 3.9. - 14 takych behov sa v dashboarde ukazalo ako "planovane").
    #
    # Definicia podla pouzivatela: PLANOVANY je ten, o ktorom rozhodol
    # planovaci cyklus (slot + interval) - vratane health checku, ktory v nom
    # zbehne, ked je otvorena pozicia. Vsetko ostatne je mimoriadne.
    #
    #   "scheduled"   - run_all_cycles / _is_due (slotova mriezka)
    #   "watch"       - splnena watch_price uroven (watch_monitor)
    #   "macro"       - makro udalost (watch_monitor)
    #   "fast_health" - minutovy health poller (31.8.-4.9., ODSTRANENY - len historia)
    #   "post_close"  - review po zatvoreni pozicie (position_monitor)
    #
    # NULL = riadok spred zavedenia stlpca. Historia sa da len ODHADNUT (viz
    # scratchpad backfill) - u starych riadkov sa fast_health od scheduled
    # spolahlivo odlisit nedá.
    trigger_source = Column(String, nullable=True)

    # 2026-09-04 (bod 6 auditu) - verdikt LACNEHO SKENU, ktory bezal pred plnym
    # cyklom (viz claude_analyst.triage + config.TRIAGE_MODE). JSON:
    #   {"worth_full_look": bool, "attention": 0-100, "reason": str,
    #    "mode": "shadow"|"active", "usage": {...}, "watch_price": ..., ...}
    # NULL = sken nebezal (rezim off, alebo mimoriadny/health cyklus).
    # V rezime "shadow" je vyplneny AJ na riadku plneho cyklu - presne preto, aby
    # sa dal verdikt skenu porovnat so skutocnym vysledkom toho isteho cyklu.
    # Pri outcome="triage_skip" (len rezim "active") je to jediny obsah cyklu.
    triage = Column(JSON, nullable=True)

    marketaux_used = Column(Boolean, nullable=True)
    social_post_count = Column(Integer, nullable=True)
    coinmarketcal_used = Column(Boolean, nullable=True)

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

    # 2026-08-21 (na ziadost pouzivatela) - PRECO cyklus caka na watch_price/
    # watch_direction vyssie (1-2 vety), analogicke key_assumptions. Ked sa
    # tento watch neskor spusti, _get_watch_set_context v trade_cycle.py ho
    # najde a vlozi do promptu NASLEDUJUCEHO (watch-triggered) cyklu - inak ten
    # cyklus nema ako vediet, PRECO predtym nechcel vstupit, a nemoze sa k
    # tomu vyslovne vyjadrit (viz diskusia s pouzivatelom o ZEC 09:33->09:34
    # rozpore: "zly risk/reward" -> o minutu LONG, bez zmienky o zmene nazoru).
    watch_rationale = Column(String, nullable=True)

    # Pridane 2026-08-15 - ak direction=long/short A confidence padla do pasma
    # tesne pod prahom na otvorenie (config.WATCH_CONFIDENCE_MARGIN), Claude
    # sem VZDY explicitne napise, pri akej cene by (cisto technicky) confidence
    # prekrocila prah, alebo ze takú cenu nevie odhadnut. Skutocna sledovana
    # uroven ide do watch_price/watch_direction vyssie - rovnaky trigger
    # mechanizmus ako pri direction=none, watch_monitor.py nerozlisuje odkial
    # watch_price pochadza (viz claude_analyst.py DECISION_TOOL).
    confidence_threshold_note = Column(String, nullable=True)

    # Volitelny DRUHY (opacny) watch par - pre genuinne obojstranne
    # neisty/range-bound setup, kde by ROVNAKO relevantne potvrdil aj breakout
    # hore aj breakdown dole (napr. "nad X = long, pod Y = short"). Rovnaka
    # semantika ako watch_price/watch_direction vyssie, len druha nezavisla
    # podmienka - watch_monitor._is_triggered sa vola pre oba pary.
    watch_price_2 = Column(Float, nullable=True)
    watch_direction_2 = Column(String, nullable=True)  # "above" | "below"

    # Ak outcome=position_check (uz otvorena pozicia - viz
    # trade_cycle._run_position_health_check), Claudeho odporucanie: "hold"
    # (predpoklady drzia) alebo "consider_closing" (pouzivatel by mal zvazit
    # rucne zatvorenie cez kill-switch - bot sam pozicie nezatvara).
    health_recommendation = Column(String, nullable=True)
    # "favorable" | "unfavorable" | "uncertain" - ci Claude ocakava, ze sa cena
    # bude dalej hybat V PROSPECH otvorenej pozicie alebo PROTI nej.
    health_expected_direction = Column(String, nullable=True)
    # EXPERIMENTALNE (2026-08-17, na ziadost pouzivatela) - 0-100, LEN ked
    # health_recommendation="consider_closing": ako velmi si je Claude isty,
    # ze zatvorenie PRAVE TERAZ by bolo spravne rozhodnutie (nie vseobecna
    # obchodna istota). ZATIAL sa LEN loguje, nespusta ziadnu akciu - cielom
    # je nazbierat data na buducu kalibraciu, kym sa tomuto skore niekedy v
    # buducnosti prizna skutocna moznost poziciu (cez novy watcher) zatvorit.
    close_confidence = Column(Integer, nullable=True)

    # Ak tento cyklus je "post-close review" (mimoriadny beh spusteny hned po
    # TP/timeout/manual zatvoreni - viz position_monitor + trade_cycle.
    # run_triggered_check), id PREDTYM zatvoreneho Trade, ku ktoremu sa
    # closed_trade_reflection vztahuje. NEZAMIENAT s `trade_id` vyssie - to je
    # NOVY obchod otvoreny (ak vobec) V RAMCI TOHTO cyklu.
    reviewed_trade_id = Column(Integer, nullable=True)
    closed_trade_reflection = Column(String, nullable=True)

    # 2026-08-19 (na ziadost pouzivatela) - vyplnene LEN spolu s closed_trade_reflection,
    # explicitny verdikt k SL/TP TEJTO konkretnej pozicie: bolo spravne / mal sa
    # pouzit niektory kalibracny kandidat (viz SlTpBacktestCandidate)
    # / uplne iny navrh s TECHNICKYM (nie len empirickym) zdovodnenim.
    sl_tp_calibration_verdict = Column(String, nullable=True)

    # Ak tento cyklus bol vyvolany mimoriadne kvoli PRAVE zverejnenej makro
    # udalosti (FOMC/CPI/NFP - viz macro_calendar.py + watch_monitor.
    # _check_macro_events), nazov tej udalosti (napr. "CPI"). None pre bezne
    # naplanovane cykly.
    triggered_by_macro_event = Column(String, nullable=True)

    # True ak tento cyklus vyvolal splneny watch_price/watch_direction (viz
    # watch_monitor.check_watch_triggers) - 2026-08-19, na ziadost pouzivatela
    # po HYPE zacykleni (watch sa opakovane spustal, Claude vzdy 'none' +
    # novy tesny watch, bez toho aby o tom vedel, ze uz je to Nty raz za
    # sebou). Umoznuje presne (nie odhadovane) spocitat _get_watch_retrigger_streak
    # v trade_cycle.py, na rozdiel od macro_event vyssie (string nazov
    # udalosti) tu staci boolean - watch nema "nazov", len fakt ze sa spustil.
    triggered_by_watch = Column(Boolean, nullable=True)

    # outcome: opened | rejected | error | skipped | disabled | position_check |
    #          evaluation_only | skipped_concurrent_cycle | triage_skip
    outcome = Column(String)
    reject_reason = Column(String, nullable=True)

    trade_id = Column(Integer, nullable=True)  # ak outcome=opened, id v `trades`

    # Pridane 2026-08-15 - token usage tohto Claude volania (viz claude_analyst.
    # _call_claude), TRVALO ulozene namiesto len vypisania do Railway logu -
    # dovod: logy maju retenciu len ~2h, takze porovnanie nakladov naprieč dnami
    # (napr. efekt ADA_EFFORT=xhigh testu) sa z nich spatne nedalo zrekonstruovat.
    # Ukladá sa PRE KAZDY cyklus vratane position_check (health check bezi
    # rovnaky plny cyklus ako otvaracie rozhodnutie - rovnaky system prompt,
    # web_search, aj effort).
    usage_input_tokens = Column(Integer, nullable=True)
    usage_cache_write_tokens = Column(Integer, nullable=True)
    usage_cache_read_tokens = Column(Integer, nullable=True)
    usage_output_tokens = Column(Integer, nullable=True)
    # 2026-09-04 - z usage_cache_write_tokens vydelena 1-HODINOVA cast.
    #
    # PRECO: cache write sa uctuje INAK podla TTL - 5-minutovy 1.25x zakladnu
    # input sadzbu, 1-hodinovy 2x. System prompt cachujeme s ttl="1h" (viz
    # claude_analyst._system_prompt_blocks), user sprava default 5 min. Dashboard
    # dovtedy uctoval vsetko jednotne 1.25x, takze kazdy cyklus podhodnocoval
    # asi o $0.008 (~4 % tokenoveho uctu). API rozpad vracia v
    # usage.cache_creation.ephemeral_1h_input_tokens - dovtedy sme ho zahadzovali
    # a spatne sa dopocitat neda.
    #
    # usage_cache_write_tokens ostava CELKOM (5m + 1h), aby stare riadky nemenili
    # vyznam; tento stlpec je jeho PODMNOZINA.
    usage_cache_write_1h_tokens = Column(Integer, nullable=True)
    effort = Column(String, nullable=True)  # "" (default)/"high"/"xhigh"/"max" - viz assets.py


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

    # 2026-09-01 - mikroštruktura z /v2/markets, ktoru poller uz aj tak kazdu
    # minutu stahuje, len ju doteraz zahadzoval. Ukladaju sa ako PRIEMER za
    # hodinu (sucet + pocet vzoriek, delenie az pri citani) - jedna nahodna
    # hodnota z konca hodiny by bola prilis suma na to, aby sa z nej dalo nieco
    # citat, a priemer sa da pocitat priebezne bez drzania celej vzorky.
    #
    # Ciel (dohodnute s pouzivatelom 2026-09-01): najprv MERAT, az potom
    # pripadne stavat trigger. Objem by bol lepsi kandidat, ale Strike ho vobec
    # nevracia a externe zdroje pokryvaju len cast tickerov - toto pokryva 100 %
    # a nestoji ziadne volanie navyse.
    spread_pct_sum = Column(Float, nullable=True)      # (ask-bid)/mark * 100
    book_imbalance_sum = Column(Float, nullable=True)  # (bid_size-ask_size)/(bid_size+ask_size), -1..1
    premium_pct_sum = Column(Float, nullable=True)     # (mark-index)/index * 100
    micro_samples = Column(Integer, nullable=True)     # delitel pre vsetky tri vyssie
    # Kedy bol tento riadok naposledy dotknuty pollerom (2026-08-15) - NA ROZDIEL
    # od hour_start (vzdy zaokruhlene na zaciatok hodiny) toto je skutocny cas
    # posledneho 1-min tiku. Pridane po tom, co monitor-web zobrazoval "live"
    # nerealizovane PnL s casovkou hour_start, co posobilo (mylne) ako hodinu
    # stare data, hoci close sa priebezne aktualizuje kazdu minutu - viz
    # nas100-monitor-web computeUnrealizedPnl.
    updated_at = Column(DateTime, nullable=True)


class FundingRateBar(Base):
    """Vlastna hodinova historia AKTUALNEJ trhovej funding rate (2026-08-15) -
    NEZAVISLA od FundingPayment nizsie (ktora zaznamenava len REALIZOVANE platby
    za skutocne drzane pozicie, teda nic pre tickery/obdobia bez otvorenej
    pozicie). Zdroj: /v2/markets['funding_rate'] - ROVNAKY bulk GET call, aky uz
    price_poller.poll_prices() robi kvoli cene, takze zber je bez dodatocneho
    nakladu. Pouziva sa ako TA vstup do promptu (viz market_data.get_market_snapshot
    -> claude_analyst _FUNDING_NOTE) aj pre graf v monitor-web Konfiguracia tabe."""
    __tablename__ = "funding_rate_bars"
    __table_args__ = (UniqueConstraint("symbol", "hour_start", name="uq_funding_rate_bars_symbol_hour"),)

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    hour_start = Column(DateTime, nullable=False, index=True)  # UTC, zaokruhlene na celu hodinu
    funding_rate = Column(Float, nullable=False)


class FundingPayment(Base):
    """Periodicke funding platby za drzanie perpetual pozicie na Strike
    (kladne amount = prijate, zaporne = zaplatene) - viz strike_client.
    get_funding_history() + funding_tracker.py. UPLNE NEZAVISLE od Trade/
    fillov: /v2/history/fill neobsahuje funding vobec (overene 2026-08-15,
    kvoli rozdielu medzi nasim trackovanym PnL a Strike leaderboardom).
    strike_id = Strike-ove vlastne id zaznamu, unique kluc na dedup pri
    kazdom pollovani (viz funding_tracker.poll_new)."""
    __tablename__ = "funding_payments"

    id = Column(Integer, primary_key=True)
    strike_id = Column(Integer, unique=True, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    position_side = Column(String, nullable=True)  # "Long" | "Short"
    position_size = Column(Float, nullable=True)
    funding_rate = Column(Float, nullable=True)
    amount = Column(Float, nullable=False)  # signed - kladne=prijate, zaporne=zaplatene
    occurred_at = Column(DateTime, nullable=False, index=True)  # Strike-ov timestamp
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TriggeredMacroEvent(Base):
    """Zaznamenava, ktore makro udalosti (FOMC/CPI/NFP - viz macro_calendar.py)
    uz spustili mimoriadny cyklus, aby sa ta ista udalost nespustala opakovane
    na kazdom dalsom watch_monitor tiku po jej case - viz
    watch_monitor._check_macro_events. DB (nie in-memory flag), aby to
    prezilo aj redeploy procesu."""
    __tablename__ = "triggered_macro_events"

    id = Column(Integer, primary_key=True)
    event_key = Column(String, nullable=False, unique=True)  # napr. "CPI_2026-08-12"
    triggered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TriggeredWatch(Base):
    """Zaznamenava KAZDY watch-triggered mimoriadny cyklus (viz
    watch_monitor.check_watch_triggers - cenova podmienka watch_price/
    watch_direction splnena) - na rozdiel od TriggeredMacroEvent vyssie tu
    NEIDE o dedup jednej konkretnej udalosti (cenova podmienka sa moze
    splnit opakovane), ale o POCITADLO za posledny hodinu na asset (viz
    config.WATCH_TRIGGER_MAX_PER_HOUR) - bez neho by sa mohla rovnaka
    cenova hranica (alebo tesne nova, znova nastavena kazdym dalsim
    mimoriadnym cyklom) spustat neobmedzene casto. Kazdy watch-trigger je
    per-asset nezavisly (na rozdiel od makro udalosti, ktore su casto
    zdielany "vsetky assety" burst), preto sa limit pocita OSOBITNE pre
    kazdy symbol, nie ako jeden zdielany rozpocet."""
    __tablename__ = "triggered_watches"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    triggered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FlaggedMacroEvent(Base):
    """Vyznamne makro udalosti s presnym znamym datumom, ktore Claude SAM
    identifikoval pocas beznej analyzy (cez web_search TOHTO cyklu, nie z
    pamate) - viz claude_analyst DECISION_TOOL/POSITION_HEALTH_TOOL pole
    'upcoming_macro_event' a trade_cycle._save_flagged_macro_event. Doplnok k
    rucne udrzovanemu macro_calendar.MACRO_EVENTS (FOMC/CPI/NFP, overene z
    oficialnych zdrojov) - pokryva OSTATNE vyznamne udalosti (OPEC+ stretnutia,
    dolezite earnings, volby, ine centralne banky...), ktore sa nedaju vopred
    vsetky rucne vymenovat, ale Claude ich priebezne pri svojej beznej praci
    zachyti. watch_monitor._check_macro_events oba zdroje zlucuje pri vyhodnocovani,
    ci uz nastal cas nejakej udalosti."""
    __tablename__ = "flagged_macro_events"

    id = Column(Integer, primary_key=True)
    event_key = Column(String, nullable=False, unique=True)  # nazov + datum, napr. "OPEC+_2026-09-01"
    name = Column(String, nullable=False)
    datetime_utc = Column(DateTime, nullable=False)
    flagged_by_symbol = Column(String, nullable=True)  # ktory asset/cyklus to prvy zaznacil
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AccountSnapshot(Base):
    """Zivy stav Strike uctu (2026-08-17, na ziadost pouzivatela - sledovanie
    volnej likvidity v Prehlad tabe monitor-web). JEDEN riadok (id=1),
    PREPISOVANY (nie pripajany) kazdu minutu rovnakym pollerom ako ceny
    (viz price_poller.poll_prices - jeden dalsi lahky GET /v2/account
    navyse). available_balance = volny (nepouzity v ziadnej otvorenej
    pozicii) zostatok, ktory pouzivatel realne chce sledovat kvoli
    likvidite. wallet_balance = celkovy zostatok VRATANE nerealizovaneho
    PnL a pouzitej marze - viz strike_client.get_account()."""
    __tablename__ = "account_snapshot"

    id = Column(Integer, primary_key=True)
    wallet_balance = Column(Float, nullable=True)
    available_balance = Column(Float, nullable=True)
    margin_balance = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    total_margin = Column(Float, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class AtrCalibration(Base):
    """Append-only historia SL/TP kalibracie zalozenej na ATR (2026-08-19,
    nahradza starú klient-side "35% z 24h range" kalibraciu v monitor-web) -
    viz sl_calibration.py. Jeden riadok = jeden prepocet pre JEDEN symbol,
    NIKDY sa neprepisuje (na rozdiel od AccountSnapshot) - takto sa da sledovat
    vyvoj navrhovanej hodnoty v case, nie len posledny stav.

    Nic tu NIKDY automaticky nemeni RiskOverride nizsie - je to len navrh.
    Pouzivatel si v nas100-monitor-web (Kalibracia SL/TP tab) pozrie najnovsi
    riadok pre dany symbol a rucne (tlacidlom) sa rozhodne, ci ho chce
    aplikovat - viz nas100-monitor-web api/apply-calibration.js."""
    __tablename__ = "atr_calibrations"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    lookback_days = Column(Integer, nullable=True)
    bars_used = Column(Integer, nullable=True)
    source = Column(String, nullable=True)  # "own_bars" | "yfinance" | ...

    atr_pct = Column(Float, nullable=True)      # najnovsi hodinovy ATR14 ako % z ceny
    k_multiple = Column(Float, nullable=True)   # vybrany multiplikator (sl_pct = atr_pct * k)
    ratio = Column(Float, nullable=True)        # efektivny tp_pct/sl_pct tohto tickera v case prepoctu

    configured_sl_pct = Column(Float, nullable=True)  # co bolo EFEKTIVNE nastavene v case prepoctu
    configured_tp_pct = Column(Float, nullable=True)
    suggested_sl_pct = Column(Float, nullable=True)
    suggested_tp_pct = Column(Float, nullable=True)


class RiskOverride(Base):
    """Live-updatovatelny SL/TP override PER TICKER (2026-08-19) - ak pre
    symbol existuje riadok tu, MA PREDNOST pred config.py {TICKER}_SL_PCT/
    {TICKER}_TP_PCT defaultom (viz risk_overrides.get_effective_sl_tp(),
    volane z trade_cycle.py). Zamerne v DB, nie v Railway ENV - zmena sa
    prejavi OKAMZITE na dalsom cykle, ziadny redeploy netreba.

    Jediny sposob zapisu je nas100-monitor-web tlacidlo "Nastavit ako default"
    (api/apply-calibration.js) - ten si VZDY sam dohlada najnovsi
    AtrCalibration.suggested_sl_pct/suggested_tp_pct pre dany symbol priamo v
    DB, NIKDY neveri cislam poslanym z prehliadaca (rovnaky bezpecnostny vzor
    ako Trade.manual_close_requested_at kill-switch - citlive zapisy idu cez
    DB flag, nie priamo z verejne dostupneho Vercelu)."""
    __tablename__ = "risk_overrides"

    symbol = Column(String, primary_key=True)
    sl_pct = Column(Float, nullable=False)
    tp_pct = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    source = Column(String, nullable=True)  # napr. "dashboard_apply"


class AssetConfigLive(Base):
    """AKTUALNA konfiguracia kazdeho assetu tak, ako ju worker vidi PRAVE TERAZ
    (2026-09-01, na ziadost pouzivatela).

    Dovod: nas100-monitor-web bezi na Verceli a vidi VYHRADNE tuto Postgres DB -
    k Railway ENV premennym workera pristup nema (a mat nema, su tam aj Strike/
    Claude kluce). Dashboard preto konfiguraciu doteraz cital z
    CycleLog.config_snapshot, teda z posledneho REALNE ZBEHNUTEHO cyklu daneho
    tickera. Po zmene ENV sa nova hodnota objavila az po dalsom behu - pri
    12-hodinovom intervale teda az o pol dna.

    Tento riadok zapise worker SAM hned pri starte (a zmena ENV na Railway
    redeploy/restart vyvola), takze dashboard vidi novu hodnotu prakticky
    okamzite. Jeden riadok na symbol, upsert - ziadna historia; tu ide o
    "co plati teraz", historicke "s cim bot naozaj bezal" zostava v
    CycleLog.config_snapshot a ma sa dalej pouzivat na spatnu analyzu.

    Zapisuju sa VSETKY assety z ALL_ASSETS vratane vypnutych (rovnaky vzor ako
    price_poller) - dashboard tak vie ukazat aj konfiguraciu tickera, ktory
    este ani raz nezbehol."""
    __tablename__ = "asset_config_live"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, unique=True, index=True)
    config_snapshot = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RiskOverrideHistory(Base):
    """Append-only log KAZDEJ zmeny RiskOverride (2026-08-19, na ziadost
    pouzivatela) - RiskOverride vyssie je len JEDEN aktualny riadok na symbol
    (upsert pri kazdom apply, ziadna historia predoslych hodnot), takze bez
    tejto tabulky by nebolo vidno, ODKEDY-DOKEDY aky SL/TP naozaj platil.
    Dolezite pri interpretacii baseline PnL/win-rate v kalibracnom tabe -
    ten agreguje VSETKY uzavrete obchody tickera bez ohladu na to, pod akym
    SL/TP rezimom boli otvorene, takze cislo moze byt mix viacerych rezimov.

    Zapisuje sa automaticky pri kazdom buducom apply (api/apply-calibration.js,
    hned po upsert-e RiskOverride) - `note` je volitelne volny text, ktory
    pouzivatel moze pridat pri kliknuti na tlacidlo. Riadky spred zavedenia
    tejto tabulky (2026-08-19) treba dopisat rucne (jednorazovo, viz
    memory/komentar pri prvom pouziti)."""
    __tablename__ = "risk_override_history"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    sl_pct = Column(Float, nullable=False)
    tp_pct = Column(Float, nullable=False)
    source = Column(String, nullable=True)
    note = Column(String, nullable=True)
    applied_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SlTpBacktestCandidate(Base):
    """"Ziva" TOP-5 tabulka SL/TP kandidatov PER TICKER (2026-08-19, viz
    sl_grid_backtest.py) - NA ROZDIEL od AtrCalibration (jednoduchy ATR-
    zalozeny navrh) toto je grid-search backtest, ktory pre KAZDY ticker
    nezavisle prehla jeho VLASTNE uzavrete obchody a vyberie NAJLEPSIU
    (sl_k, tp_k) kombinaciu (2026-08-19 prepracovane z povodnej POOLED
    verzie naprieč vsetkymi tickermi - pouzivatel spravne poznamenal, ze
    kazdy ticker chce vlastnu analyzu s vlastnym pocitadlom vzorky, nie
    zdielanie s inymi tickermi). Vzdy PREPISOVANA (rovnaky vzor ako
    AccountSnapshot) - az 5 riadkov NA TICKER (kombinovany kluc symbol+rank),
    nie historia v case.

    POZOR: tabulka existovala predtym s inou schemou (LEN rank ako primary
    key, POOLED naprieč vsetkymi tickermi) - __tablename__ je preto zamerne
    NOVY ("_by_symbol" prípona), aby SQLAlchemy create_all() vytvorilo cerstvu
    tabulku so spravnym kombinovanym klucom namiesto konfliktu so starym
    schema (viac tickerov by inak porusovalo povodny rank-only PRIMARY KEY).
    Stara tabulka `sl_tp_backtest_candidates` ostava v DB nepouzivana
    (neskodna, da sa manualne zmazat).

    trade_count = pocet UZAVRETYCH obchodov TOHTO KONKRETNEHO tickera vo
    vzorke - pouzivatel navrhol dovervyhodny prah ~20 (per-ticker, nie
    zdielany)."""
    __tablename__ = "sl_tp_backtest_candidates_by_symbol"

    symbol = Column(String, primary_key=True)
    rank = Column(Integer, primary_key=True)  # 1-5
    sl_k = Column(Float, nullable=False)
    tp_k = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    win_rate = Column(Float, nullable=False)
    trade_count = Column(Integer, nullable=False)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # POZOR (2026-08-19): tento model mal predtym aj sr_total_pnl/sr_win_rate
    # stlpce (S/R-upravena verzia toho isteho kandidata) - cely ten koncept
    # bol ZRUSENY (viz pending_sr_calibration_shelved memory), stlpce v
    # produkcnej DB fyzicky ostavaju (orphaned, neskodne, poor-man's migration
    # nevie DROP COLUMN), ale uz sa nezapisuju ani necitaju.


class SlTpLocalSensitivity(Base):
    """Lokalna citlivostna analyza OKOLO TOP-1 kandidata z SlTpBacktestCandidate
    (2026-08-19, na ziadost pouzivatela) - grid search vyssie testuje len
    diskretne body mriezky (napr. SL_k skace 2->3->4), takze #1 je najlepsi
    z TESTOVANYCH bodov, nie nutne skutocne lokalne optimum. Táto tabulka
    zoberie #1 (sl_k, tp_k) a otestuje plnu 3x3 mriezku okolo neho (vsetky
    kombinacie SL_k/TP_k delta z {-0.25, 0, +0.25}, vratane diagonalnych ako
    SL+0.25 SUCASNE s TP-0.25 - 2026-08-19 zjednodusene z povodnej verzie,
    ktora testovala kazdu os OSOBITNE s +-0.25/+-0.5) na TOTOZNYCH pripravenych
    obchodoch tickera (rovnaka _simulate() funkcia ako hlavny grid - simulacia
    je uz genericka na lubovolny sl_k/tp_k float, ziadny novy simulacny kod
    netreba).

    Ucel: ukazat, ci #1 sedi v stabilnej "plosine" (susedne varianty podobny
    PnL/win-rate = dovervyhodnejsie) alebo je to osamely vrchol (susedne
    varianty vyrazne horsie = skor sum/overfit pri malom n) - viz diskusia
    s pouzivatelom 2026-08-19 o citlivosti odstupov medzi kandidatmi.

    Varianty s neplatnym (<=0) vysledym sl_k/tp_k (napr. base SL_k=0.5 mensi
    o 0.5 = 0) sa jednoducho vynechaju - menej ako 9 riadkov je preto
    normalne, nie chyba."""
    __tablename__ = "sl_tp_local_sensitivity"

    symbol = Column(String, primary_key=True)
    variant = Column(String, primary_key=True)  # "base" | "SL-0.5" | "SL+0.25" | "TP+0.5" | ...
    sort_order = Column(Integer, nullable=False)
    sl_k = Column(Float, nullable=False)
    tp_k = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    win_rate = Column(Float, nullable=False)
    trade_count = Column(Integer, nullable=False)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SlTpBacktestCandidateConstrained(Base):
    """Zrkadlova tabulka SlTpBacktestCandidate (2026-08-19, na ziadost
    pouzivatela) - TOP-5, ale LEN z kombinacii kde TP_k >= 1.5x SL_k (viz
    sl_grid_backtest._MIN_REWARD_RISK_RATIO). Dovod separatnej tabulky
    namiesto pridania stlpca do SlTpBacktestCandidate: rank 1-5 by sa
    prekryval pre oba rebricky na tom istom symbole (PRIMARY KEY konflikt),
    a zmena existujuceho PRIMARY KEY nie je cez poor-man's migraciu
    (_ensure_columns) mozna bez rizikovej ALTER TABLE operacie - rovnaky
    dovod ako pri povodnom sl_tp_backtest_candidates -> _by_symbol prechode.

    Ucel 1.5x filtra: cisto volny grid search moze najst kombinacie so
    SL>=TP, ktore su ziskove LEN ak je odhadnuty win rate (z malej vzorky)
    presny - zly risk:reward pomer nema ziadnu rezervu proti chybe odhadu.
    Tato tabulka ukazuje, ake najlepsie kombinacie by vysli, keby sme sa
    (z opatrnosti, na zaklade povodneho navrhu bota s TP=1.5xSL) obmedzili
    len na disciplinovane pomery."""
    __tablename__ = "sl_tp_backtest_candidates_constrained"

    symbol = Column(String, primary_key=True)
    rank = Column(Integer, primary_key=True)
    sl_k = Column(Float, nullable=False)
    tp_k = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    win_rate = Column(Float, nullable=False)
    trade_count = Column(Integer, nullable=False)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SlTpLocalSensitivityConstrained(Base):
    """Zrkadlova tabulka SlTpLocalSensitivity (2026-08-19) - lokalna 3x3
    citlivost okolo #1 Z SlTpBacktestCandidateConstrained (nie z volneho
    rebricka). Rovnaky dovod separatnej tabulky ako vyssie (PRIMARY KEY
    kolizia so zdielanym symbol+variant klucom)."""
    __tablename__ = "sl_tp_local_sensitivity_constrained"

    symbol = Column(String, primary_key=True)
    variant = Column(String, primary_key=True)
    sort_order = Column(Integer, nullable=False)
    sl_k = Column(Float, nullable=False)
    tp_k = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    win_rate = Column(Float, nullable=False)
    trade_count = Column(Integer, nullable=False)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SlTpRecomputeStatus(Base):
    """Kedy bol naposledy prepocitany SL/TP grid-search rebricek pre KAZDY
    ticker + kolko jeho VLASTNYCH uzavretych obchodov bolo v tom prepocte
    POUZITELNYCH (t.j. malo uz kompletnu 24h cenovu historiu - viz
    sl_grid_backtest._prepare_trade guard) - 2026-08-19, na ziadost
    pouzivatela, aby bolo v dashboarde vidno "ako cerstvy" je rebricek a na
    akej vzorke stoji. VZDY zapisovana pri kazdom recompute_symbol() volani,
    aj ked grid search nenajde ziadne pouzitelne top-5 vysledky (na rozdiel
    od SlTpBacktestCandidate/SlTpLocalSensitivity, ktore v tom pripade
    ostanu prazdne pre dany symbol)."""
    __tablename__ = "sl_tp_recompute_status"

    symbol = Column(String, primary_key=True)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_trade_count = Column(Integer, nullable=False)


class CostCorrection(Base):
    """DOPOCET nakladov za behy, ktore Clauda zaplatili, ale zaznam nezapisali.

    2026-09-04: dvojity kwarg `reviewed_trade_id` zhodil zapis CycleLog az PO
    zaplatenej analyze, takze NEAR sa 9 hodin tocil dokola a v cycle_logs po tom
    neostala ani stopa. Dashboard preto za ten den ukazoval $14 namiesto ~$52.
    Poistka v trade_cycle uz taky beh zapise, ale SPATNE sa tie tokeny dopocitat
    inak nedaju - nikde zaznamenane nie su.

    PRECO VLASTNA TABULKA a nie riadky v cycle_logs: dopocet NIE JE cyklus.
    Keby sme ho tam vlozili, zapocital by sa do poctov behov, trigger statistik,
    audit kariet aj do historie signalov - vsade, kde sa cycle_logs cita ako
    MERANY udaj. Takto ho scitava vyhradne to, co pocita naklady, a dashboard ho
    ukazuje ako samostatnu, oznacenu zlozku.

    Kazdy riadok musi byt OBHAJITELNY: `method` popisuje, ako sa cislo ziskalo,
    `runs_low`/`runs_high` drzia rozsah a `runs_used` to, co sme nakoniec pouzili.
    Ked sa neskor objavi lepsi zdroj, riadok sa prepise alebo zmaze - nic ineho
    od neho nezavisi."""
    __tablename__ = "cost_corrections"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    # Okno, ktore dopocet pokryva (kvoli zaradeniu do spravneho dna na grafe).
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    reason = Column(String, nullable=False)          # kratky nazov incidentu
    method = Column(String, nullable=True)           # ako sa cislo ziskalo
    runs_low = Column(Integer, nullable=True)
    runs_high = Column(Integer, nullable=True)
    runs_used = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    cache_write_1h_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    web_searches = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CoinMarketCalEvent(Base):
    """Kesovane nadchadzajuce krypto-projektove udalosti z CoinMarketCal
    (2026-08-19, na ziadost pouzivatela) - burzove listingy, hlasovania,
    protokolove upgrady, token unlocky. Doplna existujuci Event Risk Gate
    mechanizmus (viz claude_analyst.py), ktory doteraz spolieha VYHRADNE na
    Claude-ov vlastny web_search bez strukturovaneho zdroja.

    Refreshuje sa DENNE (viz coinmarketcal_client.poll_events) - ZIADNE zive
    volanie API pocas obchodneho cyklu. Free plan ma kreditovy kvoten (nie
    klasicky rate-limit, resetuje sa ~13 dni), takze denny poll pre max. 4
    tickery je bezpecne konzervativny.

    Kompletne PREPISOVANA pre kazdy symbol pri kazdom pollovacom behu (stare
    riadky zmazane, nove vlozene) - nie historia v case, len aktualny
    nadchadzajuci vyhlad."""
    __tablename__ = "coinmarketcal_events"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)  # nas strike_symbol, napr. "ADA-USD"
    cmc_event_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    date_start = Column(DateTime, nullable=False)
    date_end = Column(DateTime, nullable=True)
    is_estimated = Column(Boolean, default=False)
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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


# pool_size/max_overflow explicitne (2026-08-19, crash-scenario audit) - bez
# tychto parametrov SQLAlchemy default (5 + 10 overflow = 15 sucasnych spojeni)
# by pri hromadnom zatvoreni viacerych pozicii naraz mohol byt tesny: az
# _DISPATCH_CONCURRENCY_LIMIT (viz trade_cycle.py) suecasnych review vlakien +
# 6 scheduler jobov, kazde drziace session (a teda pripojenie) otvorenu PO
# CELU DLZKU Claude volania (nie len pocas samotneho DB dotazu). 30 celkovo
# dava pohodlnu rezervu. pool_pre_ping=True zabrani chybam z "stale" spojeni,
# ktore Railway/Postgres proxy obcas po dlhsej necinnosti tichy zahodi.
_engine = create_engine(config.DATABASE_URL, future=True,
                         pool_size=10, max_overflow=20, pool_pre_ping=True)
Base.metadata.create_all(_engine)
_ensure_columns(_engine)
SessionLocal = sessionmaker(bind=_engine, future=True)


def get_session():
    return SessionLocal()
