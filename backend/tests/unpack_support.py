"""A zip uploaded and unpacked on PostgreSQL, for the unpacking tests (#522 PR 3): the upload
service and the unpacker over one database, in-memory storage and queue, and a scanner that
finds only the EICAR test string (the content check runs in its own tests)."""

import io
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from kaagaz.adapters.scanner import EICAR
from kaagaz.db import zip_unpacks
from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.db.upload_policy import customer_policy
from kaagaz.ingestion.ports import DocumentRecord, Job
from kaagaz.ingestion.service import UploadService
from kaagaz.jobs.unpack import ZipUnpacker
from kaagaz.scanning.gate import Verdict
from tests.fakes import MemoryQueue, MemoryStorage
from tests.settings_factory import make_settings


class EicarScanner:
    """INFECTED for a file holding the EICAR test string, else CLEAN; keeps what it saw."""

    def __init__(self) -> None:
        self.scanned: list[bytes] = []

    def scan(self, data: bytes) -> Verdict:
        self.scanned.append(data)
        return Verdict.INFECTED if EICAR in data else Verdict.CLEAN


@dataclass
class Unpacking:
    db: Database
    customer_id: str
    user_id: str
    storage: MemoryStorage = field(default_factory=MemoryStorage)
    queue: MemoryQueue = field(default_factory=MemoryQueue)
    scanner: EicarScanner = field(default_factory=EicarScanner)
    beats: int = 0

    def upload(self, data: bytes, name: str = "Archive.zip", folder_id: str | None = None) -> DocumentRecord:
        """The zip, uploaded as a person uploads it (its own job is not run here)."""
        with self.db.customer(self.customer_id) as conn:
            policy = customer_policy(conn, self.customer_id, make_settings())
        service = UploadService(policy, PgDocuments(self.db), self.storage, MemoryQueue())
        result = service.accept(
            self.customer_id,
            io.BytesIO(data),
            display_name=name,
            uploaded_by=self.user_id,
            folder_id=folder_id,
        )
        record = PgDocuments(self.db).get(self.customer_id, result.document_id)
        assert record is not None
        return record

    def unpacker(self) -> ZipUnpacker:
        return ZipUnpacker(self.db, self.storage, self.queue, self.scanner, make_settings())

    def unpack(self, record: DocumentRecord, data: bytes, *, customer_id: str | None = None) -> bool:
        job = Job(customer_id=customer_id or self.customer_id, document_id=record.document_id)
        return self.unpacker()(job, record, data, self._beat)

    def _beat(self) -> None:
        self.beats += 1

    def rows(self, sql: str, **params: Any) -> list[tuple[Any, ...]]:
        with self.db.customer(self.customer_id) as conn:
            return [tuple(row) for row in conn.execute(text(sql), {"c": self.customer_id, **params})]

    def execute(self, sql: str, **params: Any) -> None:
        with self.db.customer(self.customer_id) as conn:
            conn.execute(text(sql), {"c": self.customer_id, **params})

    def result(self, record: DocumentRecord) -> zip_unpacks.Unpack | None:
        with self.db.customer(self.customer_id) as conn:
            return zip_unpacks.unpacks(conn, self.customer_id, [record.document_id]).get(record.document_id)

    def tree(self, root: str) -> dict[str, list[str]]:
        """Each folder's path under ``root`` ("" is the root) and its documents' names, sorted."""
        folders = self.rows(
            "WITH RECURSIVE t (id, path) AS (SELECT id, '' FROM folders WHERE customer_id = :c AND id = :r"
            " UNION ALL SELECT f.id, t.path || '/' || f.name FROM folders f JOIN t ON f.parent_id = t.id"
            " WHERE f.customer_id = :c) SELECT id, path FROM t",
            r=root,
        )
        tree: dict[str, list[str]] = {}
        for folder, path in folders:
            names = self.rows(
                "SELECT display_name FROM documents WHERE customer_id = :c AND folder_id = :f", f=folder
            )
            tree[str(path)] = sorted(str(name) for (name,) in names)
        return tree

    def root_of(self, record: DocumentRecord) -> str:
        found = self.result(record)
        assert found is not None and found.root_folder_id is not None
        return found.root_folder_id
