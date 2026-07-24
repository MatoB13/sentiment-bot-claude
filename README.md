# Sentiment Bot (Strike Finance) — NAS100 + NVDA + ADA + GOLD

Automatizovaný multi-asset obchodný bot na Strike Finance: **NAS100** (index),
**NVDA** (akcia), **ADA** (krypto perpetuál) a **GOLD** (komodita, zámerne
pridaná ako protivietor k prevažne risk-on smerovaniu ostatných troch — safe-haven
asset, opačná polarita VIX). Každý asset je nezávislý "bot" — vlastná pozícia,
vlastný risk (SL/TP %, leverage, margin, min. confidence), vlastné rozhodnutie od
Claude — ale všetky bežia v **jednom scheduler cykle** a zdieľajú cross-market/
session (a pre ADA aj BTC-proxy) makro fetch, takže sa tie isté dáta nesťahujú
4x (viz `assets.py`, `trade_cycle.run_all_cycles`).

**Ako to funguje (jeden cyklus, `trade_cycle.run_all_cycles`):**

0. Zdieľaný krok: `market_data.get_cross_market_snapshot()` a `get_session_snapshot()`
   sa zavolajú **RAZ** pre celý cyklus (nie per asset). Ak je aktívna ADA, pridá sa
   ešte `get_btc_proxy_snapshot()` (BTC ako krypto-makro proxy, tiež cez yfinance,
   žiadny nový platený zdroj).
1. Pre každý aktívny asset z `assets.py` (NAS100/NVDA/ADA/GOLD):
   - `market_data.py` stiahne cenové dáta (NAS100 cez `^NDX`/`NQ=F` proxy, NVDA,
     ADA-USD a GOLD cez `GC=F`/`GLD` fallback priamo) a spočíta TA indikátory
     (RSI, MACD, EMA20/50/200, Bollinger Bands, ATR, trend).
   - (voliteľne) `social_sentiment.py` stiahne najnovšie tweety/posty s
     relevantnými hashtagmi/cashtagmi pre daný asset cez X API.
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

`main.py` toto všetko spúšťa na pozadí cez scheduler (APScheduler) — beží ako
jeden dlhodobo bežiaci proces na Railway (worker service). `TRADE_INTERVAL_HOURS`
a `MONITOR_INTERVAL_MINUTES` sú **zdieľané pre všetky assety** (bežia v tom istom
tiku) — zmena v Railway env sa prejaví pre všetky naraz.

Assety možno jednotlivo vypnúť cez `ENABLE_NVDA`/`ENABLE_ADA`/`ENABLE_GOLD` (NAS100 beží vždy).

## ⚠️ Dôležité upozornenia

- **Toto obchoduje s reálnymi peniazmi na pákový produkt — na TROCH nezávislých
  assetoch naraz.** SL/TP sa nastavujú cez bracket "strategy" objednávku
  (`POST /v2/order/strategy`, polia `tp_order`/`sl_order`), leverage sa nastavuje
  samostatne pred otvorením pozície (`POST /v2/leverage`) a `size` je v
  base-asset jednotkách, nie notional USD. Overené voči
  https://docs.strikefinance.org/api/trade/orders a
  https://docs.strikefinance.org/api/trade/trading.
- NVDA, ADA a GOLD majú nižšiu default paku a širšie SL/TP % než NAS100 (viz
  `.env.example`) — sú kalibrované na vyššiu typickú volatilitu jednotlivej akcie
  resp. krypta, ale over si to sám na pár dňoch DRY_RUN dát pred ostrým behom.
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
- `STRIKE_NAS100_SYMBOL` / `STRIKE_NVDA_SYMBOL` / `STRIKE_ADA_SYMBOL` / `STRIKE_GOLD_SYMBOL` — presný
  symbol/market identifikátor pre daný asset na Strike (zisti cez `get_markets()` v `strike_client.py`)
- `TWITTER_BEARER_TOKEN` — voliteľné, X API v2 (platený tier na zmysluplný recent search)
- `DATABASE_URL` — pre trvalé uloženie histórie obchodov použi Railway Postgres plugin
  (SQLite súbor na Railway sa stratí pri každom redeployi!)
- `DRY_RUN` — `true`/`false` — **zdieľané pre všetky assety**
- `TRADE_INTERVAL_HOURS` — ako často beží analytický cyklus (napr. `4`) — **zdieľané pre všetky assety**
- `MONITOR_INTERVAL_MINUTES` — ako často sa kontrolujú otvorené pozície (napr. `10`) — zdieľané
- `POSITION_MAX_HOURS` — max. držanie pozície pred force-close — zdieľané
- `ENABLE_NVDA` / `ENABLE_ADA` / `ENABLE_GOLD` — `true`/`false`, vypnutie/zapnutie daného bota (NAS100 beží vždy)
- `MIN_CONFIDENCE`, `NVDA_MIN_CONFIDENCE`, `ADA_MIN_CONFIDENCE`, `GOLD_MIN_CONFIDENCE` - min. confidence
  pre otvorenie obchodu (per asset)
- `MARGIN_USD`/`NVDA_MARGIN_USD`/`ADA_MARGIN_USD`/`GOLD_MARGIN_USD`,
  `LEVERAGE`/`NVDA_LEVERAGE`/`ADA_LEVERAGE`/`GOLD_LEVERAGE` -
  fixny margin+leverage na kazdy obchod (notional = margin x leverage), per asset
- `DEFAULT_SL_PCT`/`NVDA_SL_PCT`/`ADA_SL_PCT`/`GOLD_SL_PCT`,
  `DEFAULT_TP_PCT`/`NVDA_TP_PCT`/`ADA_TP_PCT`/`GOLD_TP_PCT` - cielove
  SL/TP ako % od live ceny (per asset); Claude navrhuje presnu vzdialenost, ktora sa oreze do
  0.1x-5x tychto hodnot (nikdy nezablokuje vstup - viz `risk_manager.py`)

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
| `assets.py` | registry assetov (NAS100/NVDA/ADA/GOLD) - symbol, TA ticker, SL/TP%, leverage, margin, min_confidence |
| `db.py` | SQLAlchemy modely `Trade`/`CycleLog` (obe majú `symbol`) + session |
| `market_data.py` | OHLCV + TA indikátory (per asset), zdieľaný cross-market/session/BTC-proxy fetch |
| `social_sentiment.py` | (voliteľné) X/Twitter sentiment, per asset query |
| `claude_analyst.py` | zostaví per-asset prompt, zavolá Claude (s `web_search` nástrojom), parsuje JSON rozhodnutie |
| `strike_client.py` | Ed25519 podpisovanie, open/close position, get positions/markets |
| `risk_manager.py` | position sizing; jediny gate na vstup je confidence, SL/TP sa vzdy pouzije (nikdy nezablokuje) |
| `trade_cycle.py` | `run_all_cycles()` - zdieľaný makro fetch + loop cez aktívne assety |
| `position_monitor.py` | kontrola/zatváranie otvorených pozícií naprieč assetmi |
