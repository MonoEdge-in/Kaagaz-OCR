"""S3Storage against a stubbed boto3 client (botocore.stub.Stubber).

No network: Stubber checks each call's parameters against what the test
expects and hands back a canned answer. Connection failures, which Stubber
cannot produce, come from a tiny client object that raises them.
"""

import io
import unittest
from collections.abc import Callable
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from kaagaz.adapters.storage import S3Storage, original_key, page_image_key, quarantine_key
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from tests.settings_factory import make_settings

CUSTOMER = "11111111-1111-4111-8111-111111111111"
DOCUMENT = "22222222-2222-4222-8222-222222222222"
BUCKET = "kaagaz-test"
PREFIX = f"customers/{CUSTOMER}/documents/{DOCUMENT}/"


def make_client() -> Any:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://localhost:8333",
        # Stubber answers before a request is signed, so no keys are needed.
        config=Config(signature_version=UNSIGNED),
    )


def body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


class StubbedStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        client = make_client()
        self.stub = Stubber(client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.storage = S3Storage(make_settings(storage_bucket=BUCKET), client=client)

    def tearDown(self) -> None:
        self.stub.assert_no_pending_responses()

    # --- keys ---------------------------------------------------------------

    def test_keys_are_made_from_ids_only(self) -> None:
        self.assertEqual(original_key(CUSTOMER, DOCUMENT), PREFIX + "original")
        self.assertEqual(page_image_key(CUSTOMER, DOCUMENT, 3), PREFIX + "pages/3.webp")
        self.assertEqual(quarantine_key(CUSTOMER, DOCUMENT), f"quarantine/{CUSTOMER}/{DOCUMENT}")

    def test_ids_are_written_in_standard_form(self) -> None:
        self.assertEqual(original_key(CUSTOMER.upper(), DOCUMENT.upper()), PREFIX + "original")

    def test_anything_but_a_uuid_is_refused(self) -> None:
        for bad in ("../other", "", "cust-a", f"{DOCUMENT}/../x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                original_key(CUSTOMER, bad)

    def test_page_numbers_start_at_one(self) -> None:
        with self.assertRaises(ValueError):
            page_image_key(CUSTOMER, DOCUMENT, 0)

    # --- put, get -----------------------------------------------------------

    def test_put_stores_the_stream_under_the_original_key(self) -> None:
        data = io.BytesIO(b"%PDF-1.7 bytes")
        self.stub.add_response("put_object", {}, {"Bucket": BUCKET, "Key": PREFIX + "original", "Body": data})
        self.storage.put(CUSTOMER, DOCUMENT, data)

    def test_get_returns_the_bytes(self) -> None:
        self.stub.add_response(
            "get_object", {"Body": body(b"hello")}, {"Bucket": BUCKET, "Key": PREFIX + "original"}
        )
        self.assertEqual(self.storage.get(CUSTOMER, DOCUMENT), b"hello")

    def test_missing_object_is_blob_not_found(self) -> None:
        self.stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        with self.assertRaises(BlobNotFound):
            self.storage.get(CUSTOMER, DOCUMENT)

    def test_missing_bucket_is_not_a_missing_file(self) -> None:
        # Otherwise the scan gate would fail the document for good.
        self.stub.add_client_error("get_object", "NoSuchBucket", http_status_code=404)
        with self.assertRaises(BackendUnavailable):
            self.storage.get(CUSTOMER, DOCUMENT)

    def test_server_error_is_backend_unavailable(self) -> None:
        self.stub.add_client_error("put_object", "InternalError", http_status_code=500)
        with self.assertRaises(BackendUnavailable):
            self.storage.put(CUSTOMER, DOCUMENT, io.BytesIO(b"x"))

    def test_access_denied_is_backend_unavailable(self) -> None:
        self.stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
        with self.assertRaises(BackendUnavailable):
            self.storage.get(CUSTOMER, DOCUMENT)

    # --- page images --------------------------------------------------------

    def test_page_image_round_trip(self) -> None:
        webp = b"RIFF\x00\x00\x00\x00WEBPVP8 ..."
        key = PREFIX + "pages/2.webp"
        self.stub.add_response(
            "put_object", {}, {"Bucket": BUCKET, "Key": key, "Body": webp, "ContentType": "image/webp"}
        )
        self.stub.add_response("get_object", {"Body": body(webp)}, {"Bucket": BUCKET, "Key": key})
        self.storage.put_page_image(CUSTOMER, DOCUMENT, 2, webp)
        self.assertEqual(self.storage.get_page_image(CUSTOMER, DOCUMENT, 2), webp)

    def test_missing_page_image_is_blob_not_found(self) -> None:
        self.stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        with self.assertRaises(BlobNotFound):
            self.storage.get_page_image(CUSTOMER, DOCUMENT, 9)

    # --- delete -------------------------------------------------------------

    def test_delete_removes_original_pages_and_quarantine_copy(self) -> None:
        listed = [PREFIX + "original", PREFIX + "pages/1.webp", PREFIX + "pages/2.webp"]
        self.stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": key} for key in listed], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": PREFIX},
        )
        expected = [*listed, f"quarantine/{CUSTOMER}/{DOCUMENT}"]
        self.stub.add_response(
            "delete_objects",
            {},
            {"Bucket": BUCKET, "Delete": {"Objects": [{"Key": k} for k in expected], "Quiet": True}},
        )
        self.storage.delete(CUSTOMER, DOCUMENT)

    def test_delete_follows_every_listing_page(self) -> None:
        self.stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": PREFIX + "original"}], "IsTruncated": True, "NextContinuationToken": "t1"},
            {"Bucket": BUCKET, "Prefix": PREFIX},
        )
        self.stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": PREFIX + "pages/1.webp"}], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": PREFIX, "ContinuationToken": "t1"},
        )
        expected = [PREFIX + "original", PREFIX + "pages/1.webp", f"quarantine/{CUSTOMER}/{DOCUMENT}"]
        self.stub.add_response(
            "delete_objects",
            {},
            {"Bucket": BUCKET, "Delete": {"Objects": [{"Key": k} for k in expected], "Quiet": True}},
        )
        self.storage.delete(CUSTOMER, DOCUMENT)

    def test_delete_reports_objects_storage_could_not_remove(self) -> None:
        self.stub.add_response(
            "list_objects_v2", {"IsTruncated": False}, {"Bucket": BUCKET, "Prefix": PREFIX}
        )
        self.stub.add_response(
            "delete_objects",
            {"Errors": [{"Key": f"quarantine/{CUSTOMER}/{DOCUMENT}", "Code": "InternalError"}]},
            {"Bucket": BUCKET, "Delete": ANY},
        )
        with self.assertRaises(BackendUnavailable):
            self.storage.delete(CUSTOMER, DOCUMENT)

    # --- quarantine ---------------------------------------------------------

    def test_quarantine_copies_then_deletes_the_original(self) -> None:
        self.stub.add_response(
            "copy_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": f"quarantine/{CUSTOMER}/{DOCUMENT}",
                "CopySource": {"Bucket": BUCKET, "Key": PREFIX + "original"},
            },
        )
        self.stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": PREFIX + "original"})
        self.storage.quarantine(CUSTOMER, DOCUMENT)

    def test_quarantine_twice_is_quiet_when_the_original_is_gone(self) -> None:
        self.stub.add_client_error("copy_object", "NoSuchKey", http_status_code=404)
        self.storage.quarantine(CUSTOMER, DOCUMENT)  # no delete call is expected

    def test_quarantine_failure_to_copy_keeps_the_original(self) -> None:
        self.stub.add_client_error("copy_object", "InternalError", http_status_code=500)
        with self.assertRaises(BackendUnavailable):
            self.storage.quarantine(CUSTOMER, DOCUMENT)  # no delete call was made

    # --- ping, ensure_bucket ------------------------------------------------

    def test_ping(self) -> None:
        self.stub.add_response("head_bucket", {}, {"Bucket": BUCKET})
        self.storage.ping()
        self.stub.add_client_error("head_bucket", "404", http_status_code=404)
        with self.assertRaises(BackendUnavailable):
            self.storage.ping()

    def test_ensure_bucket_leaves_an_existing_bucket(self) -> None:
        self.stub.add_response("head_bucket", {}, {"Bucket": BUCKET})
        self.storage.ensure_bucket()

    def test_ensure_bucket_creates_a_missing_bucket(self) -> None:
        self.stub.add_client_error("head_bucket", "404", http_status_code=404)
        self.stub.add_response("create_bucket", {}, {"Bucket": BUCKET})
        self.storage.ensure_bucket()

    def test_ensure_bucket_outside_us_east_1_names_the_region(self) -> None:
        client = make_client()
        stub = Stubber(client)
        stub.add_client_error("head_bucket", "404", http_status_code=404)
        stub.add_response(
            "create_bucket",
            {},
            {"Bucket": BUCKET, "CreateBucketConfiguration": {"LocationConstraint": "ap-south-1"}},
        )
        storage = S3Storage(make_settings(storage_bucket=BUCKET, storage_region="ap-south-1"), client=client)
        with stub:
            storage.ensure_bucket()
        stub.assert_no_pending_responses()

    def test_ensure_bucket_tolerates_a_race(self) -> None:
        self.stub.add_client_error("head_bucket", "404", http_status_code=404)
        self.stub.add_client_error("create_bucket", "BucketAlreadyOwnedByYou", http_status_code=409)
        self.storage.ensure_bucket()

    def test_ensure_bucket_stops_on_access_denied(self) -> None:
        self.stub.add_client_error("head_bucket", "403", http_status_code=403)
        with self.assertRaises(BackendUnavailable):
            self.storage.ensure_bucket()


class _UnreachableClient:
    """Every call fails the way botocore does when storage cannot be reached."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __getattr__(self, name: str) -> Any:
        def call(**kwargs: Any) -> Any:
            raise self._error

        return call


def _every_call(storage: S3Storage) -> dict[str, Callable[[], object]]:
    return {
        "put": lambda: storage.put(CUSTOMER, DOCUMENT, io.BytesIO(b"x")),
        "get": lambda: storage.get(CUSTOMER, DOCUMENT),
        "delete": lambda: storage.delete(CUSTOMER, DOCUMENT),
        "quarantine": lambda: storage.quarantine(CUSTOMER, DOCUMENT),
        "put_page_image": lambda: storage.put_page_image(CUSTOMER, DOCUMENT, 1, b"p"),
        "get_page_image": lambda: storage.get_page_image(CUSTOMER, DOCUMENT, 1),
        "ping": storage.ping,
        "ensure_bucket": storage.ensure_bucket,
    }


class UnreachableStorageTest(unittest.TestCase):
    def test_every_call_maps_to_backend_unavailable(self) -> None:
        errors = [
            EndpointConnectionError(endpoint_url="http://localhost:8333"),
            ReadTimeoutError(endpoint_url="http://localhost:8333"),
        ]
        for error in errors:
            storage = S3Storage(make_settings(), client=_UnreachableClient(error))
            for name, call in _every_call(storage).items():
                with (
                    self.subTest(error=type(error).__name__, call=name),
                    self.assertRaises(BackendUnavailable),
                ):
                    call()


class ClientConfigTest(unittest.TestCase):
    """Storage that does not answer fails fast, so no caller hangs on it."""

    def test_the_client_has_short_timeouts_and_two_retries(self) -> None:
        config = S3Storage(make_settings())._client.meta.config  # builds the real client; nothing is sent
        self.assertEqual((config.connect_timeout, config.read_timeout), (5, 30))
        # max_attempts 2 is two retries after the first try.
        self.assertEqual(config.retries["total_max_attempts"], 3)
        # SeaweedFS still gets signature version 4 and the bucket in the path.
        self.assertEqual((config.signature_version, config.s3), ("s3v4", {"addressing_style": "path"}))


if __name__ == "__main__":
    unittest.main()
