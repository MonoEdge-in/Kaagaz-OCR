"""The storage helper signs links for the browser's address and talks to storage by the server's.

No storage runs here. Signed links are checked by reading their URL, and every call to
storage is answered by botocore's Stubber, which also checks what was sent.
"""

from collections.abc import Iterator
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import SecretStr

from kaagaz_shared.settings import Settings
from kaagaz_shared.storage import Storage, incoming_key

UPLOAD_ID = UUID("3f2b8c1e-0000-4000-8000-000000000001")
KEY = f"incoming/{UPLOAD_ID}/invoice.pdf"


@pytest.fixture
def storage() -> Storage:
    # model_construct skips reading the environment: only the storage settings matter here.
    settings = Settings.model_construct(
        s3_endpoint_url="http://storage:8333",
        s3_public_endpoint_url="http://localhost:8333",
        s3_access_key="access",
        s3_secret_key=SecretStr("storage-secret"),
        s3_region="us-east-1",
        s3_bucket="bucket",
    )
    return Storage(settings)


@pytest.fixture
def stub(storage: Storage) -> Iterator[Stubber]:
    """Answers the server client's calls; the test fails if a planned call never came."""
    with Stubber(storage.server_client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


def test_incoming_key_follows_the_prd_layout() -> None:
    assert incoming_key(UPLOAD_ID, "Q3 report.xlsx") == f"incoming/{UPLOAD_ID}/Q3 report.xlsx"


@pytest.mark.parametrize("filename", ["", ".", "..", "a/b", "../x", "a\\b", "x\ny", "x\x00"])
def test_incoming_key_refuses_names_that_could_escape_or_break(filename: str) -> None:
    with pytest.raises(ValueError, match="file name"):
        incoming_key(UPLOAD_ID, filename)


def test_upload_link_is_for_the_browser_address_with_the_bucket_in_the_path(
    storage: Storage,
) -> None:
    url = urlsplit(storage.upload_link(KEY, "application/pdf"))
    assert url.netloc == "localhost:8333"
    assert url.path == f"/bucket/{KEY}"


def test_upload_link_is_signature_v4_for_15_minutes(storage: Storage) -> None:
    params = query(storage.upload_link(KEY, "application/pdf"))
    assert params["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
    assert params["X-Amz-Credential"].endswith("/us-east-1/s3/aws4_request")
    assert params["X-Amz-Expires"] == "900"


def test_upload_link_fixes_the_content_type(storage: Storage) -> None:
    # A signed header can't be changed by the browser without breaking the signature.
    params = query(storage.upload_link(KEY, "application/pdf"))
    assert "content-type" in params["X-Amz-SignedHeaders"].split(";")


def test_upload_link_asks_the_browser_for_no_checksum(storage: Storage) -> None:
    # A browser can't compute boto3's checksums, so a link that asks for one can't be used.
    params = query(storage.upload_link(KEY, "application/pdf"))
    assert not [name for name in params if "checksum" in name.lower()]


def test_download_link_is_for_the_browser_address(storage: Storage) -> None:
    link = storage.download_link(KEY)
    assert urlsplit(link).netloc == "localhost:8333"
    assert urlsplit(link).path == f"/bucket/{KEY}"
    assert query(link)["X-Amz-Expires"] == "900"


def test_server_calls_use_the_server_address(storage: Storage) -> None:
    assert storage.server_client.meta.endpoint_url == "http://storage:8333"


def test_object_size_reports_the_size(storage: Storage, stub: Stubber) -> None:
    stub.add_response("head_object", {"ContentLength": 5}, {"Bucket": "bucket", "Key": KEY})
    assert storage.object_size(KEY) == 5


def test_object_size_is_none_when_nothing_is_there(storage: Storage, stub: Stubber) -> None:
    stub.add_client_error("head_object", "404", http_status_code=404)
    assert storage.object_size(KEY) is None


def test_object_size_raises_any_other_error(storage: Storage, stub: Stubber) -> None:
    stub.add_client_error("head_object", "403", http_status_code=403)
    with pytest.raises(ClientError):
        storage.object_size(KEY)


def test_ensure_bucket_leaves_an_existing_bucket(storage: Storage, stub: Stubber) -> None:
    # No create_bucket answer is planned, so calling it would fail the test.
    stub.add_response("head_bucket", {}, {"Bucket": "bucket"})
    storage.ensure_bucket()


def test_ensure_bucket_creates_a_missing_bucket(storage: Storage, stub: Stubber) -> None:
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    stub.add_response("create_bucket", {}, {"Bucket": "bucket"})
    storage.ensure_bucket()


def test_ensure_bucket_accepts_losing_a_race_to_create_it(storage: Storage, stub: Stubber) -> None:
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    stub.add_client_error("create_bucket", "BucketAlreadyOwnedByYou", http_status_code=409)
    storage.ensure_bucket()


def test_ensure_bucket_stops_when_storage_refuses(storage: Storage, stub: Stubber) -> None:
    stub.add_client_error("head_bucket", "403", http_status_code=403)
    with pytest.raises(ClientError):
        storage.ensure_bucket()


def test_copy_stays_inside_the_bucket(storage: Storage, stub: Stubber) -> None:
    stub.add_response(
        "copy_object",
        {},
        {
            "Bucket": "bucket",
            "Key": "originals/abc.pdf",
            "CopySource": {"Bucket": "bucket", "Key": KEY},
        },
    )
    storage.copy(KEY, "originals/abc.pdf")


def test_put_bytes_sends_the_bytes_and_their_type(storage: Storage, stub: Stubber) -> None:
    stub.add_response(
        "put_object",
        {},
        {"Bucket": "bucket", "Key": KEY, "Body": b"%PDF", "ContentType": "application/pdf"},
    )
    storage.put_bytes(KEY, b"%PDF", "application/pdf")


def test_get_bytes_returns_the_whole_object(storage: Storage, stub: Stubber) -> None:
    body = StreamingBody(BytesIO(b"%PDF"), 4)
    stub.add_response("get_object", {"Body": body}, {"Bucket": "bucket", "Key": KEY})
    assert storage.get_bytes(KEY) == b"%PDF"
