# Sentiment Bot (Strike Finance) — NAS100 + NVDA + ADA + GOLD + WTI + NIGHT + BTC + HYPE + SKHYNIX + AAOI + MINIMAX

Automatizovaný multi-asset obchodný bot na Strike Finance: **NAS100** (index),
**NVDA** (akcia), **ADA** (krypto perpetuál), **GOLD** (komodita, zámerne
pridaná ako protivietor k prevažne risk-on smerovaniu ostatných — safe-haven
asset, opačná polarita VIX), **WTI** (ropa, pridaná 2026-07-31 ako vyraznejsie
odlisny ticker - iny driver OPEC+/geopolitika/dopyt, NIE safe-haven ako zlato),
**NIGHT** (krypto Midnight/Cardano, pridaná v tom istom kroku — vyrazne
rizikovejsi/volatilnejsi mlady token po nedavnom bridge hacku, najnizsia paka
zo vsetkych), **BTC** (krypto Bitcoin, pridaná 2026-08-06 — najlikvidnejší
market na Strike, vlastné makro pravidlá odlišné od ADA/NIGHT: ETF toky,
inštitucionálna adopcia, rastúca makro/Fed citlivosť namiesto "len" BTC-beta
naratívu), **HYPE** (krypto Hyperliquid, pridaná 2026-08-07 spolu so SKHYNIX
ako 2 z 3 najmenej korelovaných assetov z korelačnej analýzy celej Strike
ponuky — genuinná diverzifikácia, nie len ďalší krypto-beta ticker; OHLC
zdroj je výnimočne CoinGecko namiesto Binance/yfinance, ktoré HYPE
nepokrývajú), **SKHYNIX** (akcia SK Hynix na Korea Exchange, hlavný dodávateľ
HBM pamätí pre Nvidia AI GPU — jediný asset s vlastnou KRX seansou 00:00-06:30
UTC namiesto zdieľanej NYSE session), **AAOI** a **MINIMAX** (obe pridané
2026-08-14, **NEAKTÍVNE** — `ENABLE_AAOI`/`ENABLE_MINIMAX=false`, rovnaký
"pozastavený" vzor ako NVDA. AAOI je reálna NASDAQ akcia — Applied
Optoelectronics, optické komponenty pre AI datacentrá, small-cap s historicky
vysokou volatilitou. MINIMAX je **syntetický Strike tracker súkromnej**
(pre-IPO) čínskej AI firmy MiniMax Group — rovnaká kategória ako CXMT/SPCX na
Strike, žiadny reálny burzový trh za sebou. Obe zatiaľ LEN zbierajú cenovú
históriu cez `price_poller.py` — pripravené na aktivovanie bez ďalšieho
kódovania, keď sa nazbiera dosť dát). Každý asset je nezávislý "bot" —
vlastná pozícia, vlastný risk (SL/TP %, leverage, margin, min. confidence,
frekvencia cyklu, trading hours), vlastné rozhodnutie od Claude — ale všetky
bežia v **jednom scheduler cykle** a zdieľajú cross-market/session (a pre
ADA/NIGHT/HYPE aj BTC-proxy — BTC samotné ako ticker si vlastnú proxy
referenciu nevyžaduje) makro fetch, takže sa tie isté dáta nesťahujú 9x (viz
`assets.py`, `trade_cycle.run_all_cycles`).

**Ako to funguje (jeden cyklus, `trade_cycle.run_all_cycles`):**

0. Zdieľaný krok: `market_data.get_cross_market_snapshot()` a `get_session_snapshot()`
   sa zavolajú **RAZ** pre celý cyklus (nie per asset). Ak je aktívna ADA alebo NIGHT,
   pridá sa ešte `get_btc_proxy_snapshot()` (BTC ako krypto-makro proxy, tiež cez
   yfinance, žiadny nový platený zdroj). Rovnako sa **RAZ** natiahne aj
   `fred_client.get_macro_snapshot()` (CPI/Core CPI/Fed funds rate priamo z Fedu,
   voliteľné - viz nižšie).
1. Pre každý aktívny asset z `assets.py` (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX/AAOI/MINIMAX
   — NVDA/AAOI/MINIMAX sú momentálne `enabled=False`, viz vyššie), KTORÝ je práve "na rade" (viz
   `trade_cycle._is_due` — každý asset má vlastnú frekvenciu, viz nižšie):
   - `market_data.py` zostaví hodinové OHLC sviečky a spočíta TA indikátory (RSI,
     MACD, EMA20/50/200, Bollinger Bands, ATR, trend). Primárny zdroj je **vlastná**
     `price_bars` tabuľka, ktorú `price_poller.py` plní každú minútu zo Strike
     `mark_price` (viz nižšie) — na rozdiel od yfinance zostáva živá aj mimo
     obchodných hodín/cez víkend, keďže Strike perpetuály obchodujú nonstop.
     yfinance (`^NDX`/`NQ=F`, NVDA, `GC=F`/`GLD`, `CL=F`/`USO`) slúži ako
     **fallback** (ak vlastné dáta chýbajú/sú zastarané) a ako doplnkový zdroj
     volume dát pre NAS100/NVDA/GOLD (Strike mark_price žiadny objem
     neposkytuje). Pre ADA/NIGHT/BTC ide objem namiesto toho z **Binance**
     (`binance_client.py`) — sú tam skutočne obchodované so spoľahlivým
     objemom, na rozdiel od riedkeho/chýbajúceho pokrytia cez yfinance; pre WTI
     volume zámerne vypnuté (nebolo empiricky overené), viz `assets.py`. Objem
     chýbajúci pre danú hodinu (yfinance intradenné dáta pre futures bežne
     zaostávajú za realitou) sa serializuje ako `null`, nikdy nie ako falošná
     `0` — pozri `_merge_volume`/`_merge_volume_from_binance` v `market_data.py`.
   - (voliteľne) `social_sentiment.py` stiahne najnovšie tweety/posty s
     relevantnými hashtagmi/cashtagmi pre daný asset cez X API.
   - (voliteľne) `marketaux_client.py` stiahne najnovšie finančné správy so
     sentiment skóre pre presný dopyt daného assetu (viz `assets.py`
     `marketaux_query` - napr. `QQQ` pre NAS100, `ADAUSD` pre ADA; pre NIGHT
     zámerne NIE holé "NIGHT" - bežné anglické slovo, ale `search="Midnight"
     entity_types=cryptocurrency`, viz komentár v `assets.py`).
   - (len WTI, voliteľne) `eia_client.py` stiahne posledné týždenné komerčné
     zásoby ropy priamo z EIA (US Energy Information Administration) - presné
     číslo namiesto spoliehania sa na to, či `web_search` nájde a správne
     časovo zaradí tento report.
   - `claude_analyst.py` pošle TA dáta + zdieľaný makro kontext do Claude
     (Anthropic API) s povoleným vstavaným **`web_search`** nástrojom — Claude si
     podľa potreby sám vyhľadá čerstvé správy (asset-špecifický news-focus, viz
     `claude_analyst.ASSET_TEXT`) priamo cez Anthropic API (žiadny NewsAPI kľúč
     netreba) — a vráti **štruktúrovanú JSON odpoveď**: smer (long/short/none),
     confidence 0-100, navrhovaný stop-loss a take-profit a krátke zdôvodnenie.
   - `risk_manager.py`: jediný GATE na otvorenie obchodu je **confidence** (per-asset
     `min_confidence`) - okrem toho už len veci mimo našej kontroly (už otvorená
     pozícia PRE TENTO symbol, alebo skutočné limity burzy - min. veľkosť/notional
     objednávky, ktoré Strike API jednoducho neprijme). SL vzdialenosť navrhnutá
     Claudom sa **vždy použije** (nikdy nezablokuje vstup) - orežie sa len do
     širokého bezpečnostného rozsahu (0.1x-5x asset-špecifického % z `assets.py`) a
     umiestni na správnu stranu vstupnej ceny podľa smeru. **TP sa dopočíta z tejto
     SL vzdialenosti a cieľového pomeru `tp_pct/sl_pct`** namiesto priameho použitia
     Claude-ovho navrhnutého TP - backtest na historických dátach (2026-07-24)
     ukázal, že Claude systematicky navrhoval oveľa širší SL než TP (risk:reward
     0.09-0.17 namiesto cieľových 1.5), čo pri reálnom cenovom vývoji viedlo k
     stratám aj pri dobrom win-rate (malé výhry, obrovské prehry).
   - Ak prejde kontrolou, `strike_client.py` otvorí pozíciu cez Strike API s daným
     SL/TP na asset-špecifickom symbole.
   - Obchod sa zapíše do DB (`db.py`, `symbol` stĺpec) s časom otvorenia a
     expiráciou (spoločný `POSITION_MAX_HOURS`).
   - Zlyhanie jedného assetu (chyba API, zamietnutý risk-manager) nezastaví
     ostatné — každý beží vo vlastnom try/except a vlastnej DB session.
2. `position_monitor.py` beží nezávisle v kratších intervaloch, v **jednom**
   `get_positions()` volaní (bez symbol filtra) načíta všetky otvorené pozície
   naprieč assetmi a pre každý otvorený `Trade` v DB:
   - zistí, či pozícia už bola zavretá burzou (SL/TP/likvidácia hit) → zapíše čas a PnL,
   - ak od otvorenia uplynulo `POSITION_MAX_HOURS` a pozícia je stále otvorená →
     force-close cez API a zapíše PnL.
3. `price_poller.py` beží nezávisle **každú minútu** — jedným bulk `get_markets()`
   volaním zapíše/aktualizuje aktuálnu hodinovú `price_bars` sviečku pre každý
   aktívny asset (viz vyššie). Pri štarte procesu naviac raz (idempotentne)
   zabehne `backfill_if_empty()` — natiahne ~30 dní histórie z yfinance pre
   akýkoľvek asset, ktorý ešte nemá žiadny vlastný záznam.

`main.py` toto všetko spúšťa na pozadí cez scheduler (APScheduler) — beží ako
jeden dlhodobo bežiaci proces na Railway (worker service). `trade_cycle` job
tiká na `min()` z aktuálnych `*_TRADE_INTERVAL_HOURS` všetkých aktívnych
assetov (najrýchlejšie požadovaná frekvencia) — každý asset sa reálne
rozhoduje/obchoduje na SVOJOM vlastnom (pomalšom alebo rovnakom) intervale cez
`trade_cycle._is_due()`. `MONITOR_INTERVAL_MINUTES` zostáva **zdieľané pre
všetky assety** (jedno `get_positions()` volanie kontroluje všetky otvorené
pozície naraz).

Assety možno jednotlivo vypnúť cez `ENABLE_NVDA`/`ENABLE_ADA`/`ENABLE_GOLD`/`ENABLE_WTI`/`ENABLE_NIGHT`/`ENABLE_BTC`/`ENABLE_HYPE`/`ENABLE_SKHYNIX`/`ENABLE_AAOI`/`ENABLE_MINIMAX` (NAS100 beží vždy; NVDA/AAOI/MINIMAX sú momentálne default `false`).

## ⚠️ Dôležité upozornenia

- **Toto obchoduje s reálnymi peniazmi na pákový produkt — momentálne na SIEDMICH
  nezávislých assetoch naraz (plus NAS100 = osem celkovo aktívnych; AAOI/MINIMAX/NVDA
  sú registrované, ale `enabled=false`, takže reálne neobchodujú, len zbierajú
  cenovú históriu).** SL/TP sa nastavujú cez bracket
  "strategy" objednávku (`POST /v2/order/strategy`, polia `tp_order`/`sl_order`),
  leverage sa nastavuje samostatne pred otvorením pozície (`POST /v2/leverage`),
  margin mode je **isolated** (nie cross - viz `strike_client.open_bracket_position`)
  a `size` je v base-asset jednotkách, nie notional USD. Overené voči
  https://docs.strikefinance.org/api/trade/orders a
  https://docs.strikefinance.org/api/trade/trading.
- NVDA, ADA, GOLD, WTI, NIGHT a BTC majú nižšiu default paku a širšie SL/TP % než NAS100
  (viz `.env.example`) — sú kalibrované na vyššiu typickú volatilitu jednotlivej
  akcie/komodity/krypta, ale over si to sám na pár dňoch DRY_RUN dát pred ostrým
  behom. **NIGHT je výrazne rizikovejší/volatilnejší** než ostatné (mladý,
  nízko-kapitalizovaný token, čerstvý Wanchain bridge hack 20.7.2026 - preto
  najnižšia paka zo všetkých).
- Spusti bota najprv s `DRY_RUN=true` — všetko sa vygeneruje a zaloguje/zapíše do DB,
  ale žiadny reálny obchod sa nespraví. Skontroluj si logy/DB aspoň pár dní.
- Confidence skóre od Claude je odhad, nie záruka výsledku. Nikdy nevkladaj viac
  kapitálu na obchod, než si ochotný stratiť.
- `web_search` nástroj má okrem tokenov aj vlastný poplatok za vyhľadávanie (pozri
  aktuálny cenník na console.anthropic.com) — `max_uses: 5` v `claude_analyst.py`
  limituje počet vyhľadávaní na cyklus.
- Súkromný kľúč k Strike API wallet (a Anthropic API kľúč) patria **iba** do
  Railway environment variables, nikdy do repozitára.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # vyplň hodnoty
python main.py
```

### Poznámky pre lokálny beh na Windows (Python 3.14)

Tieto problémy sa týkajú len lokálneho vývoja na tomto stroji, nie Railway (Linux) deploy:

- **SSL/TLS chyby na všetky HTTPS requesty** (Norton Antivirus robí TLS inšpekciu a jeho
  root certifikát nie je v `certifi` zväzku): `pip install pip-system-certs` (nasmeruje
  Python na Windows trust store).
- **`pandas-ta` sa nedá nainštalovať** (pinuje `numba==0.61.2`, ktorý nepodporuje Python 3.14):
  `pip install numba` (najnovšia verzia) a potom
  `pip install --no-build-isolation --no-deps pandas-ta`.
- **yfinance/curl_cffi SSL chyba aj po opravě certifikátov** (impersonate mód ignoruje
  systémový trust store): nastav `YF_DISABLE_CURL_CFFI=true` (je už v `.env`).
- **UnicodeEncodeError pri printe** (Windows konzola cp1252 nevie slovenskú diakritiku):
  spusti s `PYTHONIOENCODING=utf-8`.

## Environment premenné

Pozri `.env.example` — najdôležitejšie:

- `ANTHROPIC_API_KEY` — tvoj Anthropic API kľúč (analytik)
- `STRIKE_API_PRIVATE_KEY` / `STRIKE_API_PUBLIC_KEY` — API wallet ku Strike (Ed25519, vygeneruj na app.strikefinance.org/api-keys)
- `STRIKE_NAS100_SYMBOL` / `STRIKE_NVDA_SYMBOL` / `STRIKE_ADA_SYMBOL` / `STRIKE_GOLD_SYMBOL` /
  `STRIKE_WTI_SYMBOL` / `STRIKE_NIGHT_SYMBOL` / `STRIKE_BTC_SYMBOL` — presný symbol/market
  identifikátor pre daný asset na Strike (zisti cez `get_markets()` v `strike_client.py`)
- `TWITTER_BEARER_TOKEN` — voliteľné, X API v2 (platený tier na zmysluplný recent search)
- `EIA_API_KEY` — voliteľné, zdarma po registrácii (https://www.eia.gov/opendata/register.php),
  len pre WTI (týždenné komerčné zásoby ropy)
- `FRED_API_KEY` — voliteľné, zdarma po registrácii (https://fredaccount.stlouisfed.org),
  zdieľané pre všetky assety (CPI/Core CPI/Fed funds rate)
- `MARKETAUX_API_KEY` — voliteľné, free tier ~100 req/deň po registrácii
  (https://www.marketaux.com), per-asset news+sentiment (viz `assets.py` `marketaux_query`)
- `DATABASE_URL` — pre trvalé uloženie histórie obchodov použi Railway Postgres plugin
  (SQLite súbor na Railway sa stratí pri každom redeployi!)
- `DRY_RUN` — `true`/`false` — **zdieľané pre všetky assety**
- `MONITOR_INTERVAL_MINUTES` — ako často sa kontrolujú otvorené pozície (napr. `10`) — zdieľané.
  Nemusí byť tesný, SL/TP na otvorenej pozícii chráni Strike sám (bracket order v reálnom čase) -
  toto len dodatočne synchronizuje náš DB záznam
- `WATCH_INTERVAL_MINUTES` — ako často sa kontroluje watch cenová podmienka (default `1`) —
  samostatný, tesnejší interval nez `MONITOR_INTERVAL_MINUTES` (viz `watch_monitor.py`) - tu
  častejšia kontrola reálne znižuje šancu prehliadnuť krátky dotyk/odraz od sledovanej hladiny
- `POSITION_MAX_HOURS` — max. držanie pozície pred force-close — zdieľané
- `WATCH_TRIGGER_MAX_PER_HOUR` (default `3`, 2026-08-08) — rovnaká bezpečnostná poistka ako
  `MACRO_EVENT_MAX_TRIGGERS_PER_HOUR` nižšie, ale pre cenový watch mechanizmus - **PER ASSET** (na
  rozdiel od makro poistky, ktorá je jeden zdieľaný rozpočet naprieč všetkými assetmi naraz, keďže
  makro udalosti sú často "všetky assety" burst - cenový watch je vždy nezávislý per-symbol, preto
  má každý ticker vlastný rozpočet). Bez tejto poistky by watch-trigger mohol spúšťať mimoriadne
  cykly neobmedzene často, ak by každý ďalší cyklus znova nastavil (aj mierne inú) blízku watch
  úroveň. Sleduje sa cez novú `TriggeredWatch` DB tabuľku (`db.py`) - zápis PRED spustením cyklu
  (rovnaký crash-safe vzor ako `TriggeredMacroEvent` nižšie).
- `WATCH_CONFIDENCE_MARGIN` (default `5`, 2026-08-15) — rozšírenie watch mechanizmu z
  `direction="none"` aj na `direction=long/short`, ktoré risk manager zamietol ČISTO kvôli
  confidence. Ak Claude navrhne smer a jeho confidence padne do pásma
  `[{TICKER}_MIN_CONFIDENCE − margin, {TICKER}_MIN_CONFIDENCE)`, dostane presné číselné pásmo v
  user správe a MÁ sa vždy explicitne vyjadriť (`confidence_threshold_note` na `submit_trade_decision`),
  pri akej cene by — čisto technicky, nikdy plynutím času — jeho confidence prekročila prah. Tú cenu
  zapíše do (už existujúcich) `watch_price`/`watch_direction`, ktoré `watch_monitor.py` sleduje úplne
  rovnako ako pri `direction="none"` (nerozlišuje, odkiaľ hodnota pochádza) - žiadny nový kód na
  stranu Strike, stále chránené `WATCH_TRIGGER_MAX_PER_HOUR` vyššie, a spustený mimoriadny cyklus je
  VŽDY kompletne čerstvá analýza (nie mechanické vykonanie pôvodného návrhu). Je v poriadku, ak Claude
  napíše, že cenu nevie odhadnúť - watch sa vtedy jednoducho nenastaví.
- `TA_LIVE_PRICE_MISMATCH_RATIO` (default `3.0`, 2026-08-09 — pôvodne navrhované `2.0`, zdvihnuté po
  backteste na reálnom 10.10.2025 krypto flash-crashi: ADA mala v jednej hodine skutočný intra-hour
  knôt 2.62x pod otváracou cenou, čo by pri `2.0` bol falošný poplach na genuinnom trhovom pohybe,
  nie na chybe dát) — preventívna poistka proti
  scale-mismatch dát objavená pri SKHYNIX incidente (`000660.KS` v KRW vs. Strike-ov syntetický USD
  tracker, ~1400x rozdiel - watch_price nafúknutý na túto škálu bol voči live cene triviálne vždy
  pravdivý, watch_monitor preto spúšťal cyklus takmer na každom ticku). Existujúci SL/TP safety cap
  (`risk_manager.py`) už chránil SKUTOČNÉ OBCHODY pred zlou škálou (klampovanie na 0.1x-5x cieľového
  %), ale `watch_price`/`watch_direction` žiadnu takú ochranu nemali. `trade_cycle._check_ta_scale`
  porovná TA `last_price` voči Strike live cene HNEĎ pri zbere dát (ešte PRED Claude volaním, ušetrí
  aj náklad) - ak sa líšia viac než tento násobok, cyklus sa čisto preskočí namiesto použitia
  podozrivých dát. Zámerne NEZÁVISLÉ od konkrétneho zdroja/symbolu - zachytí to aj budúce, ešte
  neopravené zdroje, nie len tie už identifikované (SKHYNIX/GOLD/WTI). Zdieľané (nie per-ticker) - je
  to fakt o dátovej integrite, nie risk preferencia.
- `MACRO_EVENT_MAX_TRIGGERS_PER_HOUR` (default `3`) — bezpečnostná poistka pri zhluku makro udalostí;
  ich presný čas je vopred známy (na rozdiel od cenového watch vyššie), takže sa mimoriadny cyklus
  spustí HNEĎ pri zverejnení namiesto čakania na ďalší bežný interval. Dva zdroje udalostí (viz
  `watch_monitor._check_macro_events`): (1) `macro_calendar.py` — počiatočný, raz naplnený zoznam
  FOMC/CPI/NFP (overené z oficiálnych zdrojov k 2026-08-07); (2) `FlaggedMacroEvent` (DB tabuľka) —
  **priebežnú údržbu kalendára odteraz preberá Claude sám** cez `upcoming_macro_event` pole
  (`claude_analyst.py`/`trade_cycle._save_flagged_macro_event`) - buď keď na významný termín narazí
  počas bežnej analýzy, alebo cielene raz denne pri retrospektívnom cykle (explicitná inštrukcia v
  `SYSTEM_PROMPT_SHARED` prezerať web_search na udalosti v horizonte ~30-60 dní). `scope="all_assets"`
  (napr. FOMC/CPI/NFP) spustí všetky aktívne tickery; `scope="this_asset"` (default, napr. OPEC+ pre
  WTI, bezpečnostný deadline pre NIGHT) spustí LEN ten jeden asset - nikto nič ručne nedopĺňa
- `TRADING_HOURS_START_UTC` / `TRADING_HOURS_END_UTC` — hranice trading hours v UTC (default `13`/`21`,
  pokrýva NYSE cash session 9:30-16:00 ET v oboch DST stavoch) — zdieľaný DEFAULT pre všetky assety
  OKREM SKHYNIX (Korea Exchange, vlastná dvojica `SKHYNIX_TRADING_HOURS_START_UTC`/`END_UTC` nižšie,
  iný kontinent/timezone ako zdieľaný NYSE default)

**Per-ticker premenné (2026-07-31 zjednotené, BTC pridaný 2026-08-06, HYPE+SKHYNIX pridané
2026-08-07, AAOI+MINIMAX pridané 2026-08-14)** — každý z 11 tickerov
(NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX/AAOI/MINIMAX) má VLASTNÚ
sadu presne rovnakých 9 premenných, zoskupenú v `.env.example` ticker-po-tickeri:
`{TICKER}_MIN_CONFIDENCE`, `{TICKER}_MARGIN_USD`, `{TICKER}_LEVERAGE`,
`{TICKER}_LIQUIDATION_CUSHION_MULTIPLE`, `{TICKER}_SL_PCT`, `{TICKER}_TP_PCT`,
`{TICKER}_TRADE_INTERVAL_HOURS`, `{TICKER}_OFF_HOURS_INTERVAL_HOURS`,
`{TICKER}_WEEKEND_INTERVAL_HOURS`. Napr. `GOLD_LIQUIDATION_CUSHION_MULTIPLE`, `NIGHT_SL_PCT`,
`WTI_WEEKEND_INTERVAL_HOURS`. SKHYNIX má navyše vlastnú `SKHYNIX_TRADING_HOURS_START_UTC`/`END_UTC`
dvojicu (KRX seansa 00:00-06:30 UTC).

- Predtým mal NAS100 bezpredponové názvy (`MIN_CONFIDENCE`/`MARGIN_USD`/...) a ADA/NIGHT nemali
  `off_hours`/`weekend` vôbec - teraz je štruktúra jednotná pre všetkých deväť.
- `MIN_CONFIDENCE`, `MARGIN_USD`, `SL_PCT`/`TP_PCT` — risk parametre (per asset). `MARGIN_USD` je
  fixná marža na obchod; `SL_PCT`/`TP_PCT` sú cieľové SL/TP ako % od live ceny — Claude navrhuje
  presnú vzdialenosť, ktorá sa orežie do 0.1x-5x týchto hodnôt (nikdy nezablokuje vstup - viz
  `risk_manager.py`). `ADA`/`NIGHT`/`HYPE`/`SKHYNIX_MARGIN_USD` sú od 2026-08-08 znížené na `$50`
  (ostatné `$100`) - viac tickerov teraz zdiela jednu peňaženku bez koordinácie (viz preflight
  kontrola zostatku nižšie).
- **`LEVERAGE` vs. `LIQUIDATION_CUSHION_MULTIPLE`** (2026-08-08): `{TICKER}_LEVERAGE` už
  NEOVPLYVŇUJE skutočný position sizing - ostáva len ako historický/referenčný údaj (dashboard,
  `retrospective.py` fallback pre staré záznamy). Skutočná páka sa teraz DOPOČÍTAVA per-obchod z
  `{TICKER}_LIQUIDATION_CUSHION_MULTIPLE` (default `1.5`) a aktuálnej SL vzdialenosti tak, aby
  vzdialenosť do teoretickej likvidačnej ceny bola presne tento násobok SL vzdialenosti (napr. `1.5`
  = likvidácia je o 50% ďalej od vstupu než SL) - vždy orezané zhora na skutočný Strike-om povolený
  strop pre danú maržu/tier (`risk_manager._leverage_cap_and_mmr`), nikdy nad to. Cieľ (explicitne
  zvolený používateľom) je maximalizovať expozíciu pri zachovaní bezpečného odstupu od likvidácie -
  užší SL teda dnes znamená VYŠŠIU páku/notional pri rovnakej marži, širší SL nižšiu. Nastaviteľné
  per-ticker, keby niektorý ticker potreboval iný vankuš (napr. volatilnejší ticker vyšší multiple).
- `TRADE_INTERVAL_HOURS`/`OFF_HOURS_INTERVAL_HOURS`/`WEEKEND_INTERVAL_HOURS` — frekvencia cyklu
  počas trading hours / mimo nich / cez víkend. Pre 24/7 krypto (ADA/NIGHT/BTC/HYPE) sú
  `off_hours`/`weekend` defaultne rovnaké ako `trade_interval` (žiadne skutočné "off hours" preň
  neexistujú), ale sú nezávisle nastaviteľné rovnako ako pre ostatné - napr. neskôr predĺžiť
  víkendový interval aj pre ne, bez zmeny kódu.
- `ENABLE_NVDA`/`ENABLE_ADA`/`ENABLE_GOLD`/`ENABLE_WTI`/`ENABLE_NIGHT`/`ENABLE_BTC`/`ENABLE_HYPE`/
  `ENABLE_SKHYNIX`/`ENABLE_AAOI`/`ENABLE_MINIMAX` — `true`/`false`, vypnutie/zapnutie daného bota
  (NAS100 beží vždy). NVDA je od 2026-07-31 pozastavené (nahradené WTI/NIGHT, cost-optimalizácia) —
  historické `cycle_logs`/`trades` ostávajú v DB a v monitor-web dashboarde, len sa nezapočítavajú do
  nového `web_search`/Claude nákladu (viz `trade_cycle._mark_disabled_assets` pre "Pozastavené"
  označenie). AAOI/MINIMAX sú od 2026-08-14 v rovnakom stave, ale z iného dôvodu — ešte NIKDY
  neobchodovali (nie "pozastavené", ale "zatiaľ nezapnuté"): `price_poller.py` pre ne beží (zbiera
  históriu do `price_bars`), ale Claude analýza/`trade_cycle` cyklus sa nespúšťa, kým niekto ručne
  `ENABLE_AAOI`/`ENABLE_MINIMAX=true` nenastaví. Všetko ostatné (systémový prompt, marketaux/Twitter
  dotazy, risk parametre) je už pripravené, takže zapnutie nevyžaduje žiadny ďalší kód.
- **Preflight kontrola zostatku** (2026-08-08, `trade_cycle.py`): pred každým skutočným otvorením
  pozície (mimo `DRY_RUN`) sa overí `/v2/account` `available_balance` voči potrebnej marži - ak
  nestačí, obchod sa čisto zamietne (`outcome="rejected"`, `reject_reason="insufficient_balance: ..."`)
  namiesto surovej chyby zo Strike. Zlyhanie samotnej kontroly (napr. `/v2/account` nedostupné)
  obchod neblokuje - vtedy je finálnou poistkou samotné Strike API.

## Deploy na Railway

1. Push tento priečinok do vlastného GitHub repa (alebo `railway up` priamo z lokálu).
2. V Railway vytvor nový projekt → "Deploy from GitHub repo".
3. Pridaj Postgres plugin (Railway → New → Database → PostgreSQL) a skopíruj
   `DATABASE_URL` do env premenných služby s botom.
4. Nastav zvyšné env premenné v Railway → Variables.
5. Railway automaticky použije `Procfile` (`worker: python main.py`). Keďže ide
   o worker (nie web službu), nie je potrebné bindovať port.
6. Sleduj logy v Railway dashboard.

## Súbory

| Súbor | Účel |
|---|---|
| `main.py` | scheduler, entrypoint |
| `config.py` | centrálne env premenné (zdieľané + per-asset) |
| `assets.py` | registry assetov (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX/AAOI/MINIMAX) - symbol, TA ticker, SL/TP%, leverage, margin, min_confidence, frekvencia cyklu, trading hours |
| `coingecko_client.py` | verejné CoinGecko OHLC dáta - fallback/backfill zdroj LEN pre HYPE (nie je na Binance ani yfinance) |
| `db.py` | SQLAlchemy modely `Trade`/`CycleLog` (obe majú `symbol`) + session |
| `market_data.py` | OHLCV + TA indikátory (per asset, primárne z vlastných `price_bars`, fallback yfinance), zdieľaný cross-market/session/BTC-proxy fetch |
| `price_poller.py` | každominútový poller Strike `mark_price` do `price_bars` + jednorazový yfinance backfill |
| `social_sentiment.py` | (voliteľné) X/Twitter sentiment, per asset query |
| `marketaux_client.py` | (voliteľné) news + sentiment skóre per asset (free tier ~100 req/deň) |
| `eia_client.py` | (voliteľné, len WTI) týždenné komerčné zásoby ropy priamo z EIA |
| `fred_client.py` | (voliteľné, zdieľané) CPI/Core CPI/Fed funds rate priamo z FRED |
| `binance_client.py` | verejné (bez kľúča) hodinové klines - zdroj `volume` pre ADA/NIGHT/BTC namiesto riedkeho yfinance pokrytia |
| `claude_analyst.py` | zostaví per-asset prompt, zavolá Claude (s `web_search` nástrojom), parsuje JSON rozhodnutie |
| `strike_client.py` | Ed25519 podpisovanie, open/close position, get positions/markets |
| `risk_manager.py` | position sizing; jediny gate na vstup je confidence, SL/TP sa vzdy pouzije (nikdy nezablokuje) |
| `trade_cycle.py` | `run_all_cycles()` - zdieľaný makro fetch + loop cez aktívne assety |
| `position_monitor.py` | kontrola/zatváranie otvorených pozícií naprieč assetmi |
