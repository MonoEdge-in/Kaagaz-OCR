"""The model catalogue refresh against PostgreSQL (model choice, council 11 Oct 2026, MC1;
0023): the app role writes the refresh columns, a refresh keeps an operator's enable and a
verify-models pass, and a model OpenRouter no longer lists stays, ineligible. The
filter rules themselves: ``test_model_catalog.py``."""

import copy
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

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
    return catalog.refresh(db, OpenRouterClient(settings), settings, today=TODAY)


def test_a_refresh_saves_the_candidates_and_a_model_no_longer_listed_stays_ineligible(
    db: Database, fake: OpenRouterFake
) -> None:
    done = _refresh(db, fake)
    assert (done.saved, done.eligible) == (5, 2)
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


def test_a_refresh_that_cannot_read_a_list_changes_nothing(db: Database, fake: OpenRouterFake) -> None:
    _refresh(db, fake)
    before = _owner(db, "SELECT id, eligible, seen_at FROM model_catalog ORDER BY id", {})
    fake.responder = lambda request: Reply(503, {}) if request.action == "zdr" else listed(request)
    with pytest.raises(catalog.CatalogUnavailable):
        _refresh(db, fake)
    assert _owner(db, "SELECT id, eligible, seen_at FROM model_catalog ORDER BY id", {}) == before
