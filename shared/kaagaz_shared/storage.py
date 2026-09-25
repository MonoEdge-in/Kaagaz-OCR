"""The one place that talks to file storage: SeaweedFS on the laptop, Amazon S3 in AWS.

Both speak the same S3 language, so only the settings change between them, never this
code (section 5 of docs/prd/day-1-prd.html, "Storage helper").

The browser never sends a file through the API. The API hands it a signed link, a URL
that allows one upload for a short time, and the browser sends the file straight to
storage with it.

boto3 blocks while it waits for storage. From async code, call the methods that talk to
storage through `fastapi.concurrency.run_in_threadpool`. Making a signed link is only
local maths with the keys, so it's safe to call directly.
"""

from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from kaagaz_shared.settings import Settings

# How long a signed link works. The PRD fixes 15 minutes for uploads.
UPLOAD_LINK_SECONDS = 15 * 60
DOWNLOAD_LINK_SECONDS = 15 * 60

# SeaweedFS needs the bucket in the path (host/bucket/key) rather than in the host name
# (bucket.host/key), and signature version 4. Amazon S3 accepts both too.
_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})


def incoming_key(upload_id: UUID, filename: str) -> str:
    """Where a new upload is stored: `incoming/{upload_id}/{filename}`.

    The filename comes from the user, so it's checked here: a "/" or ".." could place
    the file outside its own upload's folder, and control characters break headers
    and logs.
    """
    if filename in ("", ".", ".."):
        raise ValueError("The file name is empty or only dots.")
    if "/" in filename or "\\" in filename:
        raise ValueError("The file name contains a slash.")
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise ValueError("The file name contains a control character.")
    return f"incoming/{upload_id}/{filename}"


def _is_missing(error: ClientError) -> bool:
    # A HEAD request has no body, so the only sign of "not there" is the 404 status.
    status: int = error.response["ResponseMetadata"]["HTTPStatusCode"]
    return status == 404


class Storage:
    """Signed links, object info, copy, and put and get of bytes, all in one bucket."""

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        # One session holds the keys; each client made from it has its own address.
        session = boto3.session.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name=settings.s3_region,
        )
        # Every call the API or worker makes goes through this one...
        self.server_client = session.client(
            "s3", endpoint_url=settings.s3_endpoint_url, config=_CONFIG
        )
        # ...and this one only signs links, with the address the browser can reach.
        # A link signed for one address doesn't work on another.
        self.public_client = session.client(
            "s3", endpoint_url=settings.s3_public_endpoint_url, config=_CONFIG
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it's missing. Run once when the app starts."""
        try:
            self.server_client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as error:
            # Anything but "not found", such as "forbidden", must stop start-up.
            if not _is_missing(error):
                raise
        try:
            self.server_client.create_bucket(Bucket=self.bucket)
        except ClientError as error:
            # Another process created it between our check and our create: that's fine.
            if error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise

    def upload_link(self, key: str, content_type: str) -> str:
        """A signed link the browser can PUT one file to, for 15 minutes.

        The content type is part of the signature, so the browser must send exactly
        this `Content-Type` header or storage refuses the upload.
        """
        url: str = self.public_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_LINK_SECONDS,
            HttpMethod="PUT",
        )
        return url

    def download_link(self, key: str) -> str:
        """A signed link the browser can download one file from, for 15 minutes."""
        url: str = self.public_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=DOWNLOAD_LINK_SECONDS,
        )
        return url

    def object_size(self, key: str) -> int | None:
        """The object's size in bytes, or None if there's no object at that key."""
        try:
            response = self.server_client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_missing(error):
                return None
            raise
        size: int = response["ContentLength"]
        return size

    def copy(self, source_key: str, target_key: str) -> None:
        """Copy an object inside the bucket. Storage does the copy; no bytes pass here."""
        self.server_client.copy_object(
            Bucket=self.bucket,
            Key=target_key,
            CopySource={"Bucket": self.bucket, "Key": source_key},
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Store bytes at a key, replacing anything already there."""
        self.server_client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get_bytes(self, key: str) -> bytes:
        """Read a whole object into memory. Uploads are capped at MAX_UPLOAD_MB."""
        response = self.server_client.get_object(Bucket=self.bucket, Key=key)
        with response["Body"] as body:
            data: bytes = body.read()
        return data
