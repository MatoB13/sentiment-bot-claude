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
from db import PriceBar, get_session


def poll_prices() -> None:
    """Zavola sa kazdu minutu (viz main.py) - vytvori/aktualizuje PriceBar
    riadok pre AKTUALNU hodinu kazdeho aktivneho assetu (open pri prvom
    tiku danej hodiny, high/low priebezne, close = najnovsia cena)."""
    try:
        markets = strike_client.get_markets()
    except Exception as e:
        print(f"[price_poller] Strike /v2/markets zlyhalo, preskakujem tento tik: {e}")
        return

    prices = {m.get("symbol"): m.get("mark_price") for m in markets}
    hour_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)

    session = get_session()
    try:
        updated = 0
        for asset in assets.enabled_assets():
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
                session.add(PriceBar(symbol=symbol, hour_start=hour_start,
                                      open=price, high=price, low=price, close=price))
            else:
                bar.high = max(bar.high, price)
                bar.low = min(bar.low, price)
                bar.close = price
            updated += 1
        session.commit()
        print(f"[price_poller] {updated}/{len(assets.enabled_assets())} tickerov "
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
    behu uz nic nerobi."""
    session = get_session()
    try:
        for asset in assets.enabled_assets():
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
