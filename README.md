# NAS100 Sentiment Bot (Strike Finance)

Automatizovaný obchodný bot pre NAS100 na Strike Finance (`app.strikefinance.org/trade/nas100`).

**Ako to funguje (jeden cyklus):**

1. `market_data.py` stiahne cenové dáta pre NAS100 (proxy cez `^NDX` / `NQ=F`) a spočíta TA
   indikátory (RSI, MACD, EMA20/50/200, Bollinger Bands, ATR, trend).
2. (voliteľne) `social_sentiment.py` stiahne najnovšie tweety/posty s relevantnými
   hashtagmi/cashtagmi cez X API.
3. `claude_analyst.py` pošle TA dáta do Claude (Anthropic API) s povoleným vstavaným
   **`web_search`** nástrojom — Claude si podľa potreby sám vyhľadá čerstvé správy
   o NAS100/megacap firmách priamo cez Anthropic API (žiadny NewsAPI kľúč netreba) —
   a vráti **štruktúrovanú JSON odpoveď**: smer (long/short/none), confidence 0-100,
   navrhovaný stop-loss a take-profit a krátke zdôvodnenie.
5. `risk_manager.py` overí rozhodnutie voči risk pravidlám (min. confidence, max
   leverage, rozumný pomer SL/TP k volatilite (ATR), či už nie je otvorená pozícia).
6. Ak prejde kontrolou, `strike_client.py` otvorí pozíciu cez Strike API s daným SL/TP.
7. Obchod sa zapíše do DB (`db.py`) s časom otvorenia a expiráciou o 24h.
8. `position_monitor.py` beží nezávisle v kratších intervaloch a:
   - zisťuje, či pozícia už bola zavretá burzou (SL/TP/likvidácia hit) → zapíše čas a PnL,
   - ak od otvorenia uplynulo 24h a pozícia je stále otvorená → force-close cez API a zapíše PnL.

`main.py` toto všetko spúšťa na pozadí cez scheduler (APScheduler) — beží ako
jeden dlhodobo bežiaci proces na Railway (worker service).

## ⚠️ Dôležité upozornenia

- **Toto obchoduje s reálnymi peniazmi na pákový produkt.** SL/TP sa nastavujú cez
  bracket "strategy" objednávku (`POST /v2/order/strategy`, polia `tp_order`/`sl_order`),
  leverage sa nastavuje samostatne pred otvorením pozície (`POST /v2/leverage`) a
  `size` je v base-asset jednotkách (počet NAS100 kontraktov), nie notional USD.
  Overené voči https://docs.strikefinance.org/api/trade/orders a
  https://docs.strikefinance.org/api/trade/trading.
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
- `STRIKE_NAS100_SYMBOL` — presný symbol/market identifikátor pre NAS100 na Strike (zisti cez `get_markets()` v `strike_client.py`)
- `TWITTER_BEARER_TOKEN` — voliteľné, X API v2 (platený tier na zmysluplný recent search)
- `DATABASE_URL` — pre trvalé uloženie histórie obchodov použi Railway Postgres plugin
  (SQLite súbor na Railway sa stratí pri každom redeployi!)
- `DRY_RUN` — `true`/`false`
- `TRADE_INTERVAL_HOURS` — ako často sa má bežať analytický cyklus (napr. `4`)
- `MONITOR_INTERVAL_MINUTES` — ako často sa kontrolujú otvorené pozície (napr. `10`)
- `RISK_PCT`, `MAX_LEVERAGE`, `MIN_CONFIDENCE`

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
| `config.py` | centrálne env premenné |
| `db.py` | SQLAlchemy model `Trade` + session |
| `market_data.py` | OHLCV + TA indikátory pre NAS100 |
| `social_sentiment.py` | (voliteľné) X/Twitter sentiment |
| `claude_analyst.py` | zostaví prompt, zavolá Claude (s `web_search` nástrojom), parsuje JSON rozhodnutie |
| `strike_client.py` | Ed25519 podpisovanie, open/close position, get positions/markets |
| `risk_manager.py` | position sizing + sanity kontroly pred exekúciou |
| `trade_cycle.py` | orchestrácia jedného analytického cyklu |
| `position_monitor.py` | kontrola/zatváranie otvorených pozícií |
