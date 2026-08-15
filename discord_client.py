"""Discord webhook notifikacie - zdarma, ziaden bot token netreba, len URL
webhooku vytvoreneho priamo v kanale (Channel Settings -> Integrations ->
Webhooks). Pouziva sa LEN na upozornenia (fire-and-forget) - zlyhanie NESMIE
nikdy ovplyvnit skutocne obchodovanie, preto kazda funkcia tu ticho zlyha
(vypise chybu do logu, nikdy nevyhodi vynimku volajucemu)."""
import requests

import config

_DIRECTION_COLOR = {"long": 3066993, "short": 15158332}  # Discord embed color (decimal), zelena/cervena


def notify_trade_opened(asset: dict, sized: dict) -> None:
    """Zavola sa HNED PO uspesnom otvoreni pozicie (viz trade_cycle.py) - len
    ak je DISCORD_WEBHOOK_URL nastavene, inak ticho no-op (rovnaky vzor ako
    ostatne volitelne doplnky - EIA/FRED/Marketaux)."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    direction = sized["direction"]
    emoji = "\U0001F7E2" if direction == "long" else "\U0001F534"
    payload = {
        "embeds": [{
            "title": f"{emoji} Otvorena pozicia: {asset['name']} {direction.upper()}",
            "color": _DIRECTION_COLOR.get(direction, 3447003),
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
    try:
        resp = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[discord_client] Notifikacia o otvoreni zlyhala (obchod uz je otvoreny, pokracujem): {e}")


_CLOSE_REASON_LABELS = {
    "take_profit": "Take-profit",
    "stop_loss": "Stop-loss",
    "liquidation": "Likvidacia",
    "force_closed_by_bot": "Timeout (max. doba drzania)",
}


def notify_trade_closed(symbol: str, closed_trade: dict) -> None:
    """Zavola sa po TP/SL/likvidacii/timeoute (viz position_monitor.
    _check_and_queue_close_notification) - ZAMERNE NIE pri manual_kill_switch
    (pouzivatel poziciu zatvoril sam, netreba mu to pripominat)."""
    if not config.DISCORD_WEBHOOK_URL:
        return
    pnl = closed_trade.get("pnl_usd")
    emoji = "\U0001F7E2" if (pnl or 0) >= 0 else "\U0001F534"
    reason_label = _CLOSE_REASON_LABELS.get(closed_trade.get("close_reason"), closed_trade.get("close_reason"))
    payload = {
        "embeds": [{
            "title": f"{emoji} Zatvorena pozicia: {symbol} ({reason_label})",
            "color": _DIRECTION_COLOR.get((closed_trade.get("direction") or "").lower(), 3447003),
            "fields": [
                {"name": "PnL", "value": f"${pnl:.2f}" if pnl is not None else "-", "inline": True},
                {"name": "Vstup", "value": str(closed_trade.get("entry_price")), "inline": True},
                {"name": "Vystup", "value": str(closed_trade.get("exit_price")), "inline": True},
                {"name": "Drzane", "value": f"{closed_trade.get('hours_held', 0):.1f}h", "inline": True},
            ],
        }]
    }
    try:
        resp = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[discord_client] Notifikacia o zatvoreni zlyhala: {e}")
