"""One organisation's upload limits (PRD 1, U2), for the API's uploads and the worker's unpacking
of a zip (#522 PR 3), so both check one policy.

* Each type's own limit: MAX_UPLOAD_MB for PDFs (and Word and PowerPoint files), MAX_IMAGE_MB,
  MAX_SHEET_MB; a zip's is the organisation's folder upload byte limit
  (``folder_uploads.effective_limits``), or the PDF or sheet limit when that is larger, since an
  .xlsx, .docx or .pptx is sniffed as a zip too, and never more than ``ZIP_MAX_BYTES`` (500 MB: the
  scan gate holds the whole zip in memory).
* The customer's own limit (``customers.max_upload_mb``, an admin's) lowers every type's, never
  raises one.

``customer_policy`` takes a connection from ``Database.customer(customer_id)``: two statements,
O(1).
"""

from sqlalchemy import Connection

from kaagaz.db import admin as admin_sql
from kaagaz.db.folder_uploads import effective_limits
from kaagaz.ingestion.policy import ZIP_MAX_BYTES, UploadPolicy, limits_by_type
from kaagaz.settings import Settings

MIB = 1024 * 1024


def upload_policy(
    settings: Settings, customer_max_mb: int | None, *, folder_bytes: int | None = None
) -> UploadPolicy:
    """The limits for one customer's upload: each type's own limit, lowered by the customer's
    own limit when an admin set one. ``folder_bytes``: the organisation's folder upload byte
    limit, a zip's (None: a zip has the larger of the PDF and sheet limits, ``limits_by_type``)."""
    pdf, sheet = settings.max_upload_bytes, settings.max_sheet_bytes
    type_limits = limits_by_type(
        pdf=pdf,
        image=settings.max_image_bytes,
        sheet=sheet,
        zip=None if folder_bytes is None else min(ZIP_MAX_BYTES, max(folder_bytes, pdf, sheet)),
    )
    # The largest type's limit (MAX_UPLOAD_MB before #522), so only the customer's own lowers one.
    max_bytes = settings.max_upload_bytes if folder_bytes is None else max(type_limits.values())
    if customer_max_mb is not None:
        max_bytes = min(max_bytes, customer_max_mb * MIB)
    return UploadPolicy(max_bytes=max_bytes, allowed_types=settings.allowed_types, type_limits=type_limits)


def customer_policy(conn: Connection, customer_id: str, settings: Settings) -> UploadPolicy:
    """This customer's upload limits now, its zip's included."""
    row = admin_sql.get_settings(conn, customer_id)
    customer_max_mb = None if row is None or row["max_upload_mb"] is None else int(row["max_upload_mb"])
    folder_bytes = effective_limits(conn, customer_id).max_bytes
    return upload_policy(settings, customer_max_mb, folder_bytes=folder_bytes)
