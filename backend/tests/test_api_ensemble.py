"""Ensemble after the scan through the API (kaagaz/api/routers/ensemble.py): the estimate, a
run started by a reviewer (202, queued with its run id; the same request again 200; another
409), Stop and Retry, the review status and flags showing the run, read again refused while
it runs, and the limits. Members (403), an organisation without the grant (403) and a
document still being read (409) start nothing. The job: test_ensemble_job_db.py; customer
separation: test_isolation_api_ensemble.py.
"""

from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.api.routers import ensemble as router
from kaagaz.api.routers.word_review import ENSEMBLE_OFF
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_model_choice import add_model, allow
from tests.test_api_support import Api, Signed
from tests.test_api_word_review import flags, post, scan

pytestmark = pytest.mark.db

GEMINI = make_settings(ocr_engine="gemini")


def granted(api: Api, customer_id: str) -> list[str]:
    """Three usable models (the first the default) and the Ensemble grant."""
    names = [add_model(api.db, name=name) for name in ("Lead", "Second", "Third")]
    allow(api.db, customer_id, names, default=names[0])
    with api.db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customer_ai_settings (customer_id, ensemble) VALUES (:c, true)"),
            {"c": customer_id},
        )
    return names


def ready(db: Database) -> tuple[Api, Signed, str]:
    """A reviewer of an organisation with the grant, and a ready scan with 3 flags on 2 pages."""
    # Changed on purpose (PRD 9 F-11, D5): an unpinned line alone is a check, no review item, so
    # Ensemble on flagged words leaves it out; page 2 is graded unreadable here, which keeps its
    # line a review item, so the run still reads two pages.
    api = Api(db, settings=GEMINI)
    signed = api.new_signed("reviewer")
    granted(api, signed.customer_id)
    return api, signed, scan(api, signed.customer_id, page_two="unreadable")


def start(signed: Signed, document_id: str, scope: str = "flagged", keys: list[str] | None = None) -> Any:
    return post(signed, f"/api/documents/{document_id}/ensemble", {"scope": scope, "word_keys": keys or []})


def test_ensemble_on_flagged_words_reads_the_review_items_only(db: Database) -> None:
    # #432: an unsure word (check band) is no review item, so the run counts as the list does.
    api = Api(db, settings=GEMINI)
    reviewer = api.new_signed("reviewer")
    granted(api, reviewer.customer_id)
    document_id = scan(api, reviewer.customer_id, quality="unsure")

    estimate = reviewer.client.get(f"/api/documents/{document_id}/ensemble/estimate?scope=flagged")

    # Changed on purpose (PRD 9 F-11, D5): so is page 2's unpinned line now; the [?] alone is sent.
    assert estimate.json() == {"pages": 1, "words": 1, "seconds": 1 * 2 * ensemble_sql.SECONDS_PER_READ}
    assert len(flags(reviewer, document_id)) == 1


def test_a_reviewer_starts_a_run_once_and_the_review_shows_it(db: Database) -> None:
    api, reviewer, document_id = ready(db)
    base = f"/api/documents/{document_id}"

    estimate = reviewer.client.get(f"{base}/ensemble/estimate?scope=flagged")
    assert estimate.json() == {"pages": 2, "words": 3, "seconds": 3 * 2 * ensemble_sql.SECONDS_PER_READ}
    made = start(reviewer, document_id)
    assert made.status_code == 202, made.text
    run = made.json()
    assert {
        key: run[key] for key in ("scope", "status", "pages_total", "pages_done", "words_total", "error")
    } == {
        "scope": "flagged",
        "status": "queued",
        "pages_total": 2,
        "pages_done": 0,
        "words_total": 3,
        "error": None,
    }
    assert [(job.document_id, job.run_id) for job in api.queue.jobs] == [(document_id, run["id"])]

    again = start(reviewer, document_id)
    assert (again.status_code, again.json()["id"]) == (200, run["id"])  # the same request: the same run
    other = start(reviewer, document_id, "document")
    assert (other.status_code, other.json()["error"]["code"]) == (409, "ensemble_running")
    assert len(api.queue.jobs) == 1

    status = reviewer.client.get(f"{base}/review-status").json()
    assert status["ensemble"]["run"]["id"] == run["id"]
    assert {item["state"] for item in flags(reviewer, document_id)} == {"in_ensemble"}
    assert reviewer.client.get(f"{base}/ensemble").json()["id"] == run["id"]

    read_again = post(reviewer, f"{base}/reprocess")
    assert (read_again.status_code, read_again.json()["error"]["code"]) == (409, "ensemble_running")

    stopped = post(reviewer, f"{base}/ensemble/{run['id']}/stop")
    assert (stopped.status_code, stopped.json()["status"]) == (200, "stopped")  # queued: at once
    retried = post(reviewer, f"{base}/ensemble/{run['id']}/retry")
    assert retried.status_code == 202, retried.text
    assert (retried.json()["pages_total"], api.queue.jobs[-1].run_id) == (2, retried.json()["id"])
    actions = [action for action in api.audit_actions(reviewer.customer_id) if action.startswith("ensemble.")]
    assert actions == ["ensemble.start", "ensemble.stop", "ensemble.retry"]


def test_members_an_organisation_without_the_grant_and_a_scan_still_being_read_start_nothing(
    db: Database,
) -> None:
    api, reviewer, document_id = ready(db)
    member = api.sign_in(reviewer.customer_id, "member")
    assert start(member, document_id).status_code == 403
    assert (
        member.client.get(f"/api/documents/{document_id}/ensemble/estimate?scope=flagged").status_code == 200
    )

    plain = Api(db, settings=GEMINI)
    other = plain.new_signed("reviewer")
    refused = start(other, scan(plain, other.customer_id))
    assert (refused.status_code, refused.json()["error"]) == (
        403,
        {"code": "ensemble_not_allowed", "message": ENSEMBLE_OFF},
    )

    with db.customer(reviewer.customer_id) as conn:
        conn.execute(text("UPDATE documents SET status = 'clean' WHERE id = :d"), {"d": document_id})
    scanning = start(reviewer, document_id)
    assert (scanning.status_code, scanning.json()["error"]["code"]) == (409, "not_ready")
    assert api.queue.jobs == []


def test_the_limits(db: Database) -> None:
    _api, reviewer, document_id = ready(db)
    bad = start(reviewer, document_id, "word")  # a word needs its key
    assert (bad.status_code, bad.json()["error"]["message"]) == (400, router.BAD_SCOPE)
    with db.customer(reviewer.customer_id) as conn:
        for _ in range(ensemble_sql.RUNS_PER_HOUR):
            conn.execute(
                text(
                    "INSERT INTO ensemble_runs (customer_id, document_id, scope, pages, models, status,"
                    " requested_by, finished_at) VALUES (:c, :d, 'document', '{1}', '{m}', 'done', :u, now())"
                ),
                {"c": reviewer.customer_id, "d": document_id, "u": reviewer.user_id},
            )
    limited = start(reviewer, document_id)
    assert (limited.status_code, limited.json()["error"]["code"]) == (429, "rate_limited")
