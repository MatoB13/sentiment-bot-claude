"""
Lahky 1-minutovy poller Strike mark_price - primarny zdroj hodinovych OHLC
sviecok pre TA (viz market_data.get_price_history), namiesto yfinance ktore
mimo obchodnych hodin/cez vikend pre NAS100/NVDA/GOLD zamrzne (futures/akcia
su vtedy zatvorene), zatial co Strike perpy obchoduju nonstop. Jeden bulk
GET /v2/markets call pokryje vsetky aktivne tickery naraz - ziadne extra
platene/premium data, len live mark_price.
"""
from datetime import datetime, timezone

import assets
import market_data
import strike_client
from db import AccountSnapshot, FundingRateBar, PriceBar, get_session


def _microstructure(market: dict) -> dict | None:
    """Spread / nerovnovaha knihy / premium z jedneho /v2/markets zaznamu.

    2026-09-01 - vsetky tri sa daju spocitat z dat, ktore poller uz kazdu minutu
    stahuje; doteraz sa zahadzovali. Spread uz Claude videl (trade_cycle
    ._add_spread_to_ta od 2026-08-29), ale VELKOSTI na najlepsej cene a rozdiel
    mark vs index nie - pritom prave tie hovoria nieco, co z ceny nevidno:
    na ktoru stranu knihy sa tlaci a ci perpetual ide s premiou voci indexu.

    Vracia None, ak trh nema pouzitelnu knihu (velmi tenke synteticke trackery
    maju obcas prazdne bid/ask) - volajuci to jednoducho preskoci, nie je to chyba."""
    try:
        bid = float(market.get("bid1_price") or 0)
        ask = float(market.get("ask1_price") or 0)
        mark = float(market.get("mark_price") or 0)
    except (TypeError, ValueError):
        return None
    if not (bid > 0 and ask >= bid and mark > 0):
        return None

    out = {"spread_pct": (ask - bid) / mark * 100}

    try:
        bsz = float(market.get("bid1_size") or 0)
        asz = float(market.get("ask1_size") or 0)
        if bsz + asz > 0:
            # -1 = vsetko na strane ask (predajny tlak), +1 = vsetko na bid.
            out["book_imbalance"] = (bsz - asz) / (bsz + asz)
    except (TypeError, ValueError):
        pass

    try:
        index = float(market.get("index_price") or 0)
        if index > 0:
            out["premium_pct"] = (mark - index) / index * 100
    except (TypeError, ValueError):
        pass
    return out


def _accumulate_micro(bar, micro: dict | None) -> None:
    """Priebezny sucet + pocitadlo, aby sa dal priemer za hodinu spocitat bez
    drzania jednotlivych vzoriek. micro_samples je spolocny delitel; ked nejaka
    zlozka pre dany trh chyba (napr. prazdna kniha), ostane jej sucet None a
    citanie ju proste preskoci."""
    if not micro:
        return
    bar.micro_samples = (bar.micro_samples or 0) + 1
    for field, key in (("spread_pct_sum", "spread_pct"),
                       ("book_imbalance_sum", "book_imbalance"),
                       ("premium_pct_sum", "premium_pct")):
        val = micro.get(key)
        if val is None:
            continue
        setattr(bar, field, (getattr(bar, field) or 0.0) + val)


def poll_prices() -> None:
    """Zavola sa kazdu minutu (viz main.py) - vytvori/aktualizuje PriceBar
    riadok pre AKTUALNU hodinu KAZDEHO assetu v registri (nie len enabled_assets()
    - viz nizsie), open pri prvom tiku danej hodiny, high/low priebezne,
    close = najnovsia cena, updated_at = presny cas tohto tiku (2026-08-15,
    viz PriceBar.updated_at - na rozdiel od hour_start, ktory je vzdy
    zaokruhleny na zaciatok hodiny, toto monitor-web pouziva na zobrazenie
    skutocne aktualneho casu pri nerealizovanom PnL). Rovnaky tik navyse
    zbiera aj aktualnu funding rate do FundingRateBar (viz nizsie) - ROVNAKA
    /v2/markets odpoved uz obsahuje 'funding_rate' pole, ziadny extra naklad.

    ZAMERNE ALL_ASSETS, nie enabled_assets() (2026-08-14, viz AAOI/MINIMAX
    pridanie): pozastavene/este-nezapnute tickery (NVDA, AAOI, MINIMAX) takto
    priebezne zbieraju cenovu historiu, aby mali pri buducom zapnuti uz
    nazbierane data namiesto zaciatku od nuly. Ziadny extra naklad - /v2/markets
    je aj tak JEDEN bulk GET pokryvajuci vsetky symboly naraz, nezavisle od
    toho, kolko z nich potom nizsie iterujeme."""
    try:
        markets = strike_client.get_markets()
    except Exception as e:
        print(f"[price_poller] Strike /v2/markets zlyhalo, preskakujem tento tik: {e}")
        return

    # Ziva zostava uctu (2026-08-17, na ziadost pouzivatela - sledovanie volnej
    # likvidity) - samostatny GET, nezavisly od /v2/markets vyssie, preto
    # oddelene osetreny: jeho zlyhanie nesmie zablokovat cenove sviecky nizsie.
    account = None
    try:
        account = strike_client.get_account()
    except Exception as e:
        print(f"[price_poller] Strike /v2/account zlyhalo, preskakujem tento tik: {e}")

    prices = {m.get("symbol"): m.get("mark_price") for m in markets}
    funding_rates = {m.get("symbol"): m.get("funding_rate") for m in markets}
    micro_by_symbol = {m.get("symbol"): _microstructure(m) for m in markets}
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)

    session = get_session()
    try:
        updated = 0
        for asset in assets.ALL_ASSETS:
            symbol = asset["strike_symbol"]
            raw_price = prices.get(symbol)
            if raw_price is None:
                print(f"[price_poller] {symbol} chyba v /v2/markets odpovedi, preskakujem.")
                continue
            price = float(raw_price)

            bar = (
                session.query(PriceBar)
                .filter(PriceBar.symbol == symbol, PriceBar.hour_start == hour_start)
                .first()
            )
            if bar is None:
                bar = PriceBar(symbol=symbol, hour_start=hour_start,
                               open=price, high=price, low=price, close=price,
                               updated_at=now.replace(tzinfo=None))
                session.add(bar)
            else:
                bar.high = max(bar.high, price)
                bar.low = min(bar.low, price)
                bar.close = price
                bar.updated_at = now.replace(tzinfo=None)
            _accumulate_micro(bar, micro_by_symbol.get(symbol))
            updated += 1

            raw_funding = funding_rates.get(symbol)
            if raw_funding is not None:
                frate = float(raw_funding)
                fbar = (
                    session.query(FundingRateBar)
                    .filter(FundingRateBar.symbol == symbol, FundingRateBar.hour_start == hour_start)
                    .first()
                )
                if fbar is None:
                    session.add(FundingRateBar(symbol=symbol, hour_start=hour_start, funding_rate=frate))
                else:
                    fbar.funding_rate = frate

        if account is not None:
            snapshot = session.query(AccountSnapshot).filter(AccountSnapshot.id == 1).first()
            if snapshot is None:
                snapshot = AccountSnapshot(id=1)
                session.add(snapshot)
            snapshot.wallet_balance = float(account["wallet_balance"])
            snapshot.available_balance = float(account["available_balance"])
            snapshot.margin_balance = float(account["margin_balance"])
            snapshot.unrealized_pnl = float(account["unrealized_pnl"])
            snapshot.total_margin = float(account["total_margin"])
            snapshot.updated_at = now.replace(tzinfo=None)

        session.commit()
        print(f"[price_poller] {updated}/{len(assets.ALL_ASSETS)} tickerov "
              f"aktualizovanych (hodina {hour_start.isoformat()}).")
    except Exception as e:
        print(f"[price_poller] Zapis zlyhal: {e}")
        session.rollback()
    finally:
        session.close()


def backfill_if_empty() -> None:
    """JEDNORAZOVY backfill - ak pre asset este NIE JE v price_bars ziadny
    zaznam, natiahne poslednych ~30 dni hodinovych sviecok z yfinance (alebo
    CoinGecko, viz asset["coingecko_id"] - momentalne len HYPE, ktore na
    yfinance/Binance nema pokrytie) ako pociatocnu historiu (rovnaky zdroj/
    rozsah ako predtym pouzivany market_data.fetch_ohlcv). Cez vikend/mimo
    obchodnych hodin budu v tejto historii prirodzene diery (yfinance tam
    ziadne data nema pre futures/akcie) - to je akceptovany jednorazovy
    naklad, dalej uz bezi vlastny poller. Idempotentne (kontrola 'uz existuje
    aspon 1 zaznam') - bezpecne volat pri kazdom starte, po prvom uspesnom
    behu uz nic nerobi.

    ALL_ASSETS (nie enabled_assets()) z rovnakeho dovodu ako v poll_prices()
    vyssie - pozastavene/este-nezapnute tickery maju dostat rovnaky jednorazovy
    backfill ako aktivne (pre MINIMAX bez ziadneho externeho zdroja aj tak
    ticho no-op-ne, viz nizsie)."""
    session = get_session()
    try:
        for asset in assets.ALL_ASSETS:
            symbol = asset["strike_symbol"]
            already_has_data = session.query(PriceBar.id).filter(PriceBar.symbol == symbol).first()
            if already_has_data:
                continue

            if asset.get("yf_volume_only"):
                # yf_symbol ma NEKOMPATIBILNU cenovu skalu s tymto Strike
                # syntetickym trackerom (viz assets.py komentar pri SKHYNIX,
                # produkcny incident 2026-08-09) - ziaden backfill, radsej
                # prazdna historia nez zaplnenie zlou skalou. Vlastny 1-min
                # poller nizsie (poll_prices) ju postupne nazbiera sam.
                print(f"[price_poller] Preskakujem OHLC backfill pre {symbol}: yfinance "
                      f"({asset['yf_symbol']}) ma nekompatibilnu cenovu skalu.")
                continue

            source = "CoinGecko" if asset.get("coingecko_id") else "yfinance"
            try:
                if asset.get("coingecko_id"):
                    df = market_data.fetch_ohlcv_coingecko(asset["coingecko_id"])
                else:
                    df = market_data.fetch_ohlcv(asset["yf_symbol"], asset.get("yf_fallback"))
            except Exception as e:
                print(f"[price_poller] Backfill pre {symbol} zlyhal ({source}): {e}")
                continue
            if df.empty:
                print(f"[price_poller] Backfill pre {symbol}: {source} nevratil ziadne data.")
                continue

            idx = df.index.tz_convert("UTC").tz_localize(None) if df.index.tz is not None else df.index
            count = 0
            for ts, row in zip(idx, df.itertuples()):
                session.add(PriceBar(
                    symbol=symbol, hour_start=ts.replace(minute=0, second=0, microsecond=0),
                    open=float(row.open), high=float(row.high),
                    low=float(row.low), close=float(row.close),
                ))
                count += 1
            session.commit()
            print(f"[price_poller] Backfill pre {symbol}: {count} sviecok z {source} "
                  "(vikendove/mimo-hodinove diery su ocakavane, dalej uz bezi vlastny poller).")
    finally:
        session.close()


if __name__ == "__main__":
    backfill_if_empty()
    poll_prices()
