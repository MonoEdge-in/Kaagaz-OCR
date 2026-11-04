"""The model catalogue refresh against PostgreSQL (model choice, council 11 Oct 2026, MC1;
0023): the app role writes the refresh columns, a refresh keeps an operator's enable and a
verify-models pass, and a model OpenRouter no longer lists stays, ineligible. The
filter rules themselves: ``test_model_catalog.py``."""

import copy
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from kaagaz.ai.openrouter import OpenRouterClient
from kaagaz.db.engine import Database
from kaagaz.jobs import catalog
from tests.openrouter_fake import KEY, OpenRouterFake, Recorded, Reply, running_fake
from tests.settings_factory import make_settings
from tests.test_model_catalog import FIXTURE, PRO, SONNET, listed
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 11)
_ROW = "SELECT name, price_in_usd_per_m, eligible, verified_prompt_version FROM model_catalog WHERE id = :m"


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        server.responder = listed
        yield server


def _refresh(db: Database, fake: OpenRouterFake) -> catalog.Refreshed:
    settings = make_settings(openrouter_api_key=KEY, openrouter_base_url=fake.url)
    return catalog.refresh(db, OpenRouterClient(settings), settings, today=TODAY, pause=lambda seconds: None)


def test_a_refresh_saves_the_candidates_and_a_model_no_longer_listed_stays_ineligible(
    db: Database, fake: OpenRouterFake
) -> None:
    # QA T2: the shared PRO row as a first refresh finds it, whatever ran before (this test's
    # own verify-models pass below, or another test's).
    _owner(
        db,
        "UPDATE model_catalog SET verified_prompt_version = NULL, verified_at = NULL WHERE id = :m",
        {"m": PRO},
    )
    done = _refresh(db, fake)
    # Changed on purpose (features 25-27, 15 Oct 2026): answer-only candidates are saved too,
    # and the fixture has Gemini 3.8 Flash.
    assert (done.saved, done.eligible) == (8, 3)
    chat = "SELECT id FROM model_catalog WHERE chat_eligible ORDER BY id"
    assert [row[0] for row in _owner(db, chat, {})] == sorted([SONNET, PRO, "google/gemini-3.8-flash"])
    assert tuple(_owner(db, _ROW, {"m": PRO})[0]) == ("Google: Gemini 3.1 Pro Preview", 2, True, None)
    _owner(
        db,
        "UPDATE model_catalog SET verified_prompt_version = 'g9', verified_at = now() WHERE id = :m",
        {"m": PRO},
    )

    renamed: dict[str, Any] = copy.deepcopy(FIXTURE["models"])
    renamed["data"] = [item for item in renamed["data"] if item["id"] != SONNET]
    renamed["data"][0]["name"] = "Gemini 3.1 Pro, renamed"

    def without_sonnet(request: Recorded) -> Reply:
        return Reply(200, renamed) if request.action == "models" else listed(request)

    fake.responder = without_sonnet
    _refresh(db, fake)
    # The refresh wrote its own columns only: the verify-models pass stays.
    assert tuple(_owner(db, _ROW, {"m": PRO})[0]) == ("Gemini 3.1 Pro, renamed", 2, True, "g9")
    # Never deleted: no longer listed, so no longer eligible.
    assert tuple(_owner(db, _ROW, {"m": SONNET})[0])[2] is False


def test_a_refresh_saves_a_price_a_request_and_an_image_and_null_once_they_are_not_listed(
    db: Database, fake: OpenRouterFake
) -> None:
    """#488 review (R3): the refresh writes pricing.request and pricing.image as they are listed
    now: a price left out is not known (NULL), never an old one kept."""
    priced: dict[str, Any] = copy.deepcopy(FIXTURE["models"])
    priced["data"][0]["pricing"] |= {"request": "0", "image": "0.001238"}
    assert priced["data"][0]["id"] == PRO

    def with_prices(request: Recorded) -> Reply:
        return Reply(200, priced) if request.action == "models" else listed(request)

    query = "SELECT price_request_usd, price_image_usd FROM model_catalog WHERE id = :m"
    fake.responder = with_prices
    _refresh(db, fake)
    [(request, image)] = _owner(db, query, {"m": PRO})
    assert (request, float(image)) == (0, 0.001238)
    fake.responder = listed
    _refresh(db, fake)
    assert [tuple(row) for row in _owner(db, query, {"m": PRO})] == [(None, None)]


def test_a_refresh_that_cannot_read_a_list_changes_nothing(db: Database, fake: OpenRouterFake) -> None:
    _refresh(db, fake)
    before = _owner(db, "SELECT id, eligible, seen_at FROM model_catalog ORDER BY id", {})
    fake.responder = lambda request: Reply(503, {}) if request.action == "zdr" else listed(request)
    with pytest.raises(catalog.CatalogUnavailable):
        _refresh(db, fake)
    assert _owner(db, "SELECT id, eligible, seen_at FROM model_catalog ORDER BY id", {}) == before


def test_a_model_whose_endpoints_cannot_be_read_is_left_as_it_was(db: Database, fake: OpenRouterFake) -> None:
    """R7: one model's endpoint list failing skips it; its row keeps its eligible and seen_at."""
    _refresh(db, fake)
    before = _owner(db, "SELECT eligible, seen_at FROM model_catalog WHERE id = :m", {"m": SONNET})
    fake.responder = lambda request: (
        Reply(503, {}) if request.path.endswith(f"/{SONNET}/endpoints") else listed(request)
    )
    done = _refresh(db, fake)
    assert done.skipped == 1
    assert _owner(db, "SELECT eligible, seen_at FROM model_catalog WHERE id = :m", {"m": SONNET}) == before
    assert catalog.last_refreshed(db) is not None


@pytest.mark.parametrize("model_id", ["google/..", "./model", "a" * 129 + "/model"])
def test_0023_refuses_a_model_id_outside_the_pattern(db: Database, model_id: str) -> None:
    """R8: the CHECK holds the settings' MODEL_ID: each part 1 to 128 characters, starting
    with a letter or a digit."""
    with pytest.raises(IntegrityError):
        _owner(
            db,
            "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m)"
            " VALUES (:m, 'x', 1, 1)",
            {"m": model_id},
        )
