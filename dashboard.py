"""
Lokalny dashboard pre NAS100 Sentiment Bot (Streamlit).
Spustenie: streamlit run dashboard.py

Nie je sucastou produkcneho Railway workera (Procfile spusta len main.py) -
toto je len lokalny nastroj na kontrolu/ladenie.
"""
import contextlib
import importlib
import io
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import dotenv_values

st.set_page_config(page_title="Sentiment Bot (multi-asset)", layout="wide")

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Zdielane pre vsetky assety (NAS100/NVDA/ADA bezia v tom istom cykle).
SHARED_NUMERIC = [
    ("TRADE_INTERVAL_HOURS", float, "Interval POCAS trading hours - ZDIELANE pre vsetky assety (hodiny)"),
    ("OFF_HOURS_INTERVAL_HOURS", float, "Interval mimo trading hours (len NAS100/NVDA/GOLD, hodiny)"),
    ("WEEKEND_INTERVAL_HOURS", float, "Interval cez vikend (len NAS100/NVDA/GOLD, hodiny)"),
    ("TRADING_HOURS_START_UTC", int, "Zaciatok trading hours (UTC hodina)"),
    ("TRADING_HOURS_END_UTC", int, "Koniec trading hours (UTC hodina)"),
    ("MONITOR_INTERVAL_MINUTES", float, "Ako casto sa kontroluju otvorene pozicie (minuty)"),
    ("POSITION_MAX_HOURS", float, "Max. drzanie pozicie pred force-close (hodiny)"),
]

# Per-asset risk parametre (NAS100 pouziva povodne bezpredponove nazvy env premennych).
ASSET_NUMERIC = {
    "NAS100": [
        ("MIN_CONFIDENCE", int, "Minimalna confidence pre otvorenie obchodu (0-100)"),
        ("MARGIN_USD", float, "Fixna marza na jeden obchod (USD)"),
        ("LEVERAGE", int, "Fixna paka (notional = MARGIN_USD x LEVERAGE)"),
        ("DEFAULT_SL_PCT", float, "Cielova SL vzdialenost (% od live ceny)"),
        ("DEFAULT_TP_PCT", float, "Cielova TP vzdialenost (% od live ceny)"),
    ],
    "NVDA": [
        ("NVDA_MIN_CONFIDENCE", int, "Minimalna confidence pre otvorenie obchodu (0-100)"),
        ("NVDA_MARGIN_USD", float, "Fixna marza na jeden obchod (USD)"),
        ("NVDA_LEVERAGE", int, "Fixna paka (notional = margin x leverage)"),
        ("NVDA_SL_PCT", float, "Cielova SL vzdialenost (% od live ceny)"),
        ("NVDA_TP_PCT", float, "Cielova TP vzdialenost (% od live ceny)"),
    ],
    "ADA": [
        ("ADA_MIN_CONFIDENCE", int, "Minimalna confidence pre otvorenie obchodu (0-100)"),
        ("ADA_MARGIN_USD", float, "Fixna marza na jeden obchod (USD)"),
        ("ADA_LEVERAGE", int, "Fixna paka (notional = margin x leverage)"),
        ("ADA_SL_PCT", float, "Cielova SL vzdialenost (% od live ceny)"),
        ("ADA_TP_PCT", float, "Cielova TP vzdialenost (% od live ceny)"),
    ],
    "GOLD": [
        ("GOLD_MIN_CONFIDENCE", int, "Minimalna confidence pre otvorenie obchodu (0-100)"),
        ("GOLD_MARGIN_USD", float, "Fixna marza na jeden obchod (USD)"),
        ("GOLD_LEVERAGE", int, "Fixna paka (notional = margin x leverage)"),
        ("GOLD_SL_PCT", float, "Cielova SL vzdialenost (% od live ceny)"),
        ("GOLD_TP_PCT", float, "Cielova TP vzdialenost (% od live ceny)"),
    ],
}

# Spatna kompatibilita s povodnym menom pouzivanym nizsie v kode.
EDITABLE_NUMERIC = SHARED_NUMERIC + ASSET_NUMERIC["NAS100"]


def load_env() -> dict:
    return dotenv_values(ENV_PATH)


def save_env_values(updates: dict) -> None:
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    keys_left = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in keys_left:
            lines[i] = f"{key}={keys_left.pop(key)}\n"

    for key, value in keys_left.items():
        lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def reload_app_modules():
    """Znovunacita config.py (a moduly ktore z neho citaju config.X pri kazdom volani),
    aby sa prejavili zmeny z .env bez restartu streamlit servera."""
    import config
    importlib.reload(config)


st.title("Sentiment Bot (NAS100 + NVDA + ADA + GOLD) — Dashboard")

env_values = load_env()

tabs = st.tabs(["Konfiguracia", "Live trh", "Spustit cyklus", "Historia obchodov", "Ucet na Strike"])

# --- Konfiguracia ---
with tabs[0]:
    st.subheader("Zdielane parametre (vsetky assety bezia v tom istom cykle)")
    st.caption("Zmeny sa ulozia priamo do .env a prejavia sa hned (bez restartu).")

    dry_run_current = str(env_values.get("DRY_RUN", "true")).lower() in ("1", "true", "yes", "on")
    dry_run_new = st.toggle("DRY_RUN (ak vypnute, bot posiela REALNE obchody na Strike!)",
                             value=dry_run_current)
    if dry_run_current and not dry_run_new:
        st.warning("Vypinas DRY_RUN. Dalsie spustenie cyklu posle SKUTOCNE obchody na Strike "
                   "(za vsetky aktivne assety) s realnymi penazmi. Uisti sa, ze si over vsetko "
                   "v tomto dashboarde predtym.")

    new_values = {}

    def _render_numeric(fields, cols_count=2):
        cols = st.columns(cols_count)
        for idx, (key, cast, help_text) in enumerate(fields):
            col = cols[idx % cols_count]
            current = env_values.get(key, "")
            try:
                current_cast = cast(current)
            except (TypeError, ValueError):
                current_cast = 0
            if cast is int:
                new_values[key] = col.number_input(key, value=int(current_cast), step=1, help=help_text)
            else:
                new_values[key] = col.number_input(key, value=float(current_cast), help=help_text)

    _render_numeric(SHARED_NUMERIC)

    ASSET_NAMES = ["NAS100", "NVDA", "ADA", "GOLD"]
    OPTIONAL_ASSETS = ["NVDA", "ADA", "GOLD"]  # NAS100 beri vzdy, ostatne su ENABLE_* prepinatelne

    asset_tabs = st.tabs(ASSET_NAMES)
    for asset_name, asset_tab in zip(ASSET_NAMES, asset_tabs):
        with asset_tab:
            _render_numeric(ASSET_NUMERIC[asset_name])

    enable_cols = st.columns(len(OPTIONAL_ASSETS))
    enable_new = {}
    for col, asset_name in zip(enable_cols, OPTIONAL_ASSETS):
        env_key = f"ENABLE_{asset_name}"
        current = str(env_values.get(env_key, "true")).lower() in ("1", "true", "yes", "on")
        enable_new[env_key] = col.toggle(env_key, value=current)

    if st.button("Ulozit konfiguraciu", type="primary"):
        to_save = {k: str(v) for k, v in new_values.items()}
        to_save["DRY_RUN"] = "true" if dry_run_new else "false"
        for env_key, value in enable_new.items():
            to_save[env_key] = "true" if value else "false"
        save_env_values(to_save)
        reload_app_modules()
        st.success("Ulozene do .env.")
        st.rerun()

    with st.expander("API kluce (skryte, needituju sa tu)"):
        for secret_key in ["ANTHROPIC_API_KEY", "STRIKE_API_PRIVATE_KEY", "STRIKE_API_PUBLIC_KEY"]:
            val = env_values.get(secret_key, "")
            masked = f"{'*' * max(len(val) - 4, 0)}{val[-4:]}" if val else "(prazdne)"
            st.text(f"{secret_key}: {masked}")
        st.caption("Uprav priamo v .env, ak treba zmenit kluce.")

# --- Live trh ---
with tabs[1]:
    import assets as assets_module

    asset_choice = st.selectbox("Asset", [a["name"] for a in assets_module.ALL_ASSETS])
    selected_asset = {a["name"]: a for a in assets_module.ALL_ASSETS}[asset_choice]

    if st.button("Nacitat live dáta"):
        st.session_state["refresh_market"] = True

    if st.session_state.get("refresh_market", True):
        with st.spinner("Nacitavam..."):
            import strike_client
            import market_data

            try:
                market_meta = strike_client.get_market(selected_asset["strike_symbol"])
                st.metric("Strike mark_price", market_meta.get("mark_price"))
                st.json({k: market_meta[k] for k in
                         ["symbol", "mark_price", "index_price", "last_price", "bid1_price",
                          "ask1_price", "funding_rate", "order_tick_price", "order_market_step_size",
                          "order_market_min_size", "order_min_notional"]})
            except Exception as e:
                st.error(f"Strike API chyba: {e}")

            try:
                ta = market_data.get_market_snapshot(
                    selected_asset["yf_symbol"], selected_asset.get("yf_fallback"),
                    include_volume=selected_asset.get("include_volume", False),
                )
                st.subheader(f"Technicka analyza {asset_choice} (yfinance proxy)")
                st.json(ta)
            except Exception as e:
                st.error(f"market_data chyba: {e}")

            try:
                st.subheader("Cross-market kontext (zdielane pre vsetky assety)")
                st.json(market_data.get_cross_market_snapshot())
            except Exception as e:
                st.error(f"cross-market chyba: {e}")

            try:
                st.subheader("Session alignment (zdielane pre vsetky assety)")
                st.json(market_data.get_session_snapshot())
            except Exception as e:
                st.error(f"session chyba: {e}")

            if selected_asset.get("needs_btc_proxy"):
                try:
                    st.subheader("BTC proxy (krypto-makro pre ADA)")
                    st.json(market_data.get_btc_proxy_snapshot())
                except Exception as e:
                    st.error(f"BTC proxy chyba: {e}")

# --- Spustit cyklus ---
with tabs[2]:
    st.caption("Spusti presne to iste, co by spustil scheduler v main.py, priamo teraz.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Spustit analyticky cyklus teraz (vsetky assety)", type="primary"):
            import trade_cycle
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    trade_cycle.run_all_cycles()
                except Exception as e:
                    print(f"CHYBA: {e}")
            st.code(buf.getvalue() or "(ziadny vystup)")

    with col2:
        if st.button("Skontrolovat otvorene pozicie teraz"):
            import position_monitor
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    position_monitor.check_open_trades()
                except Exception as e:
                    print(f"CHYBA: {e}")
            st.code(buf.getvalue() or "(ziadny vystup)")

# --- Historia obchodov ---
with tabs[3]:
    from db import Trade, get_session

    if st.button("Obnovit historiu"):
        st.rerun()

    session = get_session()
    try:
        trades = session.query(Trade).order_by(Trade.id.desc()).all()
    finally:
        session.close()

    if not trades:
        st.info("Zatial ziadne obchody v DB.")
    else:
        rows = [{
            "id": t.id,
            "symbol": t.symbol,
            "status": t.status,
            "direction": t.direction,
            "confidence": t.confidence,
            "entry_price": t.entry_price,
            "stop_loss_price": t.stop_loss_price,
            "take_profit_price": t.take_profit_price,
            "leverage": t.leverage,
            "size": t.size,
            "notional_usd": t.notional_usd,
            "margin_usd": t.margin_usd,
            "pnl_usd": t.pnl_usd,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "close_reason": t.close_reason,
            "reasoning": t.reasoning,
        } for t in trades]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

# --- Ucet na Strike ---
with tabs[4]:
    if st.button("Nacitat stav uctu"):
        import strike_client
        try:
            account = strike_client.get_account()
            st.json(account)
        except Exception as e:
            st.error(f"Strike API chyba: {e}")

        try:
            positions = strike_client.get_positions()
            st.subheader("Otvorene pozicie na Strike")
            st.json(positions)
        except Exception as e:
            st.error(f"Strike API chyba: {e}")
