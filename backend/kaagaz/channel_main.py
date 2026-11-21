"""The WhatsApp channel process (#603, PRD 7 section 15): ``python -m kaagaz.channel_main``, the
``whatsapp`` service (docker-compose.yml, ``--profile whatsapp``), from the API's image.

It refuses to start unless WHATSAPP_ENABLED is true. During Keycloak coexistence it also checks local
MFA enrollment on every message, so an old channel cannot preserve access for an unenrolled legacy
account. It is never the document worker, whose role
sees nothing through PRD 7's views: its Database takes ``db_app_role`` (the API's) and its login is
the API's (DATABASE_URL), so every person transaction is held to the views and the rule exactly as
a browser's (tests/test_whatsapp_guard.py). W4: the chat gets the API's own context on that one
Database (``api.app.headless_context``), so Ask and search are the browser's.

The loop (``run``): free the finished, put back in line the notices whose time came (W5,
``Channel.revive``), claim up to WHATSAPP_PEOPLE_AT_ONCE connections with
waiting messages (``kaagaz_whatsapp_claim``: one person at a time each, different people side by
side on a thread pool), and when nothing is waiting look again in POLL_SECONDS. The sweep
(``kaagaz_whatsapp_sweep``, also the worker's hourly) runs once at start and every SWEEP_SECONDS,
on its own: a sweep that fails is logged and tried again then, and never stops the claims. A lost
database pauses the loop; one person's error is logged by its type and place and leaves their lease
to run out.
SIGTERM finishes the people in hand, then stops.
"""

import logging
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from kaagaz.adapters.whatsapp import MetaWhatsApp
from kaagaz.api.app import headless_context
from kaagaz.channel.chat import Chat
from kaagaz.channel.loop import Channel
from kaagaz.db.engine import Database, make_engine
from kaagaz.db.whatsapp import Claim
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.settings import Settings
from kaagaz.worker_main import configure_logging, crash_site, install_stop_handlers

_log = logging.getLogger("kaagaz.channel")

POLL_SECONDS = 1.0
PAUSE_SECONDS = 5.0
SWEEP_SECONDS = 3600.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def handled(channel: Channel, claim: Claim) -> None:
    """``channel.handle``, with an error logged (its type and our code's place, never its message)
    instead of lost inside the thread pool."""
    try:
        channel.handle(claim)
    except Exception as error:  # noqa: BLE001 - one person's failure is logged, never the loop's end
        _log.error(
            "whatsapp messages not handled",
            extra={
                "customer_id": claim.linked.customer_id,
                "code": type(error).__name__,
                "where": crash_site(error),
            },
        )


def run(
    channel: Channel,
    stop: threading.Event,
    people_at_once: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Claim and handle until ``stop`` is set (see the module notes)."""
    running: set[Future[None]] = set()
    swept_at: float | None = None
    with ThreadPoolExecutor(max_workers=people_at_once, thread_name_prefix="whatsapp") as pool:
        while not stop.is_set():
            running = {future for future in running if not future.done()}
            if swept_at is None or monotonic() - swept_at >= SWEEP_SECONDS:
                swept_at = monotonic()
                try:
                    channel.sweep()
                except Exception as error:  # noqa: BLE001 - logged; the claims go on, the sweep runs again later
                    _log.error(
                        "whatsapp sweep failed",
                        extra={"code": type(error).__name__, "where": crash_site(error)},
                    )
            try:
                channel.revive()  # W5: notices put aside whose time came, back in line
                free = people_at_once - len(running)
                claimed = channel.claim(free) if free > 0 else []
            except BackendUnavailable:
                _log.warning("database unavailable, pausing", extra={"code": "database"})
                stop.wait(PAUSE_SECONDS)
                continue
            running |= {pool.submit(handled, channel, claim) for claim in claimed}
            if not claimed:
                stop.wait(POLL_SECONDS)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not settings.whatsapp_enabled or settings.whatsapp_number_key is None:
        _log.critical(
            "whatsapp channel cannot start: WHATSAPP_ENABLED is off", extra={"code": "whatsapp_off"}
        )
        sys.exit(1)
    # The API's timeouts: the channel answers people, as the API does.
    engine = make_engine(
        settings.database_url,
        statement_timeout_seconds=settings.db_statement_timeout_seconds,
        lock_timeout_seconds=settings.db_lock_timeout_seconds,
        idle_in_transaction_timeout_seconds=settings.db_idle_in_transaction_timeout_seconds,
    )
    database = Database(engine, settings.db_app_role)
    channel = Channel(
        db=database,
        sender=MetaWhatsApp.from_settings(settings),
        number_key=settings.whatsapp_number_key,
        clock=_utc_now,
        chat=Chat(headless_context(settings, database)),  # W4: Ask and search as the person
    )
    stop = threading.Event()
    install_stop_handlers(stop)
    _log.info("whatsapp channel started")
    try:
        run(channel, stop, settings.whatsapp_people_at_once)
    except Exception as error:  # noqa: BLE001 - the process boundary, as worker_main's
        _log.critical(
            "whatsapp channel crashed", extra={"code": type(error).__name__, "where": crash_site(error)}
        )
        sys.exit(1)
    finally:
        engine.dispose()
    _log.info("whatsapp channel stopped")


if __name__ == "__main__":
    main()
