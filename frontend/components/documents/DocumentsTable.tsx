"use client";

// The list of this customer's documents: one table, reflowed into cards when
// the list is narrower than 48rem (Documents.module.css). Names, tags and
// status messages are shown as plain text. Each row has an Actions button
// (DocumentActions.tsx). Move to Trash, and Delete forever in Trash, ask
// first; focus goes to Cancel, and back to Actions if it is cancelled. Restore
// needs no question: nothing is lost. Ticked rows can be moved to Trash
// together; in Trash they can be restored together, or deleted forever together
// by whoever may (canDeleteForever, asked first; a granted member or reviewer
// types their password in that step). In Trash the list shows when each
// document was trashed. Each row is the family's
// current version; a newer one still being read, or one that failed, gets a
// line under the name ("Version 3: ..."), linking to it. The Type column
// gives the file type, and under it what kind of document it is ("Invoice",
// or "Unknown" without the AI model's card: PRD 2, D6). Names wrap in full at
// their _ . - and @ marks (WrapName.tsx). The By cell says "Uploaded by" in
// words; the full table hides those words under its By header. A document with
// reading problems gets chips in its status ("2 pages unreadable", PRD 5 U2), and
// a reviewer or an admin can have ticked documents read again, one at a time,
// after an inline question.

import Link from "next/link";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { canReprocess } from "@/components/viewer/reprocess";
import { newerVersionText } from "@/components/viewer/versions";
import {
  ApiError,
  confirmPassword,
  deleteDocument,
  deleteDocumentForever,
  reprocessDocument,
  restoreDocument,
} from "@/lib/api";
import { useSession } from "@/lib/session";
import { docTypeLabel, formatBytes, formatDate, formatDateTime, formatRelative, typeLabel } from "@/lib/format";
import type { DocumentSummary, Me, Tag } from "@/lib/types";
import { EditDialog, RowMenu } from "./DocumentActions";
import type { FolderTree } from "./folderTree";
import { BusyLabel } from "./BusyLabel";
import { attentionWords } from "./library";
import type { EditAction, RowAction } from "./library";
import { deletedMessage, formatProgressPercent } from "./logic";
import { StatusChip } from "./StatusChip";
import { TimeLeft } from "./TimeLeft";
import { WrapName } from "./WrapName";
import {
  type BulkAction,
  bulkDoneMessage,
  bulkFailedMessage,
  bulkForeverQuestion,
  bulkReadQuestion,
  bulkTrashQuestion,
  canDeleteForever,
  deletedForeverMessage,
  foreverNeedsPassword,
  foreverQuestion,
  lostTheRight,
  restoredMessage,
  runForever,
  selectable,
  trashQuestion,
} from "./trash";
import styles from "./Documents.module.css";
import library from "./Library.module.css";

/** What a row reports back to the screen. */
export interface RowEvents {
  /** Moved to Trash, restored or deleted for good: announce it and ask for the list again. */
  onRemoved(message: string): void;
  /** Renamed, moved or tagged: announce it and ask for the list again. */
  onSaved(message: string): void;
  /** After a move the row may be gone: focus the list's heading. */
  onFocusList(): void;
  onTagCreated(): void;
  /** "Upload new version": the screen opens the file picker for this document. */
  onUploadVersion(document: DocumentSummary): void;
}

interface Props extends RowEvents {
  items: DocumentSummary[];
  inTrash: boolean;
  me: Me;
  now: number;
  tree: FolderTree;
  tags: Tag[] | null;
}

export function DocumentsTable({ items, inTrash, me, now, tree, tags, ...events }: Props) {
  const { onRemoved } = events;
  const { refresh } = useSession();
  const canForever = canDeleteForever(me);
  const askPassword = foreverNeedsPassword(me);
  // Read again (PRD 5 U2): a reviewer or an admin, for a document that can be read again.
  const readable = useCallback(
    (item: DocumentSummary) => !inTrash && canReprocess(me.user.role, item),
    [inTrash, me.user.role],
  );
  const deletableItems = useMemo(
    () => items.filter((item) => selectable(item, inTrash, canForever) || readable(item)),
    [items, inTrash, canForever, readable],
  );
  const [password, setPassword] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  // The selection's action waiting for its "now" button: Move to Trash or Delete forever.
  const [confirming, setConfirming] = useState<BulkAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<BulkAction | null>(null);
  const [deleteProgress, setDeleteProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selectAll = useRef<HTMLInputElement | null>(null);
  const visibleIds = useMemo(() => new Set(items.map((item) => item.id)), [items]);
  const visibleSelectedIds = useMemo(
    () => new Set([...selectedIds].filter((id) => visibleIds.has(id))),
    [selectedIds, visibleIds],
  );
  const selectedCount = visibleSelectedIds.size;
  // Restore (and Move to Trash) only for rows with can_delete; Delete forever for every ticked row.
  const restorableIds = items
    .filter((item) => item.can_delete && visibleSelectedIds.has(item.id))
    .map((item) => item.id);
  const readableIds = items
    .filter((item) => readable(item) && visibleSelectedIds.has(item.id))
    .map((item) => item.id);
  const allSelected = deletableItems.length > 0 && selectedCount === deletableItems.length;
  const partiallySelected = selectedCount > 0 && !allSelected;

  // Stable, with the row events below, so a poll that changes one row renders only that row.
  const toggle = useCallback((id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setError(null);
  }, []);
  const latestEvents = useRef(events);
  useEffect(() => {
    latestEvents.current = events;
  });
  const rowEvents = useMemo<RowEvents>(
    () => ({
      onRemoved: (message) => latestEvents.current.onRemoved(message),
      onSaved: (message) => latestEvents.current.onSaved(message),
      onFocusList: () => latestEvents.current.onFocusList(),
      onTagCreated: () => latestEvents.current.onTagCreated(),
      onUploadVersion: (document) => latestEvents.current.onUploadVersion(document),
    }),
    [],
  );

  // Stable, so it focuses Cancel once, when the question opens (never the destructive button).
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);

  function toggleAll() {
    setSelectedIds(allSelected ? new Set() : new Set(deletableItems.map((item) => item.id)));
    setError(null);
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setConfirming(null);
    setError(null);
    setPassword("");
  }

  // Each selected document in turn. The ones that worked leave the list (it is
  // asked for again); the ones that failed stay ticked, to try again.
  async function runSelected(action: BulkAction) {
    const ids =
      action === "forever" ? Array.from(visibleSelectedIds) : action === "read" ? readableIds : restorableIds;
    setBusy(true);
    setBusyAction(action);
    setDeleteProgress({ done: 0, total: ids.length });
    setError(null);
    const typed = action === "forever" && askPassword ? password : null;
    const calls = { confirm: confirmPassword, remove: BULK_CALLS[action] };
    const outcome = await runForever(ids, typed, calls, (handled) =>
      setDeleteProgress({ done: handled, total: ids.length }),
    );
    setPassword("");
    setBusy(false);
    setBusyAction(null);
    setDeleteProgress(null);
    const failure = outcome.kind === "password" ? outcome.error : outcome.result.firstError;
    if (lostTheRight(failure)) refresh();
    // A refused password deleted nothing: the question stays open, with why.
    if (outcome.kind === "password") {
      setError(errorText(failure));
      return;
    }
    const { result } = outcome;
    setSelectedIds(new Set(result.failed));
    setConfirming(null);
    if (result.failed.length > 0) {
      setError(bulkFailedMessage(action, result.failed.length, errorText(result.firstError)));
    }
    if (result.done.length > 0) {
      // Read again keeps the rows (they are read again); the others leave the list.
      if (action === "read") events.onSaved(bulkDoneMessage(action, result.done.length));
      else onRemoved(bulkDoneMessage(action, result.done.length));
    }
    // The buttons pressed are gone now: keep keyboard focus on the selection bar.
    selectAll.current?.focus();
  }

  const nowButton = confirming && (
    <button
      type="button"
      className={confirming === "read" ? "btn btn-primary" : `btn btn-danger ${styles.apart}`}
      onClick={() => void runSelected(confirming)}
      disabled={busy || (confirming === "forever" && askPassword && password === "")}
    >
      <Icon name={confirming === "read" ? "refresh" : "trash"} />
      <BusyLabel busy={busy} idle={NOW_WORDS[confirming]} working={BUSY_WORDS[confirming]} />
    </button>
  );

  return (
    <>
      {deletableItems.length > 0 && (
        <div className={styles.selectionBar} role="toolbar" aria-label="Document selection actions">
          <label className={styles.selectAll}>
            <input
              type="checkbox"
              checked={allSelected}
              aria-checked={partiallySelected ? "mixed" : allSelected}
              onChange={toggleAll}
              ref={(input) => {
                selectAll.current = input;
                if (input) input.indeterminate = partiallySelected;
              }}
            />
            <span>
              {allSelected
                ? "All visible documents selected"
                : partiallySelected
                  ? `${selectedCount} selected`
                  : "Select all visible"}
            </span>
          </label>
          {selectedCount > 0 && !confirming && (
            <>
              {inTrash ? (
                <>
                  {restorableIds.length > 0 && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void runSelected("restore")}
                      disabled={busy}
                    >
                      <Icon name="history" />
                      <BusyLabel busy={busy} idle={`Restore (${restorableIds.length})`} working="Restoring…" />
                    </button>
                  )}
                  {canForever && (
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={() => setConfirming("forever")}
                      disabled={busy}
                    >
                      <Icon name="trash" />
                      Delete forever ({selectedCount})
                    </button>
                  )}
                </>
              ) : (
                <>
                  {readableIds.length > 0 && (
                    <button type="button" className="btn" onClick={() => setConfirming("read")}>
                      <Icon name="refresh" />
                      Read again ({readableIds.length})
                    </button>
                  )}
                  {restorableIds.length > 0 && (
                    <button type="button" className="btn btn-danger" onClick={() => setConfirming("trash")}>
                      <Icon name="trash" />
                      Move to Trash ({restorableIds.length})
                    </button>
                  )}
                </>
              )}
              <button type="button" className="btn btn-quiet" onClick={clearSelection} disabled={busy}>
                <Icon name="close" />
                Clear selection
              </button>
            </>
          )}
          {confirming && (
            <div
              className={styles.bulkConfirm}
              role="group"
              aria-label={CONFIRM_LABEL[confirming]}
            >
              <span>
                {confirming === "forever"
                  ? bulkForeverQuestion(selectedCount)
                  : confirming === "read"
                    ? bulkReadQuestion(readableIds.length)
                    : bulkTrashQuestion(restorableIds.length)}
              </span>
              {confirming === "forever" && askPassword && (
                <PasswordField
                  id="bulk-forever-password"
                  label="Your password"
                  autoComplete="current-password"
                  value={password}
                  onChange={setPassword}
                />
              )}
              {/* [Destructive] ... [Cancel], or [Cancel] [Read again now]; focus starts on Cancel. */}
              {confirming !== "read" && nowButton}
              <button ref={focusOnMount} type="button" className="btn" onClick={clearSelection} disabled={busy}>
                <Icon name="close" />
                Cancel
              </button>
              {confirming === "read" && nowButton}
            </div>
          )}
          {busy && deleteProgress && (
            <div className={styles.progressRow} aria-live="polite">
              <progress
                className={styles.progress}
                value={deleteProgress.done}
                max={deleteProgress.total}
                aria-label={PROGRESS_LABEL[busyAction ?? "trash"]}
              />
              <span className={`muted tabular ${styles.small} ${styles.barText}`} aria-hidden="true">
                {formatProgressPercent(deleteProgress.done, deleteProgress.total)}
              </span>
            </div>
          )}
          {error && <p className={`error-text ${styles.selectionError}`} role="alert">{error}</p>}
        </div>
      )}
      <table className={`table ${styles.docs}`} role="table">
        <caption className="visually-hidden">{inTrash ? "Documents in Trash" : "Your documents"}</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" className={styles.selectColumn}>
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col" role="columnheader" className={styles.nameColumn}>Name</th>
            <th scope="col" role="columnheader" className={styles.statusColumn}>Status</th>
            <th scope="col" role="columnheader" className={styles.typeColumn}>Type</th>
            <th scope="col" role="columnheader" className={styles.sizeColumn}>Size</th>
            <th scope="col" role="columnheader" className={styles.uploadedColumn}>
              {inTrash ? "Trashed" : "Uploaded"}
            </th>
            <th scope="col" role="columnheader" className={styles.byColumn}>By</th>
            <th scope="col" role="columnheader" className={styles.actionsColumn}>
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {items.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              whenText={whenText(document, inTrash, now)}
              inTrash={inTrash}
              deletable={selectable(document, inTrash, canForever) || readable(document)}
              canForever={canForever}
              askPassword={askPassword}
              selected={selectedIds.has(document.id)}
              onToggle={toggle}
              tree={tree}
              tags={tags}
              events={rowEvents}
            />
          ))}
        </tbody>
      </table>
    </>
  );
}

// One call per document for each selection action; the server checks every rule again.
/** The list's scroll as a document opens, so Back can return to the same row (DocumentsView). */
export const listScrollKey = () => `docs-y:${location.search}`;
function rememberListScroll() {
  try {
    sessionStorage.setItem(listScrollKey(), String(Math.round(scrollY)));
  } catch {
    // Storage blocked (private window): Back starts at the top, as before.
  }
}

const BULK_CALLS: Record<BulkAction, (id: string) => Promise<unknown>> = {
  trash: deleteDocument,
  restore: restoreDocument,
  forever: deleteDocumentForever,
  read: reprocessDocument,
};

const CONFIRM_LABEL: Record<BulkAction, string> = {
  trash: "Confirm moving to Trash",
  restore: "Confirm restoring",
  forever: "Confirm deleting for good",
  read: "Confirm reading again",
};
const NOW_WORDS: Record<BulkAction, string> = {
  trash: "Move now",
  restore: "Restore now",
  forever: "Delete now",
  read: "Read again now",
};
const BUSY_WORDS: Record<BulkAction, string> = {
  trash: "Moving…",
  restore: "Restoring…",
  forever: "Deleting…",
  read: "Starting…",
};

/** The server's words for a refusal, else a hint about the connection. */
function errorText(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "Check your connection, then try again.";
}

const PROGRESS_LABEL: Record<BulkAction, string> = {
  trash: "Moving to Trash",
  restore: "Restoring",
  forever: "Deleting for good",
  read: "Starting to read again",
};

type Confirming = "trash" | "forever" | null;

// In Trash the time column is when it was trashed; elsewhere, when it was uploaded.
const whenOf = (document: DocumentSummary, inTrash: boolean) =>
  inTrash && document.deleted_at !== null ? document.deleted_at : document.created_at;

// Worked out here, so a row renders again only when its own words change, not on every poll.
function whenText(document: DocumentSummary, inTrash: boolean, now: number): string {
  const when = whenOf(document, inTrash);
  const relative = formatRelative(when, now);
  return relative === formatDate(when) ? relative : `${relative} · ${formatDate(when)}`;
}

const DocumentRow = memo(function DocumentRow({
  document,
  whenText,
  inTrash,
  deletable,
  canForever,
  askPassword,
  selected,
  onToggle,
  tree,
  tags,
  events,
}: {
  document: DocumentSummary;
  whenText: string;
  inTrash: boolean;
  deletable: boolean; // it gets a checkbox (selectable)
  canForever: boolean;
  askPassword: boolean;
  selected: boolean;
  onToggle: (id: string) => void;
  tree: FolderTree;
  tags: Tag[] | null;
  events: RowEvents;
}) {
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [editing, setEditing] = useState<EditAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const { refresh } = useSession();
  const actionsButton = useRef<HTMLButtonElement | null>(null);
  const refocusActions = useRef(false);
  const afterEdit = useRef<"actions" | "list">("actions");

  // Stable callback refs, so each runs once when its button appears.
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);
  const holdActions = useCallback((element: HTMLButtonElement | null) => {
    actionsButton.current = element;
    if (element && refocusActions.current) {
      refocusActions.current = false;
      element.focus();
    }
  }, []);

  function pick(action: RowAction) {
    setError(null);
    if (action === "trash" || action === "forever") {
      setConfirming(action);
      return;
    }
    if (action === "version") {
      events.onUploadVersion(document);
      return;
    }
    if (action === "restore") {
      void act(restoreDocument, restoredMessage(document.display_name), "Could not restore this document.");
      return;
    }
    afterEdit.current = "actions";
    setEditing(action);
  }

  function saved(message: string, moved = false) {
    afterEdit.current = moved ? "list" : "actions";
    setEditing(null);
    events.onSaved(message);
  }

  // The dialog is shut: hand focus back to Actions, or to the list after a move.
  function dialogClosed() {
    if (afterEdit.current === "list") events.onFocusList();
    else actionsButton.current?.focus();
  }

  // Move to Trash, Restore or Delete forever: the row leaves this list when it works.
  async function act(call: (id: string) => Promise<unknown>, message: string, failed: string) {
    setBusy(true);
    setError(null);
    try {
      await call(document.id);
      events.onRemoved(message);
    } catch (caught) {
      if (lostTheRight(caught)) refresh();
      setError(
        caught instanceof ApiError ? caught.message : `${failed} Check your connection, then try again.`,
      );
      setBusy(false);
      refocusActions.current = true;
      setConfirming(null);
    }
  }

  function confirm() {
    if (confirming === "forever") {
      // A granted user's password is checked first: if it is refused, nothing is deleted.
      const typed = askPassword ? password : null;
      setPassword("");
      void act(
        async (id) => {
          if (typed !== null) await confirmPassword(typed);
          await deleteDocumentForever(id);
        },
        deletedForeverMessage(document.display_name),
        "Could not delete this document.",
      );
    } else {
      void act(
        deleteDocument,
        deletedMessage(document.display_name),
        "Could not move this document to Trash.",
      );
    }
  }

  function cancel() {
    refocusActions.current = true;
    setConfirming(null);
  }

  const by = document.uploaded_by_name;
  const when = whenOf(document, inTrash);
  const question = confirming === "forever" ? foreverQuestion(document) : trashQuestion(document);
  return (
    <tr role="row" data-selected={selected ? "true" : undefined}>
      <td
        role="cell"
        className={styles.selectCell}
        data-empty={deletable ? undefined : "true"}
      >
        {deletable && (
          <label className={styles.selectHit}>
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggle(document.id)}
              aria-label={`Select ${document.display_name}`}
              title={`Select ${document.display_name}`}
            />
          </label>
        )}
      </td>
      <td role="cell" className={styles.nameCell}>
        <Link
          href={`/documents/${document.id}`}
          className={styles.name}
          title={document.display_name}
          onClick={rememberListScroll}
        >
          <WrapName name={document.display_name} />
        </Link>
        {document.tags.length > 0 && (
          <ul className={`${library.tags} ${library.rowTags}`} aria-label="Tags">
            {document.tags.map((tag) => (
              <li key={tag.id} className={library.tag}>
                {tag.name}
              </li>
            ))}
          </ul>
        )}
        {document.newer_version && (
          <Link href={`/documents/${document.newer_version.id}`} className={styles.newerVersion}>
            {newerVersionText(document.newer_version)}
          </Link>
        )}
      </td>
      <td role="cell" className={styles.statusCell}>
        <span className={styles.statusContent}>
          <StatusChip status={document.status} />
          <span className={styles.statusMessage}>{document.status_message}</span>
          <TimeLeft document={document} />
          {!inTrash &&
            attentionWords(document).map((words) => (
              <span key={words} className={styles.attentionChip}>
                <Icon name="eye-alert" size={16} />
                {words}
              </span>
            ))}
        </span>
      </td>
      <td role="cell" className={styles.meta}>
        {typeLabel(document.detected_type)}
        <span className={styles.docType}>{docTypeLabel(document.doc_type)}</span>
        <span className={`${styles.bandSize} tabular`}>{formatBytes(document.size_bytes)}</span>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.sizeCell} tabular`}>
        {formatBytes(document.size_bytes)}
      </td>
      <td role="cell" className={styles.meta}>
        {inTrash && <span className="visually-hidden">Trashed </span>}
        {/* The exact date too: a touch screen never shows the hover title. */}
        <time dateTime={when} title={formatDateTime(when)}>
          {whenText}
        </time>
        {by && (
          <span className={styles.bandBy}>
            Uploaded by <WrapName name={by} />
          </span>
        )}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.byCell}`} data-empty={by ? undefined : "true"}>
        {by ? (
          <>
            <span className={styles.byLabel}>Uploaded by </span>
            <WrapName name={by} />
          </>
        ) : (
          <>
            <span aria-hidden="true">—</span>
            <span className="visually-hidden">Not known</span>
          </>
        )}
      </td>
      <td role="cell" className={styles.actionsCell}>
        {confirming === null && !busy && (
          <RowMenu
            item={document}
            canForever={canForever}
            onPick={pick}
            toggleRef={holdActions}
          />
        )}
        {confirming === null && busy && (
          <p className={`muted ${styles.small}`} role="status">
            <span className="spinner" aria-hidden="true" /> Restoring…
          </p>
        )}
        {confirming !== null && (
          <div className={styles.confirm} role="group" aria-labelledby={`confirm-${document.id}`}>
            <span id={`confirm-${document.id}`}>{question}</span>
            {confirming === "forever" && askPassword && (
              <PasswordField
                id={`forever-password-${document.id}`}
                label="Your password"
                autoComplete="current-password"
                value={password}
                onChange={setPassword}
              />
            )}
            <div className={styles.confirmButtons}>
              <button
                type="button"
                className="btn btn-danger"
                onClick={confirm}
                disabled={busy || (confirming === "forever" && askPassword && password === "")}
              >
                <BusyLabel
                  busy={busy}
                  idle={confirming === "forever" ? "Delete forever" : "Move to Trash"}
                  working={confirming === "forever" ? "Deleting…" : "Moving…"}
                />
              </button>
              <button
                ref={focusOnMount}
                type="button"
                className="btn"
                onClick={cancel}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {error && (
          <p className={`error-text ${styles.rowError}`} role="alert">
            {error}
          </p>
        )}
        {editing && (
          <EditDialog
            action={editing}
            item={document}
            tree={tree}
            tags={tags}
            onClose={() => setEditing(null)}
            onClosed={dialogClosed}
            onSaved={saved}
            onTagCreated={events.onTagCreated}
          />
        )}
      </td>
    </tr>
  );
});
