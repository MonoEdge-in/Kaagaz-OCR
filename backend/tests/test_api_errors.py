"""The last resort for an error nothing else handled (PRD 1 B5): the usual 500 JSON,
and one log line with the error's type and the request id, never its message or
traceback, which can quote row values or document text. No database is needed.
"""

import json
import logging
import subprocess
import sys

import pytest
from sqlalchemy.exc import IntegrityError

from kaagaz.api.app import create_app
from kaagaz.db.engine import Database, make_engine
from tests.db_support import BACKEND
from tests.settings_factory import make_settings
from tests.test_api_support import NOW, ApiFakeQueue, ApiFakeStorage, FakeAnswers, TestClient

ROW_VALUE = "ABCDE1234F"  # made up; stands in for a value read from a document
REQUEST_ID = "req-0123456789"
INTERNAL = {"error": {"code": "internal_error", "message": "Something went wrong on our side."}}


def _client_raising(error: Exception) -> TestClient:
    """An app with one extra route that raises ``error``. The TestClient re-raises
    anything the app lets out, so an error raised again fails the test."""
    db = Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app")
    app = create_app(
        make_settings(),
        db=db,
        storage=ApiFakeStorage(),
        queue=ApiFakeQueue(),
        answers=FakeAnswers(),
        clock=lambda: NOW,
    )

    @app.get("/api/boom")
    def boom() -> None:
        raise error

    return TestClient(app)


def _error_lines(caplog: pytest.LogCaptureFixture) -> list[object]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "kaagaz.api.errors"]


def test_an_unhandled_error_is_a_500_and_logs_its_type_and_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = _client_raising(RuntimeError(f"could not read {ROW_VALUE}"))

    reply = client.get("/api/boom", headers={"X-Request-ID": REQUEST_ID})

    assert reply.status_code == 500
    assert reply.json() == INTERNAL
    assert reply.headers["X-Request-ID"] == REQUEST_ID
    assert _error_lines(caplog) == [{"event": "error", "request_id": REQUEST_ID, "error": "RuntimeError"}]
    assert all(record.exc_info is None for record in caplog.records)
    assert ROW_VALUE not in caplog.text
    assert "Traceback" not in caplog.text


def test_an_integrity_error_carrying_row_values_is_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    email = f"{ROW_VALUE.lower()}@example.test"
    error = IntegrityError(
        "INSERT INTO users (email) VALUES (%(email)s)",
        {"email": email},
        Exception(f"duplicate key value\nDETAIL:  Key (email)=({email}) already exists."),
    )
    assert email in str(error)  # the row value is in the error itself

    reply = _client_raising(error).get("/api/boom")

    assert reply.status_code == 500
    assert reply.json() == INTERNAL
    [line] = _error_lines(caplog)
    assert isinstance(line, dict)
    assert line["error"] == "IntegrityError"
    assert email not in caplog.text
    assert "Traceback" not in caplog.text


# A real uvicorn server on a free local port: uvicorn.Config sets up uvicorn's own
# logging, then configure_logging runs, as when uvicorn imports kaagaz.api.main. One
# request whose query string holds a row value, then an error logged the way uvicorn
# and our code log one. Run in a fresh process, because logging is set up once per
# process. http.client, because it writes no log lines of its own.
_LOGGING_SCRIPT = f"""
import http.client
import logging
import threading
import time

import uvicorn

from kaagaz.api.app import configure_logging, create_app
from kaagaz.db.engine import Database, make_engine
from tests.settings_factory import make_settings
from tests.test_api_support import NOW, ApiFakeQueue, ApiFakeStorage, FakeAnswers

app = create_app(
    make_settings(),
    db=Database(make_engine("postgresql://unused@localhost:1/unused"), "kaagaz_app"),
    storage=ApiFakeStorage(),
    queue=ApiFakeQueue(),
    answers=FakeAnswers(),
    clock=lambda: NOW,
)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off"))
configure_logging("DEBUG")
thread = threading.Thread(target=server.run)
thread.start()
deadline = time.monotonic() + 10
while not server.started and time.monotonic() < deadline:
    time.sleep(0.01)
port = server.servers[0].sockets[0].getsockname()[1]
connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
connection.request("GET", "/api/auth/me?q={ROW_VALUE}")
assert connection.getresponse().status == 401
connection.close()
server.should_exit = True
thread.join(10)

for name in ("uvicorn.error", "kaagaz.api", ""):
    try:
        raise ValueError("could not read {ROW_VALUE}")
    except ValueError:
        logging.getLogger(name).exception("Exception in ASGI application")
"""


def test_api_log_lines_carry_ids_and_error_types_only() -> None:
    ran = subprocess.run(  # noqa: S603 - fixed arguments: this interpreter and the script above
        [sys.executable, "-c", _LOGGING_SCRIPT], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    assert ran.returncode == 0, ran.stderr[-500:]

    lines = [json.loads(line) for line in ran.stderr.splitlines()]
    [access] = [json.loads(line["message"]) for line in lines if line["logger"] == "kaagaz.api.access"]
    assert (access["path"], access["status"]) == ("/api/auth/me", 401)  # our access line stays
    assert [(line["logger"], line["error"]) for line in lines if "error" in line] == [
        ("uvicorn.error", "ValueError"),
        ("kaagaz.api", "ValueError"),
        ("root", "ValueError"),
    ]
    assert ran.stdout == "", "uvicorn's own access line (on stdout) is switched off"
    assert ROW_VALUE not in ran.stderr
    assert "Traceback" not in ran.stderr
