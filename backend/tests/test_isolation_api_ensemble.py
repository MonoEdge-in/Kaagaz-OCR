"""Ensemble across customers: customer A cannot read, start, stop, retry, keep or revert
anything of customer B's (kaagaz/api/routers/ensemble.py, 0030, 0032).

A's reviewer, of an organisation with the grant, using B's document id gets 404 on every
Ensemble route, never 200, 202, 403 or 409; with A's own document, B's run and reading ids
are 404 too; nothing of B's changes and nothing is queued. The control: B's own reviewer
gets the real answer for the same ids, so A's 404 comes from the customer (session, routes,
SQL and row level security), never from a broken route or id.
"""

import json

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.test_api_ensemble import GEMINI, granted
from tests.test_api_support import Api
from tests.test_api_word_review import post, scan

pytestmark = pytest.mark.db

NOT_FOUND = {"code": "not_found", "message": "We couldn't find that."}
REGION = json.dumps({"kind": "box", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]})


def snapshot(api: Api, customer_id: str) -> tuple[object, ...]:
    with api.db.customer(customer_id) as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT (SELECT string_agg(status || coalesce(stop_requested_at::text, ''), ',')"
                    " FROM ensemble_runs WHERE customer_id = :c),"
                    " (SELECT string_agg(coalesce(decision, '-'), ',') FROM ensemble_readings"
                    " WHERE customer_id = :c),"
                    " (SELECT count(*) FROM word_changes WHERE customer_id = :c)"
                ),
                {"c": customer_id},
            ).one()
        )


@pytest.mark.parametrize("role", ["reviewer", "admin"])
def test_a_cannot_reach_bs_ensemble(db: Database, role: str) -> None:
    api = Api(db, settings=GEMINI)
    b_reviewer = api.new_signed("reviewer")
    granted(api, b_reviewer.customer_id)
    b_doc = scan(api, b_reviewer.customer_id)
    b_run = post(
        b_reviewer, f"/api/documents/{b_doc}/ensemble", {"scope": "flagged", "word_keys": []}
    ).json()["id"]
    with db.customer(b_reviewer.customer_id) as conn:
        b_reading = conn.execute(
            text(
                "INSERT INTO ensemble_readings (customer_id, document_id, run_id, word_key, page_no, region,"
                " before_text, outcome, proposal) VALUES (:c, :d, :r, :k, 1, CAST(:g AS jsonb), '4,850',"
                " 'changed', '4,350') RETURNING id"
            ),
            {"c": b_reviewer.customer_id, "d": b_doc, "r": b_run, "k": "a" * 64, "g": REGION},
        ).scalar_one()
    a_user = api.new_signed(role)
    granted(api, a_user.customer_id)
    a_doc = scan(api, a_user.customer_id)
    jobs = len(api.queue.jobs)
    before = snapshot(api, b_reviewer.customer_id)
    b_base, a_base = f"/api/documents/{b_doc}", f"/api/documents/{a_doc}"

    calls = [
        a_user.client.get(f"{b_base}/ensemble"),
        a_user.client.get(f"{b_base}/ensemble/estimate?scope=flagged"),
        post(a_user, f"{b_base}/ensemble", {"scope": "flagged", "word_keys": []}),
        post(a_user, f"{b_base}/ensemble/{b_run}/stop"),
        post(a_user, f"{b_base}/ensemble/{b_run}/retry"),
        post(a_user, f"{b_base}/ensemble/{b_run}/keep-all"),
        post(a_user, f"{b_base}/ensemble/readings/{b_reading}/keep"),
        post(a_user, f"{b_base}/ensemble/readings/{b_reading}/revert"),
        # A's own document with B's run and reading ids.
        post(a_user, f"{a_base}/ensemble/{b_run}/stop"),
        post(a_user, f"{a_base}/ensemble/{b_run}/retry"),
        post(a_user, f"{a_base}/ensemble/{b_run}/keep-all"),
        post(a_user, f"{a_base}/ensemble/readings/{b_reading}/keep"),
        post(a_user, f"{a_base}/ensemble/readings/{b_reading}/revert"),
        a_user.client.get(f"{a_base}/ensemble"),  # A has no run of its own
    ]

    assert [(reply.status_code, reply.json()["error"]) for reply in calls] == [(404, NOT_FOUND)] * len(calls)
    assert snapshot(api, b_reviewer.customer_id) == before
    assert len(api.queue.jobs) == jobs
    assert not [action for action in api.audit_actions(a_user.customer_id) if action.startswith("ensemble.")]

    own = b_reviewer.client.post(
        f"{b_base}/ensemble/readings/{b_reading}/revert", headers={"X-Requested-With": "kaagaz"}
    )
    assert own.status_code == 200, own.text
    assert b_reviewer.client.get(f"{b_base}/ensemble").json()["id"] == b_run
