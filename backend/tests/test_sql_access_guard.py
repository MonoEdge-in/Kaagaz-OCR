"""The SQL guard (PRD 7 section 10 and 11, "Guard tests"; #599): a statement the API reaches reads
``documents``, ``folders`` and ``trashed_folders`` only through their views, and a content table
(appendix A's 27) only through a document the person can see: the statement, or a fragment it
names, reads ``visible_documents`` (joined, ``visibility.SEEN_DOCUMENT``, or the outer row a
correlated fragment runs in). Anything else is on ALLOWED with one of the seven reasons and a note
naming why it may (for a statement shared with the worker, the route that checks the document
through the view first). Ask and search read only through the view (PR 8, #602): of theirs only
Home's stamp, a hashed key, is listed. The scanner is ``tests/sql_access_scan.py``; SQL inside
migrations (the owner-rights functions and the triggers) is reviewed with each migration and
pinned by test_isolation_schema.py, not here.

A text rule: a statement that joins the view for one table and reads another content table on its
own still passes. Review, and PR 9's row level security, are the backstop."""

import unittest

from tests import sql_access_scan as scan

# The seven reasons a statement the API reaches may read a table directly.
SHARED_WITH_WORKER = "shared with the worker, which must never read a view (#595 condition 1)"
WRITE_AFTER_CHECK = "a write, or the read or row lock of a write, after the route's check through the view"
TREE_WALK = "a walk of the folder tree by id; what is shown comes from a view"
ADMIN_NUMBERS = "an Admin screen's names and numbers, never content (decision 20; PR 10)"
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
    ("db/audit.py", "_INSERT_ABOUT"): (WRITE_AFTER_CHECK, "a file given: its folder and family, by id"),
    ("db/folders.py", "_IN_USE"): (
        WRITE_AFTER_CHECK,
        "PATCH /folders/{id}: _seen first, under the tree lock",
    ),
    ("db/answer_quality.py", "remove_learning"): (ADMIN_NUMBERS, "DELETE /admin/learned/{id}"),
    ("db/audit.py", "_INSERT"): (WRITE_AFTER_CHECK, "the audit row's folder and family, by id"),
    ("db/audit.py", "_ENTRIES"): (EXISTENCE_ONLY, "a feed entry's document name; _SEEN drops a hidden one"),
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
    ("db/ensemble.py", "_STOP"): (WRITE_AFTER_CHECK, "POST .../ensemble/{run}/stop: get_document first"),
    ("db/ensemble.py", "_RUN"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_ACTIVE_RUN"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_SCANNED"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/ensemble.py", "_LINES"): (SHARED_WITH_WORKER, _ENSEMBLE),
    ("db/folder_upload_items.py", "_COLUMNS"): (
        DUPLICATE_LOOKUP,
        "the uploader's items; views.seen_items blanks a hidden copy",
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
    ("db/folders.py", "_DESCENDANTS"): (TREE_WALK, "a folder's subtree, after folders_seen on the folder"),
    ("db/folders.py", "_DETACH_TRASHED"): (WRITE_AFTER_CHECK, "DELETE /folders/{id}: _seen first"),
    ("db/folders.py", "_PARENT"): (TREE_WALK, "PATCH and DELETE /folders: _seen first"),
    ("db/folders.py", "_SUMMARY"): (ADMIN_NUMBERS, "an Admin's delete dialog: true totals (PRD 7 section 5)"),
    # #594: the refusal before a keep-delete's writes; it compares the stored values of the folder and
    # of where its contents go, which a view would hide from an Admin (names, not contents).
    ("db/folders.py", "_SAME_ACCESS"): (
        WRITE_AFTER_CHECK,
        "DELETE /folders/{id}?contents=keep, an Admin's: _seen first",
    ),
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
    ("db/listing.py", "_FILE_COLUMNS"): (
        TREE_WALK,
        "the department above a file: shown from visible_folders",
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
    ("db/runs.py", "_OPEN"): (SHARED_WITH_WORKER, "the pipeline's run"),
    ("db/tags.py", "_REMOVE_OTHERS"): (WRITE_AFTER_CHECK, "PUT /documents/{id}/tags: _document_or_404 first"),
    ("db/tags.py", "_ADD"): (WRITE_AFTER_CHECK, "PUT /documents/{id}/tags: _document_or_404 first"),
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
        "a zip's unpacking, of a document read through the view",
    ),
}


# PRD 7 PR 9a (#606): what each listed statement reads once the per-person database rule is on.
# SAME: the same rows (a by-id read or write after the route's check through the view, class I).
# FEWER_OK: fewer rows, and that is right (why, after the colon). FIXED: it would read fewer, and
# PR 9a changed it. Every entry of ALLOWED has one; the reasons that read by id after the view are
# SAME unless named. OWNER_FN: the statements 0091's owner-rights functions replaced; the scanner
# must no longer find them.
OWNER_FN: dict[tuple[str, str], str] = {
    ("answering/ready.py", "_STAMP"): "F11 kaagaz_ready_stamp",
    ("db/access.py", "_GIVEN_TO"): "F12 kaagaz_admin_given_to",
    ("db/admin.py", "_COUNTS"): "F12 kaagaz_admin_stats",
    ("db/admin.py", "_DOCUMENTS_BY_STATUS"): "F12 kaagaz_admin_stats",
    ("db/admin.py", "_FAILED"): "F12 kaagaz_admin_failed",
    ("db/admin.py", "_FAILED_COUNT"): "F12 kaagaz_admin_failed",
    ("db/admin.py", "_KEPT"): "F12 kaagaz_admin_stats",
    ("db/admin.py", "_MEMBERS"): "F12 kaagaz_admin_member_numbers",
    ("db/audit.py", "_SEEN"): "F5 kaagaz_existing",
    ("db/ensemble.py", "_RUNS_THIS_HOUR"): "F9 kaagaz_ensemble_runs_this_hour",
    ("db/folder_upload_items.py", "_ALREADY_IN"): "F4 kaagaz_duplicate",
    ("db/folders.py", "_DEPTH"): "kaagaz_folder_depth",
    ("db/folders.py", "_MOVE_CHECK"): "kaagaz_folder_depth",
    ("db/listing.py", "_USAGE_ROWS"): "F12 kaagaz_admin_usage_rows",
    ("db/runs.py", "_PAGES_BY_ROUTE"): "F12 kaagaz_admin_reader_pages",
    ("db/runs.py", "_READER_PAGES"): "F12 kaagaz_admin_reader_pages",
    ("db/storage_limits.py", "_ROOM"): "F7 kaagaz_storage_used",
    ("db/trashed_folders.py", "_ANY_FILED"): "F6 kaagaz_hidden_inside",
    ("db/trashed_folders.py", "_ANY_LIVE"): "F5 kaagaz_existing",
    ("db/usage.py", "_REPORT"): "F12 kaagaz_admin_usage",
}
_SAME_BY_REASON = frozenset({WRITE_AFTER_CHECK})
# PR 9a (#606): each statement the API reaches that asks "NOT EXISTS (... FROM <covered>", and why the
# rule hiding a row there is right (a new one fails the test until it is reviewed here).
_ALL_OR_NONE = "FEWER_OK:a family's versions are seen all or none (shares go to every live version)"
NOT_EXISTS_CHECKED: dict[tuple[str, str], str] = {
    ("answering/fixed_queries.py", "_COUNT_DOCUMENTS"): "SAME:the visible document's own invoice",
    ("answering/fixed_queries.py", "_EXTREME_VALUE"): "SAME:the visible document's own invoice",
    ("answering/fixed_queries.py", "_TOP_GROUP"): "SAME:the visible document's own invoice",
    ("answering/hard_to_read.py", "_WORD_MARKS"): "SAME:the visible document's own word changes",
    ("db/chat.py", "MARKS"): "SAME:the visible document's own learning removals",
    ("db/documents.py", "REFUSED_ALONE"): _ALL_OR_NONE,
    ("db/folder_uploads.py", "_INSERT_LEVEL"): "FEWER_OK:Q12, a hidden sibling's name meets its unique index",
    ("db/folders.py", "_FREE"): "FEWER_OK:Q12, a hidden sibling's name meets its unique index",
    ("db/folders.py", "_MOVE_FREE"): "FEWER_OK:Q12, a hidden sibling's name meets its unique index",
    ("db/folders.py", "_PRUNE"): "FEWER_OK:a hidden child's key refuses the delete (prune_empty keeps it)",
    ("db/folders.py", "_SAME_ACCESS"): "SAME:an Admin's, who sees every folder",
    ("db/versions.py", "_CANDIDATES"): _ALL_OR_NONE,
    ("db/versions.py", "_HIGHEST_READY_CURRENT"): _ALL_OR_NONE,
    ("db/versions.py", "_LINK_STATE"): _ALL_OR_NONE,
    ("db/versions.py", "_NEWEST_CURRENT"): _ALL_OR_NONE,
}
# Q2 (#606): the access tables carry no person rule (they say who sees what, not what is in a file);
# these API statements read them, the Access screens' (PR 10a), and a new one fails until reviewed.
ACCESS_TABLES = frozenset({"document_shares", "lock_grants", "department_members", "private_folder_members"})
ACCESS_READERS = {
    ("db/access.py", "_DEPARTMENTS"), ("db/access.py", "_NAMED_ON"), ("db/access.py", "_PEOPLE_ON"),
    ("db/access.py", "_HEADS_OF"), ("db/access.py", "_MEMBERS"), ("db/access.py", "_IS_MEMBER"),
    ("db/access.py", "_LEAVE"), ("db/access.py", "_UNNAME"), ("db/access.py", "_SHARES"),
    ("db/folders.py", "_IN_USE"),
}  # fmt: skip
PR9: dict[tuple[str, str], str] = {
    ("db/answer_quality.py", "remove_learning"): "FEWER_OK:Q14, the narrowed Admin lists",
    ("db/audit.py", "_ENTRIES"): "FIXED:F13 names the Admin's log; the feeds drop a hidden one (_SEEN, F5)",
    (
        "db/autofile.py",
        "_DEPARTMENT_ABOVE",
    ): "FEWER_OK:class H, the department above a folder the person sees",
    (
        "db/documents.py",
        "REFUSED_ALONE",
    ): "FEWER_OK:a hidden refused copy is a hidden duplicate, not replaced",
    (
        "db/documents.py",
        "_REFUSED_COPY",
    ): "FEWER_OK:a hidden refused copy is a hidden duplicate, not replaced",
    ("db/documents.py", "_SELECT"): "FIXED:F4 and _copy_already_in: a hidden copy names no row",
    ("db/folder_upload_items.py", "_COLUMNS"): "FEWER_OK:views.seen_items blanks a hidden copy either way",
    ("db/folder_upload_items.py", "_PROGRESS"): "SAME",
    ("db/folders.py", "_DESCENDANTS"): "FEWER_OK:class H, the subtree the person sees (an Admin sees all)",
    ("db/folders.py", "_FREE"): "FEWER_OK:Q12, a hidden sibling's name is a 409 from its unique index",
    ("db/folders.py", "_PARENT"): "SAME",
    ("db/folders.py", "_PATH"): "FIXED:a path not starting at the top is not shown",
    (
        "db/folders.py",
        "_SUMMARY",
    ): "FEWER_OK:a member's dialog; an Admin's is F12 kaagaz_admin_folder_summary",
    ("db/listing.py", "_BATCH_SIZE"): "FEWER_OK:the uploader's own batch, never hidden from them",
    ("db/listing.py", "_DASHBOARD_DEPARTMENTS"): "SAME",
    ("db/listing.py", "_FILE_COLUMNS"): "SAME",
    ("db/listing.py", "_OUTSIDE_DEPARTMENTS"): "FEWER_OK:class H, departments from visible_folders",
    ("db/organization_words.py", "submit"): "FIXED:F10 for the cap; the duplicate check is the viewer's own",
    ("db/trash.py", "LIVE_FOLDERS"): "FIXED:trash.live_folders asks kaagaz_existing (F5)",
    (
        "db/trashed_folders.py",
        "_CHAIN",
    ): "FEWER_OK:the visible tail is shown; a restore through a hidden one is 9b's",
    ("db/trashed_folders.py", "_CHILD_ITEMS"): "SAME",
    ("db/trashed_folders.py", "_HELD"): "FIXED:F6 for what is hidden inside",
    ("db/trashed_folders.py", "_PATHS"): "FIXED:a path must reach the top",
    ("db/trashed_folders.py", "_SUBTREE"): "FEWER_OK:an Admin sees every folder; a member's is refused (F6)",
    # Shared with the worker (no rule) or a fragment: each said, never assumed (PR 9a review).
    (
        "db/autofile.py",
        "_BY_KEY",
    ): "FEWER_OK:auto-filing runs as the worker; a folder hidden from a person is not reused (Q12)",
    (
        "db/autofile.py",
        "_BY_NAME",
    ): "FEWER_OK:auto-filing runs as the worker; a folder hidden from a person is not reused (Q12)",
    (
        "db/autofile.py",
        "_CHILD",
    ): "FEWER_OK:auto-filing runs as the worker; a folder hidden from a person is not reused (Q12)",
    (
        "db/autofile.py",
        "_CHILD_NAMED",
    ): "FEWER_OK:auto-filing runs as the worker; a folder hidden from a person is not reused (Q12)",
    (
        "db/autofile.py",
        "_DEPARTMENTS",
    ): "FEWER_OK:auto-filing runs as the worker; a person's run sees their departments",
    ("db/autofile.py", "_INVOICE"): "SAME",
    (
        "db/autofile.py",
        "_MOST_SEEN",
    ): "FEWER_OK:auto-filing runs as the worker; a person's run learns from what they see",
    ("db/autofile.py", "_PRE_CHECK"): "SAME",
    (
        "db/autofile.py",
        "_SAME_NUMBER",
    ): "FEWER_OK:auto-filing runs as the worker; a folder hidden from a person is not reused (Q12)",
    ("db/autofile.py", "_STATE"): "SAME",
    ("db/document_stories.py", "_GET"): "SAME",
    (
        "db/documents.py",
        "PgDocuments",
    ): "FIXED:F4 and _copy_already_in; no RETURNING or ON CONFLICT on a new version",
    ("db/documents.py", "_ENSEMBLE_RUNNING"): "SAME",
    ("db/documents.py", "_PAGES_TO_READ_AGAIN"): "SAME",
    ("db/documents.py", "_READY_STATE"): "SAME",
    ("db/documents.py", "_READ_BEFORE"): "SAME",
    ("db/documents.py", "_REGIONS_TO_READ_AGAIN"): "SAME",
    ("db/ensemble.py", "_ACTIVE_RUN"): "SAME",
    ("db/ensemble.py", "_LINES"): "SAME",
    ("db/ensemble.py", "_RUN"): "SAME",
    ("db/ensemble.py", "_SCANNED"): "SAME",
    (
        "db/folder_uploads.py",
        "_DEPTHS",
    ): "FEWER_OK:class H, depths of the folders the person sees; the limit is kaagaz_folder_depth's",
    (
        "db/folder_uploads.py",
        "_FIND_LEVEL",
    ): "FEWER_OK:Q12, a hidden folder of that name is not reused; its unique index refuses the name",
    (
        "db/folder_uploads.py",
        "_INSERT_LEVEL",
    ): "FEWER_OK:Q12, a hidden folder of that name is not reused; its unique index refuses the name",
    (
        "db/folder_uploads.py",
        "_REUSE",
    ): "FEWER_OK:Q12, a hidden folder of that name is not reused; its unique index refuses the name",
    (
        "db/folder_uploads.py",
        "_SAME_NAME",
    ): "FEWER_OK:Q12, a hidden folder of that name is not reused; its unique index refuses the name",
    ("db/listing.py", "_CLEAR_COPY"): "SAME",
    ("db/listing.py", "_FIRST_UNREADABLE"): "SAME",
    ("db/listing.py", "_NEEDS_ATTENTION"): "SAME",
    ("db/listing.py", "_PAGE"): "SAME",
    ("db/listing.py", "_VALUES_FOUND"): "SAME",
    ("db/listing.py", "_WITH_TAGS"): "SAME",
    ("db/models.py", "_AREA"): "FEWER_OK:Q14, an estimate of model use from what the person sees",
    ("db/models.py", "_DOCUMENT"): "SAME",
    ("db/reading_days.py", "_STATE"): "FIXED:F8 for the month's spend; the asked document by id",
    ("db/review.py", "add_correction"): "SAME",
    ("db/runs.py", "_OPEN"): "SAME",
    ("db/value_keys.py", "_BY_ID"): "SAME",
    ("db/value_keys.py", "_UPSERT"): "SAME",
    (
        "db/versions.py",
        "_CANDIDATES",
    ): "FEWER_OK:p1, version spotting compares only with files the uploader sees",
    ("db/versions.py", "_ME"): "SAME",
    ("db/versions.py", "_MY_MINHASH"): "SAME",
    ("db/versions.py", "_TEXT"): "SAME",
    ("db/word_review.py", "_CHANGES"): "SAME",
    ("db/word_review.py", "_DOCUMENT_STATE"): "SAME",
    ("db/word_review.py", "_FLAG_SPANS"): "SAME",
    ("db/word_review.py", "_FLAG_VALUES"): "SAME",
    ("db/word_review.py", "_INSERT_CHANGE"): "SAME",
    ("db/word_review.py", "_PAGE_PIECES"): "SAME",
    ("db/word_review.py", "_PAGE_TO_KEEP"): "SAME",
    ("db/word_review.py", "_PAGE_VALUES"): "SAME",
    ("db/zip_unpacks.py", "_COUNTS"): "SAME",
}
# PR 9a (#606, plan 3.2): an organisation-wide count, sum or max with no document id, or a LEFT JOIN
# of a covered table tested for NULL, reads fewer once the rule is on; each reviewed here.
AGGREGATES_CHECKED: dict[tuple[str, str], str] = {
    (
        "db/autofile.py",
        "_MOST_SEEN",
    ): "FEWER_OK:auto-filing runs as the worker; a person's run learns from what they see",
    ("db/folder_upload_items.py", "_PROGRESS"): "SAME:the uploader's own stored files",
    ("db/listing.py", "_BATCH_SIZE"): "SAME:the uploader's own batch",
    ("db/listing.py", "_COLUMNS"): "SAME:per row of visible_documents",
    ("db/listing.py", "_DASHBOARD_DEPARTMENTS"): "FEWER_OK:the person's dashboard counts what they see",
    ("db/listing.py", "_DASHBOARD_TILES"): "FEWER_OK:the person's dashboard counts what they see",
    ("db/listing.py", "_FILE_COLUMNS"): "SAME:per row of visible_documents",
    ("db/listing.py", "_PAGE"): "SAME:per row of visible_documents",
    ("db/listing.py", "_VALUES_FOUND"): "SAME:per row of visible_documents",
    ("db/listing.py", "_WITH_TAGS"): "SAME:per row of visible_documents",
    ("db/models.py", "_AREA"): "FEWER_OK:Q14, an estimate of model use",
    ("db/review.py", "_COUNT"): "FEWER_OK:the review queue the person may work",
    (
        "db/trashed_folders.py",
        "_HELD",
    ): "FIXED:F6 for what is hidden inside; counts shown are of what they see",
    ("db/versions.py", "_VIEW"): "SAME:one family the person sees",
}


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
        self.assertEqual(set(found) - set(ALLOWED), set(), "read through the view, or list it")

    def test_no_listed_statement_is_stale(self) -> None:
        """Each entry still reads a table directly and is still reached from the API: one moved onto the
        view, renamed or no longer reached leaves the list."""
        self.assertEqual(set(ALLOWED) - set(_found()), set())

    def test_each_entry_has_a_verdict_for_the_database_rule(self) -> None:
        """PR 9a (#606): SAME, FEWER_OK or FIXED with why, for every listed statement; an OWNER_FN one
        is gone from the list and from what the scanner finds."""
        for key, (reason, _note) in ALLOWED.items():
            verdict = PR9.get(key, "SAME" if reason in _SAME_BY_REASON else "")
            with self.subTest(statement=key):
                self.assertRegex(verdict, r"^(SAME|FEWER_OK:.+|FIXED:.+)$")
        self.assertEqual(set(PR9) - set(ALLOWED), set(), "a verdict for no listed statement")
        self.assertEqual(set(OWNER_FN) & (set(ALLOWED) | set(_found())), set())

    def test_each_organisation_wide_aggregate_on_a_covered_table_is_reviewed(self) -> None:
        """PR 9a (#606, plan 3.2, classes B and C): a new one fails until it is reviewed here."""
        self.assertEqual(scan.aggregate_readers(), set(AGGREGATES_CHECKED))
        for verdict in AGGREGATES_CHECKED.values():
            self.assertRegex(verdict, r"^(SAME|FEWER_OK|FIXED):.+$")

    def test_each_not_exists_on_a_covered_table_is_reviewed(self) -> None:
        """PR 9a (#606, classes B and C): "not in the table" reads as gone once the rule hides a row."""
        self.assertEqual(set(scan.not_exists_readers()), set(NOT_EXISTS_CHECKED))

    def test_the_api_reads_no_learned_correction_and_only_the_pinned_access_rows(self) -> None:
        """Q1 (#606): learned_corrections carries no person rule, so no statement the API reaches reads
        it (a screen that needs it goes through an owner-rights function). Q2: the access tables are
        read only by the statements pinned in ACCESS_READERS."""
        found = {
            (o.file, o.name): o.reads
            for o, by_api in scan.owners(ACCESS_TABLES | {"learned_corrections"})
            if by_api
        }
        self.assertEqual({key for key, tables in found.items() if "learned_corrections" in tables}, set())
        self.assertEqual(set(found), ACCESS_READERS)

    def test_each_entry_has_one_of_the_seven_reasons_and_a_note(self) -> None:
        for key, (reason, note) in ALLOWED.items():
            with self.subTest(statement=key):
                self.assertIn(reason, REASONS)
                self.assertTrue(note.strip())

    def test_ask_and_search_read_only_through_the_view(self) -> None:
        """PR 8 (#602): no Ask or search statement is passed by a reason (Changed on purpose, #606:
        Home's stamp is 0091's kaagaz_ready_stamp now); a search statement written on the table is
        found; and none of hybrid's is."""
        listed = {key for key in ALLOWED if key[0].startswith(("answering/", "search/"))}
        self.assertEqual(listed, set())
        ask = "SELECT p.id FROM pieces p JOIN documents d ON d.id = p.document_id WHERE p.customer_id = :c"
        self.assertEqual(scan.reads(ask), {"pieces", "documents"})
        self.assertEqual({key for key in _found() if key[0] == "search/hybrid.py"}, set())

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
