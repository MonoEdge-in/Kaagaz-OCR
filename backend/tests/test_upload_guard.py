"""kaagaz/api/upload_guard.py on its own: the checks made before an upload's body is
read. Requests are built from bare ASGI scopes, so no server, body or database is needed.
"""

import pytest
from starlette.requests import Request

from kaagaz.api.errors import ApiError
from kaagaz.api.upload_guard import MULTIPART_OVERHEAD, check_upload_request
from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy, limits_by_type

MB = 1024 * 1024
POLICY = UploadPolicy(
    max_bytes=5 * MB,
    allowed_types=frozenset({sniff.PDF, sniff.PNG, sniff.TEXT}),
    type_limits=limits_by_type(pdf=5 * MB, image=2 * MB, sheet=1 * MB),
)
CEILING = 5 * MB + MULTIPART_OVERHEAD  # the largest limit plus the multipart wrapping


def _request(headers: dict[str, str]) -> Request:
    raw = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/api/documents", "headers": raw})


def _refusal(headers: dict[str, str]) -> tuple[int, str, str]:
    with pytest.raises(ApiError) as caught:
        check_upload_request(_request(headers), POLICY)
    return caught.value.status, caught.value.code, caught.value.message


# "²" is a digit to str.isdigit, but not an ASCII one.
@pytest.mark.parametrize("length", [None, "", "12a", "-1", "+5", " 5", "1e3", "5.0", "²"])
def test_an_upload_without_a_plain_size_is_400(length: str | None) -> None:
    headers = {} if length is None else {"content-length": length}
    assert _refusal(headers) == (400, "bad_request", "The upload has no size. Please try again.")


def test_a_transfer_encoding_is_400_even_next_to_a_size() -> None:
    headers = {"transfer-encoding": "chunked", "content-length": "10"}
    assert _refusal(headers) == (400, "bad_request", "The upload must say its size. Please try again.")


def test_a_size_up_to_the_largest_limit_and_the_wrapping_passes() -> None:
    check_upload_request(_request({"content-length": str(CEILING)}), POLICY)


def test_one_byte_more_is_413_naming_the_largest_limit() -> None:
    assert _refusal({"content-length": str(CEILING + 1)}) == (
        413,
        "too_large",
        "Files can be up to 5 MB. This one is larger. Split it or save it smaller and try again.",
    )
