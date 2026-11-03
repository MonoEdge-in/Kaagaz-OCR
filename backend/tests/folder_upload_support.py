"""Folder upload rows for tests (#484): written straight into the tables, as the plan leaves them."""

import hashlib
import uuid
from collections.abc import Sequence

from sqlalchemy import text

from kaagaz.db.engine import Database

_UPLOAD = (
    "INSERT INTO folder_uploads (id, customer_id, user_id, target, parent_folder_id, root_folder_id,"
    " source_name, root_name, merge, file_count, total_bytes, skipped_count)"
    " VALUES (:u, :c, :usr, 'folder', NULL, :root, 'A', 'A', true, :files, :bytes, 0)"
)
_FOLDERS = (
    "INSERT INTO folder_upload_folders (customer_id, folder_upload_id, idx, folder_id)"
    " SELECT :c, :u, i - 1, f FROM unnest(CAST(:f AS uuid[])) WITH ORDINALITY AS w (f, i)"
)


def path_key(path: str) -> str:
    """The browser's item key: SHA-256 hex of the relative path."""
    return hashlib.sha256(path.encode()).hexdigest()


def new_folder_upload(
    db: Database,
    customer_id: str,
    user_id: str,
    *,
    folder_ids: Sequence[str],
    file_count: int = 10,
    total_bytes: int = 10_000,
) -> str:
    """A planned folder upload of ``folder_ids`` (the first is its root). Returns its id."""
    upload_id = str(uuid.uuid4())
    params = {
        "u": upload_id,
        "c": customer_id,
        "usr": user_id,
        "root": folder_ids[0],
        "files": file_count,
        "bytes": total_bytes,
        "f": list(folder_ids),
    }
    with db.customer(customer_id) as conn:
        conn.execute(text(_UPLOAD), params)
        conn.execute(text(_FOLDERS), params)
    return upload_id
