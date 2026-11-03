"""The nightly backup's Python side (#476) and the disk alert's email (#480), without a network.

Storage runs against a stubbed boto3 client (botocore.stub.Stubber), as
tests/test_adapter_storage.py. The backup bucket's upload gets a small fake, because boto3's
managed transfer runs on its own threads. infra/ec2/pgdump.sh and infra/ec2/restore-drill.sh
chain these commands with pg_dump and openssl: tests/test_ec2_scripts.py reads them.
"""

import io
import tarfile
from datetime import UTC, datetime
from typing import IO, Any

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from kaagaz.adapters.email import MemoryMailer, SentEmail
from kaagaz.adapters.storage import S3Storage
from kaagaz.cli import BACKUP_REGION, main
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.settings import Settings
from tests.settings_factory import make_settings
from tests.test_settings import shipped

BUCKET = "kaagaz-test"
KEYS = ["customers/a/documents/b/original", "customers/a/documents/b/pages/1.webp", "quarantine/a/c"]
WHEN = datetime(2026, 11, 3, 1, 30, tzinfo=UTC)


def stubbed() -> tuple[S3Storage, Stubber]:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://localhost:8333",
        config=Config(signature_version=UNSIGNED),
    )
    stub = Stubber(client)
    stub.activate()
    return S3Storage(make_settings(storage_bucket=BUCKET), client=client), stub


def listing(keys: list[str], token: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"Contents": [{"Key": key, "LastModified": WHEN} for key in keys]}
    page["IsTruncated"] = token is not None
    if token is not None:
        page["NextContinuationToken"] = token
    return page


def stored(data: bytes) -> dict[str, Any]:
    return {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentLength": len(data)}


def read(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    data = archive.extractfile(member)
    assert data is not None
    return data.read()


def tar_of(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


# --- storage: every file into one tar, and back ------------------------------------------


def test_write_tar_streams_every_object_of_every_page_under_its_key() -> None:
    storage, stub = stubbed()
    stub.add_response("list_objects_v2", listing(KEYS[:2], token="t1"), {"Bucket": BUCKET})
    for key in KEYS[:2]:
        stub.add_response("get_object", stored(key.encode()), {"Bucket": BUCKET, "Key": key})
    stub.add_response("list_objects_v2", listing(KEYS[2:]), {"Bucket": BUCKET, "ContinuationToken": "t1"})
    stub.add_response("get_object", stored(b"held"), {"Bucket": BUCKET, "Key": KEYS[2]})
    out = io.BytesIO()
    assert storage.write_tar(out) == 3
    stub.assert_no_pending_responses()
    with tarfile.open(fileobj=io.BytesIO(out.getvalue())) as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == KEYS  # long keys too (PAX names)
        assert [member.mtime for member in members] == [int(WHEN.timestamp())] * 3
        assert {member.mode for member in members} == {0o600}
        contents = [read(archive, member) for member in members]
    assert contents == [KEYS[0].encode(), KEYS[1].encode(), b"held"]


def test_write_tar_skips_an_object_deleted_after_the_listing() -> None:
    storage, stub = stubbed()
    stub.add_response("list_objects_v2", listing(KEYS[:2]), {"Bucket": BUCKET})
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    stub.add_response("get_object", stored(b"page"), {"Bucket": BUCKET, "Key": KEYS[1]})
    out = io.BytesIO()
    assert storage.write_tar(out) == 1
    with tarfile.open(fileobj=io.BytesIO(out.getvalue())) as archive:
        assert archive.getnames() == [KEYS[1]]


def test_write_tar_stops_on_any_other_storage_error() -> None:
    storage, stub = stubbed()
    stub.add_response("list_objects_v2", listing(KEYS[:1]), {"Bucket": BUCKET})
    stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
    with pytest.raises(BackendUnavailable):
        storage.write_tar(io.BytesIO())


def test_read_tar_puts_each_file_back_under_its_key() -> None:
    storage, stub = stubbed()
    stub.add_response("list_objects_v2", {"IsTruncated": False}, {"Bucket": BUCKET, "MaxKeys": 1})
    for key in KEYS:
        stub.add_response("put_object", {}, {"Bucket": BUCKET, "Key": key, "Body": key.encode()})
    assert storage.read_tar(io.BytesIO(tar_of({key: key.encode() for key in KEYS}))) == 3
    stub.assert_no_pending_responses()


def test_read_tar_refuses_a_bucket_that_holds_anything() -> None:
    """A restore goes into an empty bucket only, so it never writes over live files."""
    storage, stub = stubbed()
    stub.add_response("list_objects_v2", listing(KEYS[:1]), {"Bucket": BUCKET, "MaxKeys": 1})
    with pytest.raises(ValueError, match="not empty"):
        storage.read_tar(io.BytesIO(tar_of({KEYS[0]: b"x"})))
    stub.assert_no_pending_responses()  # and no put_object was asked for


# --- the commands ------------------------------------------------------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure_bucket(self) -> None:
        self.calls.append("ensure_bucket")

    def write_tar(self, out: IO[bytes]) -> int:
        out.write(b"tar bytes")
        return 7

    def read_tar(self, source: IO[bytes]) -> int:
        self.calls.append(source.read().decode())
        return 2


def test_backup_files_writes_the_tar_to_stdout_and_the_count_elsewhere(
    capsys: pytest.CaptureFixture[str],
) -> None:
    binary = io.BytesIO()
    text = io.StringIO()
    code = main(
        ["backup-files"], settings=make_settings(), file_store=FakeStorage(), out=text, binary_out=binary
    )
    assert (code, binary.getvalue(), text.getvalue()) == (0, b"tar bytes", "")
    assert capsys.readouterr().err == "backup-files: 7 files\n"


def test_restore_files_makes_the_bucket_then_reads_stdin(capsys: pytest.CaptureFixture[str]) -> None:
    storage = FakeStorage()
    code = main(
        ["restore-files"],
        settings=make_settings(),
        file_store=storage,
        out=io.StringIO(),
        binary_in=io.BytesIO(b"tar"),
    )
    assert (code, storage.calls) == (0, ["ensure_bucket", "tar"])
    assert capsys.readouterr().err == "restore-files: 2 files\n"


class RefusingStorage(FakeStorage):
    def read_tar(self, source: IO[bytes]) -> int:
        raise ValueError("the storage bucket is not empty")


def test_restore_files_into_a_bucket_in_use_stops_with_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["restore-files"],
        settings=make_settings(),
        file_store=RefusingStorage(),
        out=io.StringIO(),
        binary_in=io.BytesIO(),
    )
    assert code == 2
    assert "not empty" in capsys.readouterr().err


class FakeBackupBucket:
    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, str, str, Any]] = []

    def upload_fileobj(self, fileobj: IO[bytes], bucket: str, key: str, Config: Any = None) -> None:
        self.uploads.append((fileobj.read(), bucket, key, Config))


def test_backup_upload_streams_stdin_to_the_key_in_parts() -> None:
    bucket = FakeBackupBucket()
    out = io.StringIO()
    code = main(
        ["backup-upload", "--bucket", "kaagaz-backups", "--key", "20261103/kaagaz.dump.cms"],
        environ={},  # no settings and no database: it runs beside the app, on the host network
        out=out,
        binary_in=io.BytesIO(b"encrypted"),
        backup_bucket=bucket,
    )
    assert code == 0
    [(data, name, key, config)] = bucket.uploads
    assert (data, name, key) == (b"encrypted", "kaagaz-backups", "20261103/kaagaz.dump.cms")
    # 32 MB parts, 4 at a time: up to 320 GB in S3's 10,000 parts, about 128 MB of memory.
    assert (config.multipart_chunksize, config.max_concurrency) == (32 * 1024 * 1024, 4)
    assert out.getvalue() == "uploaded 20261103/kaagaz.dump.cms\n"


def test_the_backup_bucket_client_is_in_mumbai_with_the_machines_own_role() -> None:
    from kaagaz.cli import _backup_bucket_client

    client = _backup_bucket_client()  # builds the real client; nothing is sent
    assert client.meta.region_name == BACKUP_REGION == "ap-south-1"


# --- the disk alert ----------------------------------------------------------------------


def alerting(**changes: Any) -> Settings:
    return make_settings(
        **{"ops_alert_email": "ops@example.com", "email_from": "no-reply@example.com", **changes}
    )


def test_disk_alert_emails_the_ops_address_once() -> None:
    mailer = MemoryMailer()
    out = io.StringIO()
    assert main(["disk-alert", "--used", "85"], settings=alerting(), mailer=mailer, out=out) == 0
    assert mailer.sent == [
        SentEmail(
            "ops@example.com",
            "Kaagaz server: the disk is 85% full",
            "The Kaagaz server's disk is 85% full. This alert is sent at most once a day.\n\n"
            "Free some space before it fills, or the database and the uploads stop. "
            "`docker system df` on the server shows what uses it.\n",
        )
    ]
    assert out.getvalue() == "disk alert sent\n"  # the address is never printed


@pytest.mark.parametrize(
    ("changes", "named"),
    [({"ops_alert_email": None}, "OPS_ALERT_EMAIL"), ({"email_from": None}, "EMAIL_FROM")],
)
def test_disk_alert_needs_the_ops_address_and_a_sender(
    changes: dict[str, Any], named: str, capsys: pytest.CaptureFixture[str]
) -> None:
    mailer = MemoryMailer()
    assert (
        main(["disk-alert", "--used", "85"], settings=alerting(**changes), mailer=mailer, out=io.StringIO())
        == 2
    )
    assert named in capsys.readouterr().err and mailer.sent == []


def test_a_disk_alert_that_fails_says_so_and_is_tried_again_next_hour(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#478 review: the disk alert and the reading pause's share one sender (kaagaz.ops_alert);
    a refused email is a failed command, so disk-alert.sh notes no day and tries next hour."""

    class Refused(MemoryMailer):
        def send(self, *, to: str, subject: str, text: str) -> None:
            raise ClientError({"Error": {"Code": "Throttling", "Message": "x"}}, "SendEmail")

    out = io.StringIO()
    assert main(["disk-alert", "--used", "85"], settings=alerting(), mailer=Refused(), out=out) == 2
    assert out.getvalue() == "" and "not sent" in capsys.readouterr().err


def test_ops_alert_email_is_read_and_optional() -> None:
    assert Settings.from_env(shipped()).ops_alert_email is None  # .env.example leaves it empty
    assert Settings.from_env(shipped(OPS_ALERT_EMAIL="ops@example.com")).ops_alert_email == "ops@example.com"
