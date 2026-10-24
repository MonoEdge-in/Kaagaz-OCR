"""File storage over the S3 API: SeaweedFS on the laptop, Amazon S3 in AWS.

It implements ``BlobStorage`` (upload), ``Quarantine`` (the scan gate) and
the page image calls the pipeline and the API use.

Keys are made from ids only, never from file names. Every key starts with
the customer, so one customer's calls can only ever build keys under that
customer's own prefix:

* ``customers/{customer_id}/documents/{document_id}/original``
* ``customers/{customer_id}/documents/{document_id}/pages/{page_no}.webp``
* ``quarantine/{customer_id}/{document_id}``: outside every customer prefix,
  so ``get`` cannot reach a quarantined file.

Ids must be UUIDs. They are parsed and written back in the standard form, so
an id like ``../other`` can never move a key to another customer's prefix.

Errors: "not there" (NoSuchKey, 404) is ``BlobNotFound``. Anything else from
storage (cannot connect, timed out, a missing bucket, access refused, a 5xx)
is ``BackendUnavailable``, so the API answers 503 and the worker retries. A
missing bucket is never reported as a missing file: that would make the scan
gate fail a document for good because storage was misconfigured.

PROVISIONAL: the key layout is the storage naming rule, #11, which is
Vrushit's. Whether a customer's delete also removes the quarantined copy is
his call too; here it does, so a delete leaves nothing of the customer's
file behind.
"""

import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound
from kaagaz.settings import Settings

# SeaweedFS needs the bucket in the path (host/bucket/key), not in the host
# name, and signature version 4. Amazon S3 accepts both.
# Storage that does not answer fails fast: 5 s to connect, 30 s per read, and at
# most two retries (botocore's max_attempts counts retries: three tries in all),
# about 105 s per call at worst instead of botocore's default 5 x (60 + 60) s.
# A delete forever and the purge call storage inside their database
# transaction, so this also bounds how long they hold its locks.
_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 2},
)

# Cannot reach storage, or it stopped answering part way.
_UNREACHABLE = (BotoConnectionError, HTTPClientError)
# Every storage failure the callers are told about as BackendUnavailable.
_ANY_ERROR = (ClientError, *_UNREACHABLE)

# The error codes that mean "no such object". A HEAD request has no body, so
# its code is the bare status "404". NoSuchBucket is deliberately not here.
_MISSING_CODES = frozenset({"NoSuchKey", "NotFound", "404"})

# DeleteObjects takes at most 1000 keys per call (the S3 limit).
_DELETE_BATCH = 1000


def _canonical_id(value: str) -> str:
    """The id in standard UUID form. Raises ValueError for anything else."""
    return str(uuid.UUID(value))


def _document_prefix(customer_id: str, document_id: str) -> str:
    return f"customers/{_canonical_id(customer_id)}/documents/{_canonical_id(document_id)}/"


def resumable_key(customer_id: str, user_id: str, session_id: str) -> str:
    return (
        f"customers/{_canonical_id(customer_id)}/uploads/{_canonical_id(user_id)}/"
        f"{_canonical_id(session_id)}/content"
    )


def original_key(customer_id: str, document_id: str) -> str:
    return _document_prefix(customer_id, document_id) + "original"


def page_image_key(customer_id: str, document_id: str, page_no: int) -> str:
    if page_no < 1:
        raise ValueError("page numbers start at 1")
    return _document_prefix(customer_id, document_id) + f"pages/{page_no}.webp"


def quarantine_key(customer_id: str, document_id: str) -> str:
    return f"quarantine/{_canonical_id(customer_id)}/{_canonical_id(document_id)}"


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _is_missing(error: ClientError) -> bool:
    return _error_code(error) in _MISSING_CODES


def _make_client(settings: Settings) -> Any:
    # With no keys set (in AWS), boto3 finds the task role's credentials itself.
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        region_name=settings.storage_region,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        config=_CONFIG,
    )


class S3Storage:
    def __init__(self, settings: Settings, client: Any = None) -> None:
        # boto3 ships no type information, so the client is Any. Tests pass a
        # client wrapped in botocore.stub.Stubber.
        self._client = client if client is not None else _make_client(settings)
        self._bucket = settings.storage_bucket
        self._region = settings.storage_region

    # --- BlobStorage -------------------------------------------------------

    def put(self, customer_id: str, document_id: str, data: IO[bytes]) -> None:
        """Store the original file. ``data`` is read from where it stands."""
        key = original_key(customer_id, document_id)
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def get(self, customer_id: str, document_id: str) -> bytes:
        """The original file. BlobNotFound if this customer has no such file."""
        return self._get_bytes(original_key(customer_id, document_id), document_id)

    def delete(self, customer_id: str, document_id: str) -> None:
        """Remove the original, every page image and any quarantined copy.

        Time O(k) and memory O(k) for k stored objects of the document (the
        original plus at most ``max_pages`` page images).
        """
        keys = self._list_keys(_document_prefix(customer_id, document_id))
        keys.append(quarantine_key(customer_id, document_id))  # deleting a missing key is fine
        for start in range(0, len(keys), _DELETE_BATCH):
            self._delete_keys(keys[start : start + _DELETE_BATCH])

    def delete_customer(self, customer_id: str) -> None:
        """Remove everything stored for one customer, ``customers/{id}/`` and
        ``quarantine/{id}/`` (deleting an organisation, super admin PR 4): each listing
        page (at most 1000 keys) is deleted before the next is asked for, so memory is
        O(1000) and time O(k) for its k objects. Safe to repeat."""
        customer = _canonical_id(customer_id)
        for prefix in (f"customers/{customer}/", f"quarantine/{customer}/"):
            arguments: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            while True:
                response = self._list_page(arguments)
                keys = [str(item["Key"]) for item in response.get("Contents", [])]
                if keys:
                    self._delete_keys(keys)
                token = response.get("NextContinuationToken")
                if not response.get("IsTruncated") or not token:
                    break
                arguments["ContinuationToken"] = token

    def start_resumable(self, customer_id: str, user_id: str, session_id: str) -> tuple[str, str]:
        key = resumable_key(customer_id, user_id, session_id)
        try:
            result = self._client.create_multipart_upload(Bucket=self._bucket, Key=key)
            return key, str(result["UploadId"])
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def upload_resumable_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> None:
        try:
            self._client.upload_part(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=body,
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def resumable_parts(self, key: str, upload_id: str) -> list[tuple[int, int, str]]:
        result: list[tuple[int, int, str]] = []
        marker = 0
        try:
            while True:
                page = self._client.list_parts(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumberMarker=marker,
                )
                result.extend(
                    (int(part["PartNumber"]), int(part["Size"]), str(part["ETag"]))
                    for part in page.get("Parts", [])
                )
                if not page.get("IsTruncated"):
                    return result
                marker = int(page.get("NextPartNumberMarker", 0))
                if marker <= 0:
                    raise BackendUnavailable("storage")
        except BackendUnavailable:
            raise
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def complete_resumable(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [{"PartNumber": number, "ETag": etag} for number, etag in parts]},
            )
        except _ANY_ERROR as error:
            if isinstance(error, ClientError) and _error_code(error) == "NoSuchUpload":
                try:
                    self._client.head_object(Bucket=self._bucket, Key=key)
                    return  # The complete reply was lost; this session already produced its temp object.
                except _ANY_ERROR as head_error:
                    raise BackendUnavailable("storage") from head_error
            raise BackendUnavailable("storage") from error

    def resumable_complete(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as error:
            if _is_missing(error):
                return False
            raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error

    @contextmanager
    def open_resumable(self, key: str) -> Iterator[IO[bytes]]:
        """The finished upload, spooled locally; closed when the caller's ``with`` ends."""
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
            try:
                body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
                try:
                    while chunk := body.read(64 * 1024):
                        spool.write(chunk)
                finally:
                    body.close()
                spool.seek(0)
            except _ANY_ERROR as error:
                raise BackendUnavailable("storage") from error
            yield spool

    def delete_resumable(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def abort_resumable(self, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
        except ClientError as error:
            if _error_code(error) != "NoSuchUpload":
                raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error

    # --- Quarantine --------------------------------------------------------

    def quarantine(self, customer_id: str, document_id: str) -> None:
        """Copy the original under ``quarantine/``, then delete the original.

        Safe to run twice: if the original is already gone, there is nothing
        left for ``get`` to reach, so it returns quietly.
        """
        source = original_key(customer_id, document_id)
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=quarantine_key(customer_id, document_id),
                CopySource={"Bucket": self._bucket, "Key": source},
            )
        except ClientError as error:
            if _is_missing(error):
                return
            raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error
        try:
            self._client.delete_object(Bucket=self._bucket, Key=source)
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    # --- Page images -------------------------------------------------------

    def put_page_image(self, customer_id: str, document_id: str, page_no: int, image: bytes) -> None:
        """Store a page's WebP picture (the pipeline encodes it)."""
        key = page_image_key(customer_id, document_id, page_no)
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=image, ContentType="image/webp")
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes:
        """The page's WebP picture. BlobNotFound if this customer has no such page image."""
        return self._get_bytes(page_image_key(customer_id, document_id, page_no), document_id)

    # --- Health and set-up -------------------------------------------------

    def ping(self) -> None:
        """Raise BackendUnavailable unless the bucket answers."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def ensure_bucket(self) -> None:
        """Create the bucket if it is missing (the laptop's first start).

        In AWS the bucket is made by the infrastructure code, so this only
        finds it there. Any answer other than "not found" stops start-up.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            if not _is_missing(error):
                raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error
        arguments: dict[str, Any] = {"Bucket": self._bucket}
        if self._region != "us-east-1":  # S3 refuses a location constraint for us-east-1
            arguments["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        try:
            self._client.create_bucket(**arguments)
        except ClientError as error:
            # Another process made it between the check and the create: fine.
            if _error_code(error) not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error

    # --- Helpers -----------------------------------------------------------

    def _get_bytes(self, key: str, document_id: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return bytes(response["Body"].read())
        except ClientError as error:
            if _is_missing(error):
                raise BlobNotFound(document_id) from None
            raise BackendUnavailable("storage") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("storage") from error

    def _list_page(self, arguments: dict[str, Any]) -> Any:
        try:
            return self._client.list_objects_v2(**arguments)
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error

    def _delete_keys(self, keys: list[str]) -> None:
        """At most ``_DELETE_BATCH`` keys in one call; any key not deleted is an error."""
        try:
            response = self._client.delete_objects(
                Bucket=self._bucket, Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True}
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("storage") from error
        if response.get("Errors"):
            raise BackendUnavailable("storage")

    def _list_keys(self, prefix: str) -> list[str]:
        """Every key under ``prefix``, page by page (1000 keys per page)."""
        keys: list[str] = []
        arguments: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
        while True:
            response = self._list_page(arguments)
            keys.extend(str(item["Key"]) for item in response.get("Contents", []))
            token = response.get("NextContinuationToken")
            if not response.get("IsTruncated") or not token:
                return keys
            arguments["ContinuationToken"] = token
