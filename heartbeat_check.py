"""2026-08-21 (na ziadost pouzivatela, pred cestou bez pocitaca) - "je bot
nazivo?" hlasenie do Discordu.

DOLEZITE OBMEDZENIE (vedome, nie da sa obist bez externeho watchdogu): tento
kod bezi V RAMCI TOHO ISTEHO worker procesu ako vsetko ostatne (viz main.py
scheduler). Ak by CELY proces spadol (napr. neocakavana vynimka pri importe
po buducom deployi, OOM), aj TENTO check prestane bezat - mrtvy proces sa
nevie sam nahlasit. Zachyti teda "proces zije, ale nieco vnutri prestalo
tikat" (zaseknuty job, dlhy vypadok Strike API a pod.), NIE uplny pad procesu.
Pre TEN pripad zostava jedina obrana dashboard "Aktualizovane" timestamp
(kontrolovat rucne) + Railway redeploy/rollback - viz diskusia s pouzivatelom.

Signal: AccountSnapshot.updated_at (price_poller.py aktualizuje kazdu minutu,
KAZDY beh, nezavisle od toho, ci sa nieco otvorilo/zatvorilo - najspolahlivejsi
"nieco tika" proxy, aky v DB mame). Ak je starsi nez HEARTBEAT_STALE_THRESHOLD_MINUTES,
Discord alert - s cooldownom (in-memory, resetuje sa pri restarte procesu -
prijatelne, kedze restart procesu sam osebe vyriesi staleness), aby dlhotrvajuci
vypadok nespamoval kanal pri kazdom tiku tohto checku."""
from datetime import datetime, timezone

import config
import discord_client
from db import AccountSnapshot, get_session

_last_alert_at: datetime | None = None
_ALERT_COOLDOWN_MINUTES = 60


def check_heartbeat() -> None:
    if not config.HEARTBEAT_CHECK_ENABLED:
        return

    global _last_alert_at
    session = get_session()
    try:
        snapshot = session.query(AccountSnapshot).filter(AccountSnapshot.id == 1).first()
    finally:
        session.close()

    if snapshot is None or snapshot.updated_at is None:
        return  # este nikdy neprebehol prvy poll - nema zmysel hodnotit "stale"

    updated_at = snapshot.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    stale_minutes = (now - updated_at).total_seconds() / 60

    if stale_minutes < config.HEARTBEAT_STALE_THRESHOLD_MINUTES:
        return

    if _last_alert_at is not None:
        since_last_alert = (now - _last_alert_at).total_seconds() / 60
        if since_last_alert < _ALERT_COOLDOWN_MINUTES:
            return

    print(f"[heartbeat_check] POZOR: posledna aktualizacia uctu je {stale_minutes:.0f} min "
          f"stara (prah {config.HEARTBEAT_STALE_THRESHOLD_MINUTES} min) - posielam Discord alert.")
    discord_client.notify_heartbeat_stale(stale_minutes)
    _last_alert_at = now
