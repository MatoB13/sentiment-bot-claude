"""Minutove vzorky mark ceny v pamati (spolocne pre extreme_alarm a tp_runner).

Samostatny modul bez dalsich importov z bota - tp_runner ho potrebuje, ale
extreme_alarm importuje trade_cycle (a ten tp_runner), takze buffer v
extreme_alarm by vytvoril kruhovy import.

Plnia ho extreme_alarm (vsetky aktivne tickery kazdu minutu) aj position_monitor
(tickery s otvorenou poziciou) - dvojity zapis v tej istej minute sa zahodi, aby
okno 4 h pre alarm nezmenslo na polovicu. Po restarte je buffer prazdny;
volajuci si musi poradit bez neho (None)."""
from collections import deque
from datetime import datetime, timedelta

BUFFER_MINUTES = 300
_MIN_GAP_SECONDS = 30
_buffer: dict[str, deque] = {}


def record_price(symbol: str, now: datetime, price: float) -> None:
    buf = _buffer.setdefault(symbol, deque(maxlen=BUFFER_MINUTES + 30))
    if buf and (now - buf[-1][0]).total_seconds() < _MIN_GAP_SECONDS:
        return
    buf.append((now, price))


def price_at(symbol: str, target: datetime, tolerance_min: float = 5) -> float | None:
    """Posledna minutova vzorka nie neskorsia nez `target` a nie starsia nez tolerancia."""
    best = None
    for t, p in _buffer.get(symbol, ()):
        if t <= target:
            best = (t, p)
        else:
            break
    if best and (target - best[0]) <= timedelta(minutes=tolerance_min):
        return best[1]
    return None
