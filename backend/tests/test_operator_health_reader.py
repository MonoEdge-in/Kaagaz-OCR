"""The operator's Health reply gains the reader and the queues (PRD 5 U9), without a
database: each worker's report holds exactly its four keys, the queues the two lane
depths, and a queue that does not answer is null, never its error. The SQL itself:
``test_isolation_reader_status.py``."""

from datetime import timedelta
from typing import cast

from fastapi import FastAPI

from kaagaz.ingestion.ports import Job
from tests import test_operator_activity as activity_tests
from tests.test_api_support import NOW, ApiFakeQueue
from tests.test_operator_activity import HEALTH, _Offline
from tests.test_operator_orgs import CID

offline = activity_tests.offline  # the fixture, by its name
READER_KEYS = {"worker", "state", "since", "seen_at"}


def _queue(offline: _Offline) -> ApiFakeQueue:
    return cast(ApiFakeQueue, cast(FastAPI, offline.client.app).state.kaagaz.queue)


def test_health_shows_each_workers_reader_state_and_the_queue_depths(offline: _Offline) -> None:
    offline.readers = [
        {"worker": "worker-1", "state": "working", "since": None, "seen_at": NOW},
        {
            "worker": "worker-2",
            "state": "key_problem",
            "since": NOW - timedelta(minutes=3),
            "seen_at": NOW - timedelta(seconds=30),
        },
    ]
    _queue(offline).enqueue(Job(CID, "11111111-1111-4111-8111-111111111111"))

    body = offline.get(HEALTH).json()

    assert [set(report) for report in body["reader"]] == [READER_KEYS] * 2
    assert body["reader"] == [
        {"worker": "worker-1", "state": "working", "since": None, "seen_at": NOW.isoformat()},
        {
            "worker": "worker-2",
            "state": "key_problem",
            "since": (NOW - timedelta(minutes=3)).isoformat(),
            "seen_at": (NOW - timedelta(seconds=30)).isoformat(),
        },
    ]
    assert body["queues"] == {"express": 0, "main": 1}


def test_a_queue_that_does_not_answer_is_null_and_no_error_shows(offline: _Offline) -> None:
    _queue(offline).down = True
    reply = offline.get(HEALTH)
    assert reply.status_code == 200
    assert (reply.json()["queues"], reply.json()["reader"]) == (None, [])
    assert "queue" not in reply.text.replace('"queues"', "")
