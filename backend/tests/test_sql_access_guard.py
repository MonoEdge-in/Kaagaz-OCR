"""The SQL guard (PRD 7 section 10 and 11, "Guard tests"; #599): a statement the API reaches reads
``documents``, ``folders`` and ``trashed_folders`` only through their views, and a content table
(appendix A's 27) only through a document the person can see: the statement, or a fragment it
names, reads ``visible_documents`` (joined, ``visibility.SEEN_DOCUMENT``, or the outer row a
correlated fragment runs in). Anything else is on ALLOWED with one of the seven reasons and a note
naming why it may (for a statement shared with the worker, the route that checks the document
through the view first), or on PENDING_PR8: Ask and search, which PR 8 moves onto the view and
empties. The scanner is ``tests/sql_access_scan.py``; SQL inside migrations (the owner-rights
functions and the triggers) is reviewed with each migration and pinned by
test_isolation_schema.py, not here.

A text rule: a statement that joins the view for one table and reads another content table on its
own still passes. Review, and PR 9's row level security, are the backstop."""

import unittest

from tests import sql_access_scan as scan

# The seven reasons a statement the API reaches may read a table directly.
SHARED_WITH_WORKER = "shared with the worker, which must never read a view (#595 condition 1)"
WRITE_AFTER_CHECK = "a write, or the read or row lock of a write, after the route's check through the view"
TREE_WALK = "a walk of the folder tree by id; what is shown comes from a view"
# Changed on purpose (#601): permanent (decision 20, Admin screens show true names and numbers), and
# reached only from a route that asks for an admin session (test_admin_numbers_...).
ADMIN_NUMBERS = (
    "permanent: an Admin screen's true names and numbers, never content, admin session only (decision 20)"
)
DUPLICATE_LOOKUP = "must find a copy the person can't see; the reply goes through the view"
EXISTENCE_ONLY = "tells only whether a row exists, or names a document already checked"
CORRELATED = "a fragment, or a correlated subquery, run only inside a statement on a view's row"
REASONS = frozenset(
    {
        SHARED_WITH_WORKER,
        WRITE_AFTER_CHECK,
        TREE_WALK,
        ADMIN_NUMBERS,
        DUPLICATE_LOOKUP,
        EXISTENCE_ONLY,
        CORRELATED,
    }
)

_UPLOAD = "POST /documents and /documents/{id}/versions: the folder through folders_seen first"
_WORDS = "word review and Ensemble routes: get_document (the view) first, in the same transaction"
_ENSEMBLE = "ensemble routes: get_document (the view) first"

ALLOWED: dict[tuple[str, str], tuple[str, str]] = {
    # Changed on purpose (#601): the Access page's files given to a person, by their true names; the
    # rows about a file given to several people; a department's kind change, refused while in use.
    ("db/access.py", "_GIVEN_TO"): (ADMIN_NUMBERS, "GET /admin/users/{id}/access"),
    # Changed on purpose (#605, PR 13): the locked files a place gives, and a person may open, by name.
    ("db/lock_places.py", "_FILES"): (ADMIN_NUMBERS, "the give pop-up: its count of files"),
    ("db/lock_places.py", "_LOCKED"): (ADMIN_NUMBERS, "the give pop-up: GET …/locked-files"),
    ("db/lock_places.py", "_LOCKED_FOR"): (ADMIN_NUMBERS, "GET /admin/users/{id}/access"),
    # PR 10b (#601): the place of a file already found; the files a wall decides for, in its preview.
    ("db/access.py", "_DOCUMENT_PLACE"): (
        EXISTENCE_ONLY,
        "GET /documents/{id}/audience: the view (a Head) or kaagaz_admin_document (an Admin) first",
    ),
    ("db/access.py", "_DEPARTMENT_FILES"): (
        ADMIN_NUMBERS,
        "GET /admin/departments/{id}/department-only/preview",
    ),
    ("db/access.py", "_FOLDER_FILES"): (ADMIN_NUMBERS, "GET /folders/{id}/private/preview: an Admin's"),
    ("db/audit.py", "_INSERT_ABOUT"): (WRITE_AFTER_CHECK, "a file given: its folder and family, by id"),
    ("db/folders.py", "_IN_USE"): (
        WRITE_AFTER_CHECK,
        "PATCH /folders/{id}: _seen first, under the tree lock",
    ),
    ("db/admin.py", "_DOCUMENTS_BY_STATUS"): (ADMIN_NUMBERS, "GET /admin/stats"),
    ("db/admin.py", "_COUNTS"): (ADMIN_NUMBERS, "GET /admin/stats"),
    ("db/admin.py", "_KEPT"): (ADMIN_NUMBERS, "GET /admin/stats"),
    ("db/admin.py", "_MEMBERS"): (ADMIN_NUMBERS, "GET /admin/member-numbers"),
    ("db/admin.py", "_FAILED"): (ADMIN_NUMBERS, "GET /admin/failed: names, never content"),
    ("db/admin.py", "_FAILED_COUNT"): (ADMIN_NUMBERS, "GET /admin/failed"),
    ("db/answer_quality.py", "remove_learning"): (ADMIN_NUMBERS, "DELETE /admin/learned/{id}"),
    ("db/audit.py", "_INSERT"): (WRITE_AFTER_CHECK, "the audit row's folder and family, by id"),
    # Changed on purpose (#601): GET /access/activity (route 22) reads it with no _SEEN, and
    # views.timeline's unseen_masked blanks a file Management can't see (test_isolation_access_reads).
    ("db/audit.py", "_ENTRIES"): (
        EXISTENCE_ONLY,
        "a feed entry's document name; _SEEN drops a hidden one, or timeline's unseen_masked blanks it",
    ),
    ("db/audit.py", "_SEEN"): (EXISTENCE_ONLY, "whether the entry's document or folder still exists"),
    ("db/autofile.py", "_PRE_CHECK"): (SHARED_WITH_WORKER, f"auto-filing; {_UPLOAD}"),
    ("db/autofile.py", "_STATE"): (SHARED_WITH_WORKER, f"auto-filing; {_UPLOAD}"),
    ("db/autofile.py", "_BY_KEY"): (SHARED_WITH_WORKER, "auto-filing finds its folder"),
    ("db/autofile.py", "_BY_NAME"): (SHARED_WITH_WORKER, "auto-filing finds its folder"),
    ("db/autofile.py", "_DEPARTMENTS"): (SHARED_WITH_WORKER, "auto-filing finds its department"),
    ("db/autofile.py", "_SAME_NUMBER"): (SHARED_WITH_WORKER, "auto-filing finds its folder"),
    ("db/autofile.py", "_CHILD"): (SHARED_WITH_WORKER, "auto-filing finds its folder"),
    ("db/autofile.py", "_FILED_IN"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/autofile/undo: _document_or_404 first",
    ),
    ("db/autofile.py", "_DEPARTMENT_ABOVE"): (TREE_WALK, "POST /documents/{id}/autofile/undo"),
    ("db/autofile.py", "_INVOICE"): (SHARED_WITH_WORKER, "auto-filing reads the file's own invoice"),
    ("db/autofile.py", "_MOST_SEEN"): (SHARED_WITH_WORKER, "auto-filing reads the file's own invoice"),
    ("db/autofile.py", "_CHILD_NAMED"): (SHARED_WITH_WORKER, "auto-filing finds its folder"),
    ("db/document_stories.py", "_SET_EMBEDDING"): (
        WRITE_AFTER_CHECK,
        "a story's vector, after put_story (SEEN_DOCUMENT); shared with the CLI",
    ),
    ("db/document_stories.py", "_GET"): (
        SHARED_WITH_WORKER,
        "GET /documents/{id}/my-story: get_document first",
    ),
    ("db/document_stories.py", "_PUT"): (WRITE_AFTER_CHECK, "the family of :d, under SEEN_DOCUMENT"),
    ("db/documents.py", "delete_forever"): (
        WRITE_AFTER_CHECK,
        "delete forever: _document_or_404 or the trashed folder's one() first",
    ),
    ("db/documents.py", "_SELECT"): (DUPLICATE_LOOKUP, f"the upload's own row and its copy; {_UPLOAD}"),
    ("db/documents.py", "REFUSED_ALONE"): (DUPLICATE_LOOKUP, "a refused copy is replaced, not a duplicate"),
    ("db/documents.py", "_REFUSED_COPY"): (DUPLICATE_LOOKUP, "a refused copy is replaced, not a duplicate"),
    ("db/documents.py", "_READY_STATE"): (SHARED_WITH_WORKER, "the pipeline's store; the upload's own row"),
    ("db/documents.py", "_READ_BEFORE"): (SHARED_WITH_WORKER, "the pipeline's store; the upload's own row"),
    ("db/documents.py", "_REPROCESS_STATE"): (
        WRITE_AFTER_CHECK,
        "reprocess and read again: _document_or_404 first",
    ),
    ("db/documents.py", "_ENSEMBLE_RUNNING"): (
        SHARED_WITH_WORKER,
        "reprocess and read again: _document_or_404 first",
    ),
    ("db/documents.py", "_REQUEST_PAGE"): (
        WRITE_AFTER_CHECK,
        "POST .../pages/{n}/read-again: _readable_or_409 first",
    ),
    ("db/documents.py", "_ACCEPT_PAGE"): (
        WRITE_AFTER_CHECK,
        "POST .../pages/{n}/accept and word review: the view first",
    ),
    ("db/documents.py", "_PAGES_TO_READ_AGAIN"): (
        SHARED_WITH_WORKER,
        "the pipeline reads the pages to read again",
    ),
    ("db/documents.py", "_REGIONS_TO_READ_AGAIN"): (
        SHARED_WITH_WORKER,
        "the pipeline reads the pages to read again",
    ),
    ("db/documents.py", "_FAMILY_VERSIONS"): (
        WRITE_AFTER_CHECK,
        "delete forever: _document_or_404 / the trashed folder first",
    ),
    ("db/documents.py", "_NEXT_VERSION"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/versions: get_document first",
    ),
    ("db/documents.py", "_INSERT_VERSION"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/versions: get_document first",
    ),
    ("db/documents.py", "_COPY_TAGS"): (WRITE_AFTER_CHECK, "a new version takes its family's tags"),
    ("db/documents.py", "PgDocuments"): (SHARED_WITH_WORKER, f"the upload and pipeline store; {_UPLOAD}"),
    # PRD 7 (#605): the worker's locked path and its check before each model step (never the API's).
    ("db/documents.py", "_LOCKED_CLEAN"): (
        SHARED_WITH_WORKER,
        "the pipeline's store: a locked version prepared",
    ),
    ("db/documents.py", "_STILL_OPEN"): (SHARED_WITH_WORKER, "the pipeline's store: before each model step"),
    # PRD 7 (#605): whether a family is locked, under its family lock, after the route's view check.
    ("db/versions.py", "_LOCKED_FAMILY"): (
        EXISTENCE_ONLY,
        "join, undo, separate, make current: the view first (_visible / _LIVE), then refused if locked",
    ),
    # PRD 7 (#605): which documents an old answer cites are locked now, so they feed no prompt; ids only,
    # of answers the person's own conversation holds (PR 8 reads document_states through the view).
    ("db/chat.py", "locked_documents"): (EXISTENCE_ONLY, "the asker's own saved answers' document ids"),
    ("db/ensemble.py", "_STOP"): (WRITE_AFTER_CHECK, "POST .../ensemble/{run}/stop: get_document first"),
    ("db/ensemble.py", "_RUN"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_ACTIVE_RUN"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_RUNS_THIS_HOUR"): (
        SHARED_WITH_WORKER,
        "the organisation's hourly count; no row shown",
    ),
    ("db/ensemble.py", "_SCANNED"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_LINES"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/folder_upload_items.py", "_COLUMNS"): (
        DUPLICATE_LOOKUP,
        "the uploader's items; views.seen_items blanks a hidden copy",
    ),
    ("db/folder_upload_items.py", "_ALREADY_IN"): (
        DUPLICATE_LOOKUP,
        "Add new files only; views.seen_items blanks a hidden copy",
    ),
    ("db/folder_upload_items.py", "_FINISH"): (WRITE_AFTER_CHECK, "the item's own stored copy"),
    ("db/folder_upload_items.py", "_PROGRESS"): (EXISTENCE_ONLY, "counts of the uploader's own stored files"),
    ("db/folder_uploads.py", "_REUSE"): (
        SHARED_WITH_WORKER,
        "a folder upload's plan; its parent through folders_seen first",
    ),
    ("db/folder_uploads.py", "_SAME_NAME"): (
        SHARED_WITH_WORKER,
        "a folder upload's plan; its parent through folders_seen first",
    ),
    ("db/folder_uploads.py", "_INSERT_LEVEL"): (
        SHARED_WITH_WORKER,
        "a folder upload's plan; its parent through folders_seen first",
    ),
    ("db/folder_uploads.py", "_FIND_LEVEL"): (
        SHARED_WITH_WORKER,
        "a folder upload's plan; its parent through folders_seen first",
    ),
    ("db/folder_uploads.py", "_DEPTHS"): (
        SHARED_WITH_WORKER,
        "a folder upload's plan; its parent through folders_seen first",
    ),
    ("db/folders.py", "_PATH"): (
        TREE_WALK,
        "a folder's kind and depth after folders_seen (PATCH, DELETE /folders, the upload's department);"
        " the viewer shows a path only when folders_seen holds all of it; a restore names a place only"
        " when visible_folders holds all of it (trashed_folders.as_seen)",
    ),
    ("db/folders.py", "_DEPTH"): (TREE_WALK, "create and move: _seen first"),
    ("db/folders.py", "_DESCENDANTS"): (TREE_WALK, "a folder's subtree, after folders_seen on the folder"),
    ("db/folders.py", "_MOVE_CHECK"): (TREE_WALK, "PATCH and DELETE /folders: _seen first"),
    ("db/folders.py", "_DETACH_TRASHED"): (WRITE_AFTER_CHECK, "DELETE /folders/{id}: _seen first"),
    ("db/folders.py", "_PARENT"): (TREE_WALK, "PATCH and DELETE /folders: _seen first"),
    ("db/folders.py", "_SUMMARY"): (ADMIN_NUMBERS, "an Admin's delete dialog: true totals (PRD 7 section 5)"),
    # #594: the refusal before a keep-delete's writes; it compares the stored values of the folder and
    # of where its contents go, which a view would hide from an Admin (names, not contents).
    ("db/folders.py", "_MOVE_DOCUMENTS"): (
        WRITE_AFTER_CHECK,
        "DELETE /folders/{id}?contents=keep, an Admin's",
    ),
    ("db/folders.py", "_MOVE_FREE"): (WRITE_AFTER_CHECK, "DELETE /folders/{id}?contents=keep, an Admin's"),
    ("db/folders.py", "_LEFT"): (WRITE_AFTER_CHECK, "DELETE /folders/{id}?contents=keep, an Admin's"),
    ("db/folders.py", "_TRASH_TREE"): (WRITE_AFTER_CHECK, "DELETE /folders/{id}?contents=trash, an Admin's"),
    ("db/folders.py", "_DELETE"): (
        WRITE_AFTER_CHECK,
        "DELETE /folders/{id}: _seen first; RETURNING its parent",
    ),
    ("db/folders.py", "_MOVE"): (
        WRITE_AFTER_CHECK,
        "PATCH /folders/{id}: _seen first; the reply through shown_folder",
    ),
    ("db/folders.py", "_RENAME"): (
        WRITE_AFTER_CHECK,
        "PATCH /folders/{id}: _seen first; the reply through shown_folder",
    ),
    ("db/folders.py", "_SET_KIND"): (
        WRITE_AFTER_CHECK,
        "PATCH /folders/{id}: _seen first; the reply through shown_folder",
    ),
    ("db/folders.py", "_FREE"): (TREE_WALK, "a free sibling name, under the tree lock"),
    ("db/folders.py", "_PRUNE"): (
        WRITE_AFTER_CHECK,
        "an empty folder Kaagaz made goes; shared with the worker",
    ),
    ("db/listing.py", "_WITH_TAGS"): (CORRELATED, "an id filter inside _LIST (visible_documents d)"),
    ("db/listing.py", "_NEEDS_ATTENTION"): (CORRELATED, "inside _LIST, on visible_documents d"),
    ("db/listing.py", "_OUTSIDE_DEPARTMENTS"): (TREE_WALK, "the departments' subtrees from visible_folders"),
    ("db/listing.py", "_NAME_OF"): (WRITE_AFTER_CHECK, "rename: _document_or_404 first"),
    ("db/listing.py", "_MOVE_FAMILY"): (
        WRITE_AFTER_CHECK,
        "PATCH /documents/{id}: _document_or_404, then the folder through folders_seen, before the locks",
    ),
    ("db/listing.py", "_FAMILY_FOLDERS"): (WRITE_AFTER_CHECK, "PATCH /documents/{id}: the file first"),
    ("db/listing.py", "_VALUES_FOUND"): (CORRELATED, "inside _BATCH, on visible_documents d"),
    ("db/listing.py", "_FIRST_UNREADABLE"): (CORRELATED, "inside _BATCH, on visible_documents d"),
    ("db/listing.py", "_BATCH_SIZE"): (EXISTENCE_ONLY, "the uploader's batch count for its cap, never shown"),
    ("db/listing.py", "_PAGE"): (CORRELATED, "list_pages and get_page add SEEN_DOCUMENT"),
    ("db/listing.py", "_CLEAR_COPY"): (
        SHARED_WITH_WORKER,
        "clear copy and line edits: _readable_or_409 first",
    ),
    ("db/listing.py", "_DASHBOARD_DEPARTMENTS"): (
        TREE_WALK,
        "the departments from visible_folders, files from the view",
    ),
    ("db/listing.py", "_SET_DETAILS"): (
        WRITE_AFTER_CHECK,
        "PATCH /documents/{id}, tags, versions: the view first",
    ),
    ("db/listing.py", "_USAGE_ROWS"): (ADMIN_NUMBERS, "GET /admin/usage.csv: numbers per user and model"),
    ("db/models.py", "_AREA"): (SHARED_WITH_WORKER, "the organisation's model use; no document named"),
    ("db/models.py", "_DOCUMENT"): (
        SHARED_WITH_WORKER,
        "a document's scan choice; ensemble routes check it first",
    ),
    ("db/organization_words.py", "submit"): (
        EXISTENCE_ONLY,
        "a duplicate or the cap; the viewer's own document",
    ),
    ("db/reading_days.py", "_STATE"): (SHARED_WITH_WORKER, "the organisation's reading pause; no row shown"),
    ("db/review.py", "add_correction"): (
        SHARED_WITH_WORKER,
        "after lock_value (the view) or the word routes' check",
    ),
    ("db/runs.py", "_PAGES_BY_ROUTE"): (ADMIN_NUMBERS, "GET /admin/stats"),
    ("db/runs.py", "_READER_PAGES"): (ADMIN_NUMBERS, "GET /admin/stats"),
    ("db/runs.py", "_OPEN"): (SHARED_WITH_WORKER, "the pipeline's run"),
    ("db/storage_limits.py", "_ROOM"): (SHARED_WITH_WORKER, "the organisation's bytes; no row shown"),
    ("db/tags.py", "_REMOVE_OTHERS"): (WRITE_AFTER_CHECK, "PUT /documents/{id}/tags: _document_or_404 first"),
    ("db/tags.py", "_ADD"): (WRITE_AFTER_CHECK, "PUT /documents/{id}/tags: _document_or_404 first"),
    # PRD 7 PR 10c (#601): the move rules, the move previews, a folder holding what is hidden, and
    # the place of an upload with no folder.
    ("db/folders.py", "_SAME_PLACE"): (EXISTENCE_ONLY, "a move's rule: _seen or the view first, ids only"),
    ("db/folders.py", "_PLACE_OF"): (EXISTENCE_ONLY, "a move's places: _seen or the view first, ids only"),
    ("db/folders.py", "_PRIVATE_AMONG"): (TREE_WALK, "the private folders a move carries, ids only"),
    ("db/folders.py", "_HIDDEN_INSIDE"): (EXISTENCE_ONLY, "DELETE /folders/{id}: whether anything is hidden"),
    ("db/access.py", "_DOCUMENT_HOLD"): (
        EXISTENCE_ONLY,
        "a file found through the view: its place, ids only",
    ),
    ("db/access.py", "_CLOSED"): (EXISTENCE_ONLY, "a move preview: whether a department is Department only"),
    ("db/documents.py", "_NO_FOLDER"): (
        SHARED_WITH_WORKER,
        "an upload with no folder: its uploader's departments",
    ),
    ("db/autofile.py", "_HOLD_FOR"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/autofile/undo: _document_or_404 first",
    ),
    ("db/trash.py", "_LOCK_FAMILY"): (WRITE_AFTER_CHECK, "the family lock, after the view check (#595)"),
    ("db/trash.py", "_TRASH_IN_FOLDERS"): (
        WRITE_AFTER_CHECK,
        "DELETE /folders/{id}?contents=trash, an Admin's",
    ),
    ("db/trash.py", "_FAMILY_IN_TRASH"): (WRITE_AFTER_CHECK, "the family lock, after the view check (#595)"),
    # #594: where a restore writes the family when its folder is gone, read under the tree lock and
    # the family lock, after the route found the file through the view. The family's frozen values,
    # and whether its department or private folder is live (a row lock, FOR SHARE): that folder may
    # be one the restorer can't see, so a view would put the file somewhere wider.
    ("db/trash.py", "_KEPT_PLACE"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/restore: the view first; the family lock held",
    ),
    ("db/trash.py", "LIVE_FOLDERS"): (
        WRITE_AFTER_CHECK,
        "the row lock of a restore's write: the file (or the trashed folder, one()) through the view first",
    ),
    ("db/trashed_folders.py", "_SUBTREE"): (TREE_WALK, "restore and delete forever: one() (the view) first"),
    ("db/trashed_folders.py", "_CHAIN"): (
        TREE_WALK,
        "contents() keeps only what visible_trashed_folders holds",
    ),
    ("db/trashed_folders.py", "_CHILD_ITEMS"): (TREE_WALK, "the parent's own delete; items from the view"),
    ("db/trashed_folders.py", "_HELD"): (
        TREE_WALK,
        "who uploaded every file, for the restore rule; counts from views",
    ),
    ("db/trashed_folders.py", "_PATHS"): (
        TREE_WALK,
        "a path is shown only when visible_folders holds all of it",
    ),
    ("db/trashed_folders.py", "_ANY_LIVE"): (EXISTENCE_ONLY, "restore: one() first"),
    ("db/trashed_folders.py", "_TOP"): (WRITE_AFTER_CHECK, "restore and delete forever: one() first"),
    ("db/trashed_folders.py", "_MAKE_LIVE"): (WRITE_AFTER_CHECK, "restore: one() or the file first"),
    ("db/trashed_folders.py", "_RESTORE_IN"): (WRITE_AFTER_CHECK, "restore: one() first"),
    ("db/trashed_folders.py", "_INTO_LIVE"): (WRITE_AFTER_CHECK, "restore: one() or the file first"),
    ("db/trashed_folders.py", "_FILED_IN"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/restore: _document_or_404 first",
    ),
    ("db/trashed_folders.py", "_FAMILIES_IN"): (
        WRITE_AFTER_CHECK,
        "delete forever: one() and the hidden-inside rule first",
    ),
    ("db/trashed_folders.py", "_ANY_FILED"): (EXISTENCE_ONLY, "delete forever's last step"),
    ("db/usage.py", "_REPORT"): (ADMIN_NUMBERS, "GET /admin/usage: names and spend"),
    ("db/value_keys.py", "_BY_ID"): (SHARED_WITH_WORKER, f"a value's keys; {_WORDS}"),
    ("db/value_keys.py", "_UPSERT"): (SHARED_WITH_WORKER, f"a value's keys; {_WORDS}"),
    ("db/versions.py", "_KEPT_APART_AGAIN"): (WRITE_AFTER_CHECK, "separate: the view first"),
    ("db/versions.py", "_DECIDE"): (
        WRITE_AFTER_CHECK,
        "accept and reject: _LIVE (both sides through the view) first",
    ),
    ("db/versions.py", "_ACCEPT"): (WRITE_AFTER_CHECK, "accept: _LIVE (both sides through the view) first"),
    ("db/versions.py", "_TEXT"): (
        SHARED_WITH_WORKER,
        "version spotting; compare: both documents through the view first",
    ),
    ("db/versions.py", "_ME"): (SHARED_WITH_WORKER, "version spotting of the upload's own row"),
    ("db/versions.py", "_CANDIDATES"): (
        SHARED_WITH_WORKER,
        "version spotting; a suggestion is shown only through LIVE_SUGGESTION",
    ),
    ("db/versions.py", "_MY_MINHASH"): (SHARED_WITH_WORKER, "version spotting of the upload's own row"),
    ("db/versions.py", "_SUGGESTION"): (WRITE_AFTER_CHECK, "accept and join again: _LIVE / both files first"),
    ("db/versions.py", "_LINK_STATE"): (WRITE_AFTER_CHECK, "accept and join again: both files first"),
    ("db/versions.py", "_JOIN"): (WRITE_AFTER_CHECK, "accept and join again: both files first"),
    ("db/versions.py", "_FAMILY_TAGS"): (WRITE_AFTER_CHECK, "accept and join again: both files first"),
    ("db/versions.py", "_UNDO_STATE"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/version-undo: the view first",
    ),
    ("db/versions.py", "_HIGHEST_READY_CURRENT"): (WRITE_AFTER_CHECK, "undo and separate: the view first"),
    ("db/versions.py", "_SEPARATE_STATE"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/separate: the view first",
    ),
    ("db/versions.py", "_NEWEST_CURRENT"): (
        WRITE_AFTER_CHECK,
        "POST /documents/{id}/separate: the view first",
    ),
    ("db/versions.py", "_STATE"): (WRITE_AFTER_CHECK, "POST /documents/{id}/make-current: the view first"),
    ("db/word_review.py", "_FLAG_SPANS"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_FLAG_VALUES"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_CHANGES"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_DOCUMENT_STATE"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_INSERT_CHANGE"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_PAGE_VALUES"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_PAGE_PIECES"): (SHARED_WITH_WORKER, _WORDS),
    ("db/word_review.py", "_PAGE_TO_KEEP"): (SHARED_WITH_WORKER, _WORDS),
    ("db/zip_unpacks.py", "_COUNTS"): (
        SHARED_WITH_WORKER,
        "a zip's unpacking, of a document read through the view; zip_unpacks() blanks a hidden root",
    ),
}

# Ask and search (PRD 7 phase 4): PR 8 moves each onto the view and removes it from here.
PENDING_PR8: dict[tuple[str, str], str] = {
    ("answering/clarify.py", "_CANDIDATES"): "Did you mean",
    ("answering/clarify.py", "_NEAR"): "Did you mean: a district's places",
    ("answering/clarify.py", "_ANY_KEYS"): "Did you mean: any land record",
    ("answering/exhaustive.py", "_IN_SCOPE"): "every document holding a word",
    ("answering/exhaustive.py", "_MENTIONS"): "Also mentions",
    ("answering/exhaustive.py", "_PIECES"): "every document holding a word",
    ("answering/fixed_queries.py", "_AVAILABLE_FIELDS"): "totals",
    ("answering/fixed_queries.py", "_TOP_GROUP"): "totals",
    ("answering/fixed_queries.py", "_FIGURES"): "totals",
    ("answering/fixed_queries.py", "_EXTREME_VALUE"): "totals",
    ("answering/fixed_queries.py", "_COUNT_DOCUMENTS"): "counts",
    ("answering/fixed_queries.py", "_COUNT_INVOICES"): "counts",
    ("answering/fixed_queries.py", "_VALUE_REFS"): "totals",
    ("answering/hard_to_read.py", "_FLAGGED_PAGES"): "pages hard to read",
    ("answering/hard_to_read.py", "_WORD_MARKS"): "pages hard to read",
    ("answering/owners.py", "_RESOLVE"): "owners and land",
    ("answering/owners.py", "_OWNED"): "owners and land",
    ("answering/owners.py", "_KEYED"): "owners and land",
    ("answering/owners.py", "_EVERY_RECORD"): "owners and land",
    ("answering/owners.py", "_SHARES_AS_PRINTED"): "owners and land",
    ("answering/ready.py", "_TYPES"): "Home's ready answers",
    ("answering/ready.py", "_STAMP"): "Home's ready answers",
    ("answering/ready.py", "_TAGGED"): "Home's ready answers",
    ("answering/ready.py", "_WORDS"): "Home's ready answers",
    ("answering/unchecked.py", "_CARD_DOCUMENTS"): "unchecked figures",
    ("search/hybrid.py", "_WORD"): "keyword search",
    ("search/hybrid.py", "_FUZZY"): "typo search",
    ("search/hybrid.py", "_MEANING"): "meaning search",
    ("search/hybrid.py", "_MEANING_EXACT"): "meaning search",
    ("search/hybrid.py", "_DETAILS"): "search details",
    ("search/hybrid.py", "_NEIGHBOURS"): "wider evidence",
    ("search/hybrid.py", "_WHOLE_SIZE"): "whole documents",
    ("search/hybrid.py", "_WHOLE"): "whole documents",
    ("search/hybrid.py", "_TOPIC_PIECES"): "topics",
    ("search/hybrid.py", "_SECTION_PIECES"): "sections",
    ("search/hybrid.py", "_CARDS"): "cards",
    ("search/story_search.py", "_STORIES"): "Search my stories",
    ("db/chat.py", "messages_with_feedback"): "old conversations and their source picks",
    ("db/chat.py", "document_states"): "old answers' sources",
    ("db/chat.py", "live_document_names"): "chat memory",
    ("db/chat.py", "pins_for"): "Most relevant picks fed to Ask",
    ("db/chat.py", "MARKS"): "other people's Useful marks",
    ("db/organization_words.py", "approved"): "word suggestions Ask uses",
}
# PR 8's files: Ask, search, and the chat and word statements that feed Ask.
_PR8_FILES = ("answering/", "search/", "db/chat.py", "db/organization_words.py")


def _found() -> dict[tuple[str, str], scan.Owner]:
    """The statements the API reaches that read a table directly and are not passed by the rule."""
    return {
        (owner.file, owner.name): owner
        for owner, by_api in scan.owners()
        if by_api and (owner.reads & scan.PLACES or (owner.reads & scan.CONTENT and not owner.view))
    }


class SqlAccessGuardTest(unittest.TestCase):
    def test_every_direct_read_the_api_reaches_is_listed(self) -> None:
        found = _found()
        self.assertEqual(
            set(found) - set(ALLOWED) - set(PENDING_PR8), set(), "read through the view, or list it"
        )
        self.assertEqual(set(ALLOWED) & set(PENDING_PR8), set())

    def test_no_listed_statement_is_stale(self) -> None:
        """Each entry still reads a table directly and is still reached from the API: one moved onto the
        view, renamed or no longer reached leaves the list."""
        self.assertEqual((set(ALLOWED) | set(PENDING_PR8)) - set(_found()), set())

    def test_each_entry_has_one_of_the_seven_reasons_and_a_note(self) -> None:
        for key, (reason, note) in ALLOWED.items():
            with self.subTest(statement=key):
                self.assertIn(reason, REASONS)
                self.assertTrue(note.strip())
        for key, note in PENDING_PR8.items():
            with self.subTest(statement=key):
                self.assertTrue(note.strip())
                self.assertTrue(key[0].startswith(_PR8_FILES), "only Ask and search wait for PR 8")

    def test_admin_numbers_are_reached_only_from_admin_session_routes(self) -> None:
        """Decision 20 (#601): each ADMIN_NUMBERS statement is reached from a route, and only from
        routes that ask for an admin session themselves (``scan.routes``: their router, an
        AdminSession or require_admin_session named in the route, never only in a helper)."""
        routes = scan.routes()
        self.assertFalse(routes[("api/routers/documents.py", "list_documents")][1])  # a plain route
        for key, (reason, _) in ALLOWED.items():
            if reason != ADMIN_NUMBERS:
                continue
            reaching = {route: admin for route, (reached, admin) in routes.items() if key in reached}
            with self.subTest(statement=key):
                self.assertTrue(reaching)
                self.assertEqual({route for route, admin in reaching.items() if not admin}, set())

    def test_no_statement_the_worker_reaches_reads_a_view(self) -> None:
        """#595 condition 1, across modules: the worker (jobs/, worker_main, cli, pipeline, the alerts)
        runs as kaagaz_app with no person on a server, and would see nothing through a view."""
        self.assertEqual(scan.worker_view_readers(), set())

    def test_the_scanner_sees_a_direct_read_and_a_checked_one(self) -> None:
        self.assertEqual(scan.reads("SELECT count(*) FROM pages WHERE customer_id = :c"), {"pages"})
        self.assertEqual(
            scan.reads(" FROM visible_documents d JOIN folders f ON f.id = d.folder_id"), {"folders"}
        )
        self.assertEqual(scan.reads("DELETE FROM pages WHERE customer_id = :c"), set())
        # #599 review: what returns rows reads them; ONLY, quotes, brackets, long lists, small letters.
        self.assertEqual(scan.reads("DELETE FROM pages WHERE customer_id = :c RETURNING id"), {"pages"})
        self.assertEqual(scan.reads("UPDATE documents SET x = 1 WHERE id = :d"), set())
        self.assertEqual(scan.reads("UPDATE documents SET x = 1 WHERE id = :d RETURNING id"), {"documents"})
        self.assertEqual(scan.reads("SELECT 1 FROM ONLY documents"), {"documents"})
        self.assertEqual(scan.reads('SELECT 1 FROM "documents" d, public."pages" p'), {"documents", "pages"})
        self.assertEqual(
            scan.reads("SELECT 1 FROM (documents d JOIN pages p ON p.id = d.id)"), {"documents", "pages"}
        )
        self.assertEqual(
            scan.reads("SELECT 1 FROM visible_documents d, users u, pieces p WHERE true"), {"pieces"}
        )
        self.assertEqual(scan.reads("select 1 from pages where customer_id = :c"), {"pages"})
        self.assertEqual(
            scan.reads("SELECT 1 FROM unnest(CAST(:x AS uuid[])) AS w (k), pieces p"), {"pieces"}
        )
        self.assertEqual(scan.reads("SELECT 1 FROM documents d, pieces p"), {"documents", "pieces"})
        self.assertEqual(scan.reads("Answer only from pages the person sent"), set())  # prose
        found = _found()
        self.assertIn(("db/word_review.py", "_FLAG_SPANS"), found)  # shared with the worker: on the table
        self.assertNotIn(("db/listing.py", "list_values"), found)  # SEEN_DOCUMENT, named in its SQL
