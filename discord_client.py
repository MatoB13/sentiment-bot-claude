"""Discord webhook notifikacie - zdarma, ziaden bot token netreba, len URL
webhooku vytvoreneho priamo v kanale (Channel Settings -> Integrations ->
Webhooks). Pouziva sa LEN na upozornenia (fire-and-forget) - zlyhanie NESMIE
nikdy ovplyvnit skutocne obchodovanie, preto kazda funkcia tu ticho zlyha
(vypise chybu do logu, nikdy nevyhodi vynimku volajucemu)."""
import time
from datetime import datetime, timezone

import requests

import config

_DIRECTION_COLOR = {"long": 2123412, "short": 10181046}  # Discord embed color (decimal), tmavomodra/fialova
_PNL_COLOR = {"win": 3066993, "loss": 15158332}  # zelena/cervena, podla vysledku (viz notify_trade_closed)

# 2026-08-19 (na ziadost pouzivatela) - notifikacie chodia aj na hodinky, kde
# sa zvycajne zobrazi LEN prvy riadok/content, nie cele embed telo - preto
# VZDY prvy znak(y) (kratky pismenkovy kod), potom ticker, potom suma. Povodne
# farebne sipka-glyfy (skusane naozivo, viz o par commitov skor) pouzivatel po
# zvazeni zrusil v prospech jednoduchsich pismenkovych skratiek - OL/OS
# (otvorenie: OTVORENIE, nie vysledok) vs. L/P (zatvorenie: VYSLEDOK, nie
# smer) - rovnaky dovod odlisenia ako predtym pri farbach (zisková SHORT musi
# byt odlisitelna od otvorenia SHORT).
_LONG_LABEL = "OL"    # Open Long
_SHORT_LABEL = "OS"   # Open Short
_PROFIT_LABEL = "P"   # zisk (Profit)
_LOSS_LABEL = "L"     # strata (Loss)

# 2026-08-19 (na ziadost pouzivatela) - na PC notifikacia len ticho pipne bez
# vyrazneho odznaku/unread pocitadla, ktore pouzivatel potrebuje, aby si
# nevsimnutu spravu nepretiekol. Discord vsak takto vyrazne oznaci LEN
# spravy s @mention (priamy tag alebo @everyone) - webhook spravy bez
# akehokolvek tagu su "tiche". Pouzivatel vedome zvolil @everyone (pred
# alternativou vlastneho <@user_id> tagu) - viz AskUserQuestion v tejto
# session. Pripojene AZ za headline (nie pred), aby prvych par znakov v
# skratenom watch nahlade zostalo LEN glyf+ticker+suma.
_EVERYONE_PING = "@everyone"


def _short_ticker(symbol: str) -> str:
    """'ADA-USD' -> 'ADA' - kratsie pre watch notifikaciu (rovnaky vzor ako
    monitor-web tickerLabel())."""
    return symbol.removesuffix("-USD") if symbol else symbol

# 1 retry (2026-08-19, crash-scenario audit) - Discord webhook ma prisny
# rate limit; pri hromadnom zatvoreni viacerych pozicii naraz (kazda vlastna
# notifikacia) by niekolko notifikacii v rychlom slede mohlo dostat 429 a
# doteraz sa ticho zahodili bez akehokolvek pokusu o retry.
_MAX_RETRIES = 1
_RETRY_DELAY_SECONDS = 2


def _post_webhook(payload: dict, label: str) -> bool:
    """Zdielana odosielacia logika - fire-and-forget, ale s jednym retry pri
    prechodnej chybe (vratane Discord 429). Nikdy nevyhodi vynimku volajucemu.

    2026-08-31 (UNITREE #155 incident) - vracia True/False (predtym vzdy
    None) namiesto tichej straty vysledku, aby si volajuci (viz
    notify_trade_opened/notify_trade_closed) mohol PERZISTOVAT dedup flag
    LEN po skutocnom uspechu - inak (povodne spravanie) sa flag nastavoval
    hned pri pokuse o odoslanie, takze prechodne zlyhanie (aj po retry)
    znamenalo NAVZDY stratenu notifikaciu bez akejkolvek stopy."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            if attempt < _MAX_RETRIES:
                print(f"[discord_client] {label} zlyhala, skusam znova o "
                      f"{_RETRY_DELAY_SECONDS}s: {e}")
                time.sleep(_RETRY_DELAY_SECONDS)
            else:
                print(f"[discord_client] {label} zlyhala: {e}")
                return False


def notify_trade_opened(asset: dict, sized: dict) -> bool:
    """Zavola sa HNED PO uspesnom otvoreni pozicie (viz trade_cycle.py) - len
    ak je DISCORD_WEBHOOK_URL nastavene, inak ticho no-op (rovnaky vzor ako
    ostatne volitelne doplnky - EIA/FRED/Marketaux). Vracia True/False (viz
    _post_webhook) - volajuci to pouziva na perzistovanie dedup flagu LEN po
    skutocnom uspechu (viz trade_cycle.py + position_monitor.
    _backfill_missing_open_notifications)."""
    if not config.DISCORD_WEBHOOK_URL:
        return False
    # POZOR (2026-08-17 oprava): risk_manager.validate_and_size vracia
    # "Long"/"Short" (velke pismeno), nie "long"/"short" - povodne porovnanie
    # direction == "long" preto NIKDY nebolo True a farba/emoji padali vzdy
    # na default/cervenu bez ohladu na skutocny smer. .lower() to zjednoti.
    direction = sized["direction"]
    direction_key = direction.lower()
    label = _LONG_LABEL if direction_key == "long" else _SHORT_LABEL
    # Label, ticker, suma (notional = skutocna velkost pozicie, NIE margin) -
    # v tomto presnom poradi, aby to bolo citatelne aj v skratenom watch
    # nahlade (viz modulovy docstring vyssie).
    headline = f"{label} {asset['name']} ${sized['notional_usd']:.0f}"
    payload = {
        "content": f"{headline} {_EVERYONE_PING}",
        "embeds": [{
            "title": f"{headline} - {direction.upper()} otvorena",
            "color": _DIRECTION_COLOR.get(direction_key, 3447003),
            "fields": [
                {"name": "Confidence", "value": str(sized["confidence"]), "inline": True},
                {"name": "Entry", "value": str(sized["entry_price"]), "inline": True},
                {"name": "Leverage", "value": f"{sized['leverage']}x", "inline": True},
                {"name": "Stop-loss", "value": str(sized["stop_loss_price"]), "inline": True},
                {"name": "Take-profit", "value": str(sized["take_profit_price"]), "inline": True},
                {"name": "Margin", "value": f"${sized['margin_usd']:.2f}", "inline": True},
            ],
        }]
    }
    return _post_webhook(payload, "Notifikacia o otvoreni")


_CLOSE_REASON_LABELS = {
    "take_profit": "Take-profit",
    "stop_loss": "Stop-loss",
    "liquidation": "Likvidacia",
    "force_closed_by_bot": "Timeout (max. doba drzania)",
    "ai_early_close": "AI predcasne zatvorenie (vysoka istota)",
}


def notify_trade_closed(symbol: str, closed_trade: dict) -> bool:
    """Zavola sa po TP/SL/likvidacii/timeoute (viz position_monitor.
    _check_and_queue_close_notification) - ZAMERNE NIE pri manual_kill_switch
    (pouzivatel poziciu zatvoril sam, netreba mu to pripominat). Vracia
    True/False - viz notify_trade_opened."""
    if not config.DISCORD_WEBHOOK_URL:
        return False
    pnl = closed_trade.get("pnl_usd")
    # 2026-08-17 oprava: farba pri ZATVORENI ma vyjadrovat VYSLEDOK (zisk/strata),
    # nie smer pozicie - predtym sa farba (na rozdiel od uz spravneho emoji nizsie)
    # riadila direction, takze napr. zisková SHORT pozicia mala cervenu farbu.
    is_win = (pnl or 0) >= 0
    label = _PROFIT_LABEL if is_win else _LOSS_LABEL
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "-"
    ticker = _short_ticker(symbol)
    # Label, ticker, suma (PnL) - rovnake poradie/dovod ako notify_trade_opened.
    headline = f"{label} {ticker} {pnl_str}"
    reason_label = _CLOSE_REASON_LABELS.get(closed_trade.get("close_reason"), closed_trade.get("close_reason"))
    payload = {
        "content": f"{headline} {_EVERYONE_PING}",
        "embeds": [{
            "title": f"{headline} ({reason_label})",
            "color": _PNL_COLOR["win"] if is_win else _PNL_COLOR["loss"],
            "fields": [
                {"name": "PnL", "value": pnl_str if pnl is not None else "-", "inline": True},
                {"name": "Vstup", "value": str(closed_trade.get("entry_price")), "inline": True},
                {"name": "Vystup", "value": str(closed_trade.get("exit_price")), "inline": True},
                {"name": "Drzane", "value": f"{closed_trade.get('hours_held', 0):.1f}h", "inline": True},
            ],
        }]
    }
    return _post_webhook(payload, "Notifikacia o zatvoreni")


def notify_ready_for_production(asset_name: str, sl_pct: float, tp_pct: float,
                                 atr_pct: float, bars_used: int) -> None:
    """Zavola sa PRESNE RAZ na symbol (viz sl_calibration._maybe_auto_apply
    guard) - ked ticker, ktorý zatiaľ LEN zbieral cenovú históriu (žiadny
    externý fallback zdroj, viz assets.py MINIMAX/UNITREE), prvýkrát dosiahne
    dosť vlastných barov na spoľahlivú ATR-based SL/TP kalibráciu AJ
    plnohodnotnú TA (pre tieto konkrétne tickery je to ten istý okamih - viz
    modulový docstring sl_calibration.py). SL/TP sa AUTOMATICKY aplikuje ako
    RiskOverride (rovnaký mechanizmus ako ručné "Nastaviť ako default"), túto
    notifikáciu dostaneš, aby si vedel z telefónu bez počítača, že stačí
    zvážiť ENABLE_{TICKER}=true (a prípadne maržu) na Railway - SL/TP už nie
    je slepý odhad."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    headline = f"READY {asset_name}"
    payload = {
        "content": f"{headline} {_EVERYONE_PING}",
        "embeds": [{
            "title": f"{headline} - pripravený na produkciu (SL/TP auto-prekalibrované)",
            "color": 3066993,
            "fields": [
                {"name": "Nové SL", "value": f"{sl_pct:.2f}%", "inline": True},
                {"name": "Nové TP", "value": f"{tp_pct:.2f}%", "inline": True},
                {"name": "ATR14", "value": f"{atr_pct:.3f}%", "inline": True},
                {"name": "Barov použitých", "value": str(bars_used), "inline": True},
            ],
        }]
    }
    _post_webhook(payload, "Notifikacia o pripravenosti na produkciu")


# 2026-08-24 (MINIMAX nalez - burzova anomalia stracajuca TP/SL nohu sa
# opakovala ~kazdu minutu takmer 3.5h, kazda oprava USPESNE prebehla, ale
# kazda si aj tak vypytala vlastny @everyone ping - desiatky notifikacii pre
# TU ISTU vec). Per-symbol cooldown: prva oprava v okne sa nahlasi VZDY hned
# (ziadne oneskorenie prveho varovania), dalsie v ramci cooldownu sa LEN
# pocitaju (ziadny Discord post) - az ked cooldown vyprsi a prava dalsia
# oprava skutocne prijde, zahrnie sa do jej spravy aj "(+N potlacenych)".
# In-memory (rovnaky vzor ako heartbeat_check._last_alert_at) - resetuje sa
# pri restarte procesu, co je prijatelne (restart sam osebe je novy zaciatok).
_REPAIR_ALERT_COOLDOWN_MINUTES = 20
_last_repair_alert_at: dict[str, datetime] = {}
_suppressed_repair_count: dict[str, int] = {}


def notify_bracket_leg_restored(symbol: str, leg: str, price: float) -> None:
    """2026-08-21 (po ADA incidente, na ziadost pouzivatela) - zavola sa,
    ked position_monitor._check_and_reheal_bracket_legs zisti a znovu doplni
    CHYBAJUCU TP alebo SL nohu uz otvorenej pozicie (burza ju z nejakeho
    dovodu "stratila" - viz strike_client.get_open_orders docstring). Toto je
    vzdy anomalia (za normalnych okolnosti sa toto nikdy nemalo stat) - preto
    VZDY s @everyone, na rozdiel od bezneho notify_trade_opened/closed.

    Cooldown (viz komentar vyssie) je LEN o frekvencii Discord notifikacii -
    samotna oprava v position_monitor.py prebehne VZDY, bez ohladu na toto."""
    if not config.DISCORD_WEBHOOK_URL:
        return

    now = datetime.now(timezone.utc)
    last_alert = _last_repair_alert_at.get(symbol)
    if last_alert is not None and (now - last_alert).total_seconds() < _REPAIR_ALERT_COOLDOWN_MINUTES * 60:
        _suppressed_repair_count[symbol] = _suppressed_repair_count.get(symbol, 0) + 1
        print(f"[discord_client] REPAIR {symbol} {leg} - cooldown aktivny, "
              f"potlacam Discord alert (potlacenych spolu: {_suppressed_repair_count[symbol]}).")
        return

    suppressed = _suppressed_repair_count.pop(symbol, 0)
    _last_repair_alert_at[symbol] = now

    headline = f"REPAIR {_short_ticker(symbol)}"
    suppressed_note = (
        f"\n\n_(+{suppressed} ďalších opakovaných opráv za posledných "
        f"{_REPAIR_ALERT_COOLDOWN_MINUTES} min potlačených, aby toto nezaplavilo kanál)_"
        if suppressed else ""
    )
    payload = {
        "content": f"{headline} {_EVERYONE_PING}",
        "embeds": [{
            "title": f"{headline} - chýbajúca {leg} noha automaticky obnovená",
            "description": (
                f"Burza stratila {leg} objednávku otvorenej pozície bez akéhokoľvek "
                "nášho zásahu (anomália na strane burzy) - bot ju práve teraz znovu "
                "nastavil na pôvodnú hodnotu. Over si prosím na Strike, že je to "
                f"v poriadku.{suppressed_note}"
            ),
            "color": 15105570,  # oranzova - anomalia, nie bezna udalost
            "fields": [{"name": f"Obnovená {leg}", "value": str(price), "inline": True}],
        }]
    }
    _post_webhook(payload, "Notifikacia o obnovenej bracket nohe")


def notify_heartbeat_stale(stale_minutes: float) -> None:
    """2026-08-21 (viz heartbeat_check.py pre plne obmedzenia/kontext) - "bot
    nemusi zit" hlasenie. Rovnako ako notify_bracket_leg_restored VZDY s
    @everyone (anomalia)."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    headline = "HEARTBEAT stale"
    payload = {
        "content": f"{headline} {_EVERYONE_PING}",
        "embeds": [{
            "title": f"{headline} - bot mozno nezije alebo je zaseknuty",
            "description": (
                f"Ucet sa neaktualizoval {stale_minutes:.0f} minut (normalne kazdu minutu). "
                "Skontroluj Railway - ak proces spadol, skus Restart/Redeploy, prip. rollback na "
                "staršiu verziu. Existujuce SL/TP na burze zostavaju zive nezavisle od tohto bota."
            ),
            "color": 15158332,  # cervena
        }]
    }
    _post_webhook(payload, "Notifikacia o zastaranom heartbeate")
