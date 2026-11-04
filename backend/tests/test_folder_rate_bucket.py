"""A folder upload's file sends (#484) without a database: their own rate bucket, and the sends
at once of one organisation and of every organisation capped with a short Retry-After."""

import pytest

from kaagaz.api.errors import ApiError
from kaagaz.api.upload_guard import SendSlots
from tests.test_api_auth import _offline_client
from tests.test_api_support import CSRF

ITEM = "/api/documents?folder_upload_item_id=7a0f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"


def test_folder_file_sends_have_their_own_bucket_and_nothing_else_does() -> None:
    client = _offline_client(rate_limit_per_minute=1, folder_upload_calls_per_minute=3)
    assert client.get("/api/auth/me").status_code != 429
    assert client.get("/api/auth/me").status_code == 429  # the general bucket is used up
    for _ in range(3):
        assert client.post(ITEM, headers=CSRF).status_code != 429
    assert client.post(ITEM, headers=CSRF).status_code == 429  # its own bucket, used up too
    assert client.post("/api/documents", headers=CSRF).status_code == 429  # a plain upload: general
    assert client.post("/api/folder-uploads", json={}, headers=CSRF).status_code == 429  # planning: general


def test_send_slots_refuse_past_either_cap_and_free_on_the_way_out() -> None:
    slots = SendSlots()
    with slots.held("a", per_org=2, total=3), slots.held("a", per_org=2, total=3):
        with pytest.raises(ApiError) as refused, slots.held("a", per_org=2, total=3):
            pass
        assert (refused.value.status, refused.value.code, refused.value.headers) == (
            429,
            "rate_limited",
            {"Retry-After": "2"},
        )
        with (
            slots.held("b", per_org=2, total=3),
            pytest.raises(ApiError),
            slots.held("c", per_org=2, total=3),
        ):
            pass  # three at once in all
    with pytest.raises(RuntimeError), slots.held("a", per_org=1, total=1):
        raise RuntimeError("a send that failed")
    with slots.held("a", per_org=1, total=1):
        pass  # every slot came back
