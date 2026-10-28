"use client";

// The list of this customer's documents: one table, reflowed into cards only when
// the list is narrower than 36rem (Documents.module.css; UI audit D3, 16 Oct 2026).
// Names, tags and status messages are shown as plain text. The whole row opens the
// document (its name is a stretched link, D11); each row has a menu button
// (DocumentActions.tsx). The select-all box heads the select column (D9); the bar
// with the selection's actions shows only while rows are ticked, stuck to the bottom
// of the list's view, so it never pushes a row (on cards it also holds Select all).
// Move to Trash, and Delete forever in Trash, ask
// first, in a dialog as on the document screen (issue 408: the question opened over the row's
// own columns and the next row); focus goes to Cancel, and back to the menu button. Restore
// needs no question: nothing is lost. Ticked rows can be moved to Trash
// together; in Trash they can be restored together, or deleted forever together
// by whoever may (canDeleteForever, asked first; a granted member or reviewer
// types their password in that step). In Trash the list shows when each
// document was trashed. Each row is the family's
// current version; a newer one still being read, or one that failed, gets a
// line under the name ("Version 3: ..."), linking to it. The Type column
// gives the file type, and under it what kind of document it is ("Invoice"), once the AI
// model's card has said (PRD 2, D6); with no card yet (the model off, down, or still reading)
// that line is left out, never "Unknown" on every row (issue 371). Names wrap in full at
// their _ . - and @ marks (WrapName.tsx). The By cell says "Uploaded by" in
// words; the full table hides those words under its By header. A document with
// reading problems says so in its status ("Scan complete" then "12 flagged · Review",
// the link read out as "Review 12 flagged words in ..."), and a reviewer or an admin can have ticked documents
// read again, one at a time, after an inline question.
// Move to folder (16 Oct 2026): every row out of Trash can be ticked, and the bar's Move to folder
// opens the same Move dialog as a row's menu (MoveDialog) for the ticked rows.
// Scan states (16 Oct 2026): the status cell is two fixed lines while a document
// is queued, being read, or read, so a scan finishing never changes its row's height;
// a row keeps its place (the list is newest first, which a state never changes), and
// while the pointer is on the list a row not shown yet waits until it leaves
// (rowsInPlace), so nothing is put in above the pointer.

import Link from "next/link";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent, RefCallback } from "react";
import { keycloakStartPath } from "@/app/login/signIn";
import { extensionOf } from "@/components/dashboard/words";
import { LabelChip } from "@/components/files/LabelChip";
import { FILES_TEXT, NEWER_SPOKEN, versionCell } from "@/components/files/words";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { beingRead, canReprocess, reprocessErrorText } from "@/components/viewer/reprocess";
import { newerVersionText } from "@/components/viewer/versions";
import { FADE_IN, MOTION_MS, play } from "@/lib/motion";
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
import { ActionDialog } from "./ActionDialog";
import { EditDialog, MoveDialog, RowMenu } from "./DocumentActions";
import { DOCUMENT_DRAG } from "./FolderNav";
import { pathLabel } from "./folderTree";
import type { FolderTree } from "./folderTree";
import { BusyLabel } from "./BusyLabel";
import { movableItems } from "./library";
import type { EditAction, RowAction } from "./library";
import type { SortKey, SortOrder } from "./libraryQuery";
import { rowsInPlace } from "./listMerge";
import {
  UNDERSTANDING_TEXT,
  deletedMessage,
  flaggedShort,
  formatProgressPercent,
  scanWords,
  shortWhen,
  toReview,
} from "./logic";
import { StatusChip } from "./StatusChip";
import { TimeLeft } from "./TimeLeft";
import { WrapName } from "./WrapName";
import {
  type BulkAction,
  type RemovalUndo,
  bulkDoneMessage,
  bulkFailedMessage,
  bulkForeverQuestion,
  bulkReadQuestion,
  bulkTrashQuestion,
  canDeleteForever,
  deletedForeverMessage,
  foreverConfirmsWithKeycloak,
  foreverNeedsPassword,
  foreverQuestion,
  lostTheRight,
  restoredMessage,
  runForever,
  selectable,
  trashQuestion,
  undoRemoval,
} from "./trash";
import styles from "./Documents.module.css";
import library from "./Library.module.css";

/** What a row reports back to the screen. */
export interface RowEvents {
  /** Moved to Trash, restored or deleted for good: say it and ask for the list again; a move
   * to Trash or a restore can be undone (issue 372). */
  onRemoved(message: string, undo?: RemovalUndo): void;
  /** Renamed, moved or tagged: announce it and ask for the list again; a move can be undone. */
  onSaved(message: string, undo?: () => Promise<void>): void;
  /** After a move the row may be gone: focus the list's heading. */
  onFocusList(): void;
  onTagCreated(): void;
  /** A folder was made in the Move dialog: ask for the folders again. */
  onFolderCreated(): void;
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
  /** Names for a new folder (folderNameSuggestions). */
  suggestions: readonly string[];
  /** A moved document leaves this list (a folder is shown), so focus goes to the list. */
  movedLeaves: boolean;
  /** Q4: the list's sort and what a header press does; left out, the headers are words only. */
  sorting?: Sorting;
}

/** The list's sort (Q4) and the header press that changes it. */
export interface Sorting {
  sort: SortKey;
  order: SortOrder;
  onSort(key: SortKey): void;
}

/** A column header that sorts (Q4): a button, with aria-sort on the header while it is the sort. */
function SortHeader({
  className,
  column,
  label,
  sorting,
}: {
  className: string | undefined;
  column: SortKey;
  label: string;
  sorting: Sorting | undefined;
}) {
  const active = sorting?.sort === column;
  const ascending = sorting?.order === "asc";
  return (
    <th
      scope="col"
      role="columnheader"
      className={className}
      aria-sort={active ? (ascending ? "ascending" : "descending") : undefined}
    >
      {sorting ? (
        <button
          type="button"
          className={styles.sortButton}
          title={FILES_TEXT.sortBy(label)}
          onClick={() => sorting.onSort(column)}
        >
          {label}
          {active && (
            <span className={styles.sortMark} aria-hidden="true">
              {ascending ? "↑" : "↓"}
            </span>
          )}
        </button>
      ) : (
        label
      )}
    </th>
  );
}

export function DocumentsTable({
  items: listed,
  inTrash,
  me,
  now,
  tree,
  tags,
  suggestions,
  movedLeaves,
  sorting,
  ...events
}: Props) {
  const { onRemoved } = events;
  // The rows shown when the pointer came onto the list; null while it is elsewhere.
  const [pinned, setPinned] = useState<ReadonlySet<string> | null>(null);
  const items = useMemo(() => rowsInPlace(listed, pinned), [listed, pinned]);
  // A row that arrives (an upload, a poll) fades in where it sits; the first rows do not.
  const body = useRef<HTMLTableSectionElement>(null);
  const seenIds = useRef<ReadonlySet<string> | null>(null);
  useLayoutEffect(() => {
    const before = seenIds.current;
    seenIds.current = new Set(items.map((item) => item.id));
    if (before === null) return;
    for (const row of body.current?.querySelectorAll<HTMLElement>("tr[data-id]") ?? []) {
      if (!before.has(row.dataset.id ?? "")) play(row, FADE_IN, MOTION_MS.med);
    }
  }, [items]);
  const { refresh } = useSession();
  const canForever = canDeleteForever(me);
  const askPassword = foreverNeedsPassword(me);
  const askKeycloak = foreverConfirmsWithKeycloak(me);
  // Read again (PRD 5 U2): a reviewer or an admin, for a document that can be read again.
  const readable = useCallback(
    (item: DocumentSummary) => !inTrash && canReprocess(me.user.role, item),
    [inTrash, me.user.role],
  );
  // A row out of Trash can always be ticked: anyone may move it to a folder.
  const tickable = useCallback(
    (item: DocumentSummary) => !inTrash || selectable(item, inTrash, canForever),
    [inTrash, canForever],
  );
  const deletableItems = useMemo(() => items.filter(tickable), [items, tickable]);
  const [moving, setMoving] = useState(false);
  const bulkMoved = useRef(false);
  const [password, setPassword] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  // The selection's action waiting for its "now" button: Move to Trash or Delete forever.
  const [confirming, setConfirming] = useState<BulkAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<BulkAction | null>(null);
  const [deleteProgress, setDeleteProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Two select-all boxes: the table's header, and the bar's on cards; one shows at a time.
  const selectAll = useRef<HTMLInputElement[]>([]);
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
  const movable = movableItems(items, visibleSelectedIds);
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
      onRemoved: (message, undo) => latestEvents.current.onRemoved(message, undo),
      onSaved: (message, undo) => latestEvents.current.onSaved(message, undo),
      onFocusList: () => latestEvents.current.onFocusList(),
      onTagCreated: () => latestEvents.current.onTagCreated(),
      onFolderCreated: () => latestEvents.current.onFolderCreated(),
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
      else onRemoved(bulkDoneMessage(action, result.done.length), undoRemoval(action, result.done));
    }
    // The buttons pressed are gone now: keep keyboard focus on the select-all box shown.
    selectAll.current.find((input) => input.isConnected && input.offsetParent !== null)?.focus();
  }

  const allRef: RefCallback<HTMLInputElement> = (input) => {
    if (!input) return;
    input.indeterminate = partiallySelected;
    if (!selectAll.current.includes(input)) selectAll.current = [...selectAll.current.filter((i) => i.isConnected), input];
  };
  const allBox = (label?: string) => (
    <input
      type="checkbox"
      checked={allSelected}
      aria-checked={partiallySelected ? "mixed" : allSelected}
      aria-label={label}
      title={label}
      onChange={toggleAll}
      ref={allRef}
    />
  );
  const idle = selectedCount === 0 && !confirming && !busy && !error;

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
      <table
        className={`table ${styles.docs}`}
        role="table"
        onPointerEnter={() => setPinned(new Set(listed.map((item) => item.id)))}
        onPointerLeave={() => setPinned(null)}
      >
        <caption className="visually-hidden">{inTrash ? "Documents in Trash" : "Your documents"}</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" className={styles.selectColumn}>
              {deletableItems.length > 0 ? (
                <label className={styles.selectHit}>{allBox("Select all visible documents")}</label>
              ) : (
                <span className="visually-hidden">Select</span>
              )}
            </th>
            {/* File management F5, Q4 (Changed on purpose, PRD 1 screens): every column with a sort key sorts. */}
            <SortHeader className={styles.nameColumn} column="name" label={FILES_TEXT.columns.name} sorting={sorting} />
            <th scope="col" role="columnheader" className={styles.statusColumn}>Status</th>
            <SortHeader className={styles.typeColumn} column="doc_type" label={FILES_TEXT.columns.type} sorting={sorting} />
            <SortHeader className={styles.ownerColumn} column="owner" label={FILES_TEXT.columns.owner} sorting={sorting} />
            <SortHeader className={styles.labelColumn} column="sensitivity" label={FILES_TEXT.columns.label} sorting={sorting} />
            <SortHeader className={styles.versionColumn} column="version" label={FILES_TEXT.columns.version} sorting={sorting} />
            <SortHeader
              className={styles.docDateColumn}
              column="document_date"
              label={FILES_TEXT.columns.documentDate}
              sorting={sorting}
            />
            <SortHeader
              className={styles.uploadedColumn}
              column="updated_at"
              label={inTrash ? "Trashed" : FILES_TEXT.columns.updatedAt}
              sorting={inTrash ? undefined : sorting}
            />
            <th scope="col" role="columnheader" className={styles.byColumn}>{FILES_TEXT.columns.updatedBy}</th>
            <th scope="col" role="columnheader" className={styles.actionsColumn}>
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody ref={body} role="rowgroup">
          {items.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              whenShort={shortWhen(whenOf(document, inTrash), now)}
              whenSpoken={whenSpoken(document, inTrash, now)}
              inTrash={inTrash}
              deletable={tickable(document)}
              readable={readable(document)}
              canForever={canForever}
              askPassword={askPassword}
              askKeycloak={askKeycloak}
              selected={selectedIds.has(document.id)}
              onToggle={toggle}
              tree={tree}
              tags={tags}
              suggestions={suggestions}
              movedLeaves={movedLeaves}
              events={rowEvents}
            />
          ))}
        </tbody>
      </table>
      {deletableItems.length > 0 && (
        <div
          className={styles.selectionBar}
          role="toolbar"
          aria-label="Document selection actions"
          data-idle={idle ? "true" : undefined}
        >
          <label className={styles.selectAll}>
            {allBox()}
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
                  {movable.length > 0 && (
                    <button type="button" className="btn" onClick={() => setMoving(true)}>
                      Move to folder ({movable.length})
                    </button>
                  )}
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
              aria-describedby="bulk-confirm-question"
            >
              <span id="bulk-confirm-question">
                {confirming === "forever"
                  ? bulkForeverQuestion(selectedCount)
                  : confirming === "read"
                    ? bulkReadQuestion(readableIds.length)
                    : bulkTrashQuestion(restorableIds.length)}
              </span>
              {confirming === "forever" && askKeycloak && <KeycloakConfirm />}
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
      {moving && (
        <MoveDialog
          items={movable}
          tree={tree}
          suggestions={suggestions}
          onClose={() => setMoving(false)}
          onClosed={() => {
            // Cancelled: focus goes back to Move to folder. Moved: the bar's buttons are gone.
            if (!bulkMoved.current) return;
            bulkMoved.current = false;
            const box = selectAll.current.find((input) => input.isConnected && input.offsetParent !== null);
            if (box) box.focus();
            else events.onFocusList();
          }}
          onSaved={(message, _moved, undo) => {
            bulkMoved.current = true;
            setMoving(false);
            clearSelection();
            events.onSaved(message, undo);
          }}
          onFolderCreated={events.onFolderCreated}
        />
      )}
    </>
  );
}

// One call per document for each selection action; the server checks every rule again.
/** The list's scroll as a document opens, so Back can return to the same row (DocumentsView). */
export const listScrollKey = () => `docs-y:${location.search}`;
function rememberListScroll() {
  try {
    // The app shell's <main> is the one scroller (MonoEdge design system); the window on other pages.
    const main = document.getElementById("main");
    sessionStorage.setItem(listScrollKey(), String(Math.round(main ? main.scrollTop : scrollY)));
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
/** A member allowed to delete forever, signed in through Keycloak (#435): in place of their
 * password, they confirm it is them on Keycloak's page, which brings them back here; then
 * Delete forever works for 5 minutes. Shown only in an open dialog, so the address is known. */
function KeycloakConfirm() {
  const here = `${window.location.pathname}${window.location.search}`;
  return (
    <p className="hint">
      Confirm it is you first, then delete within 5 minutes.{" "}
      <a className="btn" href={keycloakStartPath(here, true)}>
        Confirm with Keycloak
      </a>
    </p>
  );
}

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

// In Trash the time column is when it was trashed; elsewhere, its last change (File management
// O3: the newest upload or details change), or when it was uploaded on an older reply.
const whenOf = (document: DocumentSummary, inTrash: boolean) =>
  inTrash && document.deleted_at !== null ? document.deleted_at : (document.updated_at ?? document.created_at);

// Worked out here, so a row renders again only when its own words change, not on every poll.
// Shown "2h ago" (shortWhen); read out "2 hours ago, 16 Oct 2026, 2:05 pm".
function whenSpoken(document: DocumentSummary, inTrash: boolean, now: number): string {
  const when = whenOf(document, inTrash);
  const relative = formatRelative(when, now);
  return relative === formatDate(when) ? formatDateTime(when) : `${relative}, ${formatDateTime(when)}`;
}

const DocumentRow = memo(function DocumentRow({
  document,
  whenShort,
  whenSpoken,
  inTrash,
  deletable,
  readable,
  canForever,
  askPassword,
  askKeycloak,
  selected,
  onToggle,
  tree,
  tags,
  suggestions,
  movedLeaves,
  events,
}: {
  document: DocumentSummary;
  whenShort: string;
  whenSpoken: string;
  inTrash: boolean;
  deletable: boolean; // it gets a checkbox (selectable)
  readable: boolean; // it may be read again (canReprocess): its status cell may offer Retry
  canForever: boolean;
  askPassword: boolean;
  askKeycloak: boolean; // confirms through Keycloak instead (#435)
  selected: boolean;
  onToggle: (id: string) => void;
  tree: FolderTree;
  tags: Tag[] | null;
  suggestions: readonly string[];
  movedLeaves: boolean;
  events: RowEvents;
}) {
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [editing, setEditing] = useState<EditAction | null>(null);
  const [busy, setBusy] = useState(false);
  // What the row says while Restore or Retry runs (Trash and Delete forever say it in their dialog).
  const [working, setWorking] = useState(BUSY_WORDS.restore);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const { refresh } = useSession();
  const actionsButton = useRef<HTMLButtonElement | null>(null);
  const refocusActions = useRef(false);
  const afterEdit = useRef<"actions" | "list">("actions");

  // A stable callback ref, so it runs once when its button appears.
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
      setWorking(BUSY_WORDS.restore);
      void act(
        restoreDocument,
        restoredMessage(document.display_name),
        "Could not restore this document.",
        undoRemoval("restore", [document.id]),
      );
      return;
    }
    if (action === "read") return; // only the document screen's menu offers it
    afterEdit.current = "actions";
    setEditing(action);
  }

  function saved(message: string, moved = false, undo?: () => Promise<void>) {
    afterEdit.current = moved && movedLeaves ? "list" : "actions";
    setEditing(null);
    events.onSaved(message, undo);
  }

  // The dialog is shut: hand focus back to Actions, or to the list after a move.
  function dialogClosed() {
    if (afterEdit.current === "list") events.onFocusList();
    else actionsButton.current?.focus();
  }

  // Move to Trash, Restore or Delete forever: the row leaves this list when it works.
  async function act(call: (id: string) => Promise<unknown>, message: string, failed: string, undo?: RemovalUndo) {
    setBusy(true);
    setError(null);
    try {
      await call(document.id);
      events.onRemoved(message, undo);
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
        undoRemoval("trash", [document.id]),
      );
    }
  }

  // The dialog hands focus back to the menu button once it is shut (onClosed).
  function cancel() {
    setConfirming(null);
  }

  // Issue 407: the status cell's Retry is Read again (POST .../reprocess); the row stays and
  // the list is asked for again.
  async function retry() {
    setWorking(BUSY_WORDS.read);
    setBusy(true);
    setError(null);
    try {
      await reprocessDocument(document.id);
      events.onSaved(bulkDoneMessage("read", 1));
    } catch (caught) {
      if (lostTheRight(caught)) refresh();
      setError(reprocessErrorText(caught));
    }
    setBusy(false);
  }

  // O3: who changed it last (the newest upload or details change); the uploader on an older reply.
  const by = document.updated_by ?? document.uploaded_by_name;
  const when = whenOf(document, inTrash);
  // F5: "v1 · newer?" while a newer version of this family waits to be confirmed.
  const newer = document.pending_suggestion?.role === "candidate";
  const owner = document.owner?.name ?? null;
  const version = (
    <>
      {versionCell(document.version_no, false)}
      {newer && (
        <span className={styles.newer}>
          <span aria-hidden="true"> · newer?</span>
          <span className="visually-hidden">{`, ${NEWER_SPOKEN}`}</span>
        </span>
      )}
    </>
  );
  const dash = (
    <>
      <span aria-hidden="true">—</span>
      <span className="visually-hidden">Not known</span>
    </>
  );
  // Vrushit UAT (25 Oct 2026): the tags sit with the label, in its column once it shows.
  const tagList = (className = "") =>
    document.tags.length > 0 && (
      <ul className={`${library.tags} ${library.rowTags} ${className}`} aria-label="Tags">
        {document.tags.map((tag) => (
          <li key={tag.id} className={library.tag}>
            {tag.name}
          </li>
        ))}
      </ul>
    );
  // D11: the whole row opens the document. A click handler, not a stretched link: an
  // absolutely placed ::after stays in its row only where the browser makes a <tr> its
  // containing block, and some (Safari) do not, so it covered the page and any click
  // opened one file (QA video, 17 Oct 2026). Controls, modifier clicks and text
  // selection keep their own behaviour; the name stays a real link for keys and new tabs.
  function openRow(event: ReactMouseEvent<HTMLTableRowElement>) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    // A row's dialogs sit inside it: a click on their words or backdrop never opens the document.
    if ((event.target as Element).closest("a, button, input, label, select, textarea, dialog, [role='menu'], [role='dialog']")) return;
    if (window.getSelection()?.toString()) return;
    // The name's own link: its navigation and its scroll memory, nothing new.
    event.currentTarget.querySelector<HTMLAnchorElement>(`.${styles.name}`)?.click();
  }
  const question = confirming === "forever" ? foreverQuestion(document) : trashQuestion(document);
  return (
    <tr role="row" data-id={document.id} data-selected={selected ? "true" : undefined} onClick={openRow}>
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
          onDragStart={(event) => {
            if (inTrash) return;
            event.dataTransfer.setData(DOCUMENT_DRAG, document.id);
            event.dataTransfer.effectAllowed = "copyMove";
          }}
        >
          <span className={`k-file-ic ${styles.fileIcon}`} data-ext={extensionOf(document.display_name)} aria-hidden="true" />
          <span>
            <WrapName name={document.display_name} />
          </span>
        </Link>
        <span className={styles.bandType}>
          {typeLabel(document.detected_type)}
          {document.doc_type && ` · ${docTypeLabel(document.doc_type)}`} · {formatBytes(document.size_bytes)}
        </span>
        {/* F5: the label, version, owner and date, each here until its own column has room. */}
        <span className={styles.facts}>
          {document.sensitivity && (
            <span className={styles.factLabel}>
              <LabelChip label={document.sensitivity} />
            </span>
          )}
          <span className={styles.factVersion}>{version}</span>
          {owner && (
            <span className={styles.factOwner}>
              {FILES_TEXT.columns.owner}: {owner}
            </span>
          )}
          {document.document_date && (
            <span className={styles.factDate}>{FILES_TEXT.dated(formatDate(document.document_date))}</span>
          )}
          {/* Where it is filed, so a move shows in All documents too (16 Oct 2026); on the facts'
              line, not a line of its own, so a row stays short (issue 371). */}
          {!inTrash && document.folder_id && tree.byId.has(document.folder_id) && (
            <span className={styles.inFolder}>In {pathLabel(tree, document.folder_id)}</span>
          )}
        </span>
        {tagList(styles.factTags)}
        {document.newer_version && (
          <Link href={`/documents/${document.newer_version.id}`} className={styles.newerVersion}>
            {newerVersionText(document.newer_version)}
          </Link>
        )}
      </td>
      <td role="cell" className={styles.statusCell}>
        <StatusCell
          document={document}
          inTrash={inTrash}
          onRetry={readable ? () => void retry() : undefined}
          busy={busy}
        />
      </td>
      <td role="cell" className={`${styles.meta} ${styles.typeCell}`}>
        {typeLabel(document.detected_type)}
        {document.doc_type && <span className={styles.docType}>{docTypeLabel(document.doc_type)}</span>}
        <span className={`${styles.bandSize} tabular`}>{formatBytes(document.size_bytes)}</span>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.ownerCell}`}>
        {owner ? <WrapName name={owner} /> : dash}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.labelCell}`}>
        {document.sensitivity ? <LabelChip label={document.sensitivity} /> : dash}
        {tagList()}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.versionCell} tabular`}>
        {version}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.docDateCell} tabular`}>
        {document.document_date ? formatDate(document.document_date) : dash}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.uploadedCell}`}>
        {inTrash && <span className="visually-hidden">Trashed </span>}
        {/* One short line, the exact date in its title; read out in full instead. */}
        <time dateTime={when} title={formatDateTime(when)} className={styles.when} aria-hidden="true">
          {whenShort}
        </time>
        <span className="visually-hidden">{whenSpoken}</span>
        {by && (
          <span className={styles.bandBy} title={by}>
            <span className="visually-hidden">Updated </span>by {by}
          </span>
        )}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.byCell}`} data-empty={by ? undefined : "true"}>
        {by ? (
          <>
            <span className={styles.byLabel}>Updated by </span>
            <span className={styles.byName} title={by}>
              <WrapName name={by} />
            </span>
          </>
        ) : (
          <>
            <span aria-hidden="true">—</span>
            <span className="visually-hidden">Not known</span>
          </>
        )}
      </td>
      <td role="cell" className={styles.actionsCell}>
        {confirming === null && busy ? (
          <p className={`muted ${styles.small}`} role="status">
            <span className="spinner" aria-hidden="true" /> {working}
          </p>
        ) : (
          <RowMenu
            item={document}
            canForever={canForever}
            onPick={pick}
            toggleRef={holdActions}
          />
        )}
        {confirming !== null && (
          <ActionDialog
            title={confirming === "forever" ? "Delete forever" : "Move to Trash"}
            onClose={cancel}
            onClosed={() => actionsButton.current?.focus()}
            canClose={() => !busy}
          >
            <div className={library.dialogForm}>
              <p>{question}</p>
              {confirming === "forever" && askKeycloak && <KeycloakConfirm />}
              {confirming === "forever" && askPassword && (
                <PasswordField
                  id={`forever-password-${document.id}`}
                  label="Your password"
                  autoComplete="current-password"
                  value={password}
                  onChange={setPassword}
                />
              )}
              <div className={library.formButtons}>
                <button
                  type="button"
                  className={`btn btn-danger ${library.apart}`}
                  onClick={confirm}
                  disabled={busy || (confirming === "forever" && askPassword && password === "")}
                >
                  <BusyLabel
                    busy={busy}
                    idle={confirming === "forever" ? "Delete forever" : "Move to Trash"}
                    working={confirming === "forever" ? "Deleting…" : "Moving…"}
                  />
                </button>
                <button type="button" className="btn" data-autofocus onClick={cancel} disabled={busy}>
                  Cancel
                </button>
              </div>
            </div>
          </ActionDialog>
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
            suggestions={suggestions}
            onFolderCreated={events.onFolderCreated}
          />
        )}
      </td>
    </tr>
  );
});

/**
 * The status cell. Queued, being read, and read: two lines of fixed height (the chip and
 * its words, then the bar and the time left, or what was flagged), keyed by phase so the
 * new lines fade in (opacity only) and nothing else moves. Any other state: the chip and
 * the server's words, which may wrap.
 * Issue 407: with `onRetry` (whoever may read it again), a failed row, or one still being read
 * 6 hours on (`stuck`), ends its second line with Retry; the server checks both again.
 */
export function StatusCell({
  document,
  inTrash,
  onRetry,
  busy = false,
}: {
  document: DocumentSummary;
  inTrash: boolean;
  onRetry?: () => void;
  busy?: boolean;
}) {
  const retry = onRetry && !inTrash && (document.status === "failed" || document.stuck === true) && (
    <button type="button" className={styles.statusAction} onClick={onRetry} disabled={busy}>
      Retry<span className="visually-hidden">{` ${document.display_name}`}</span>
    </button>
  );
  if (beingRead(document.status) && document.deleted_at === null) {
    const words = scanWords(document);
    const detail = words ? words.detail : document.status_message;
    return (
      <span className={styles.statusContent} key="scanning">
        <span className={styles.statusLine}>
          <StatusChip status={document.status} text={words ? words.chip : "Queued"} />
          <span className={styles.statusText} title={detail}>
            {detail}
          </span>
        </span>
        <span className={styles.statusLine}>
          <TimeLeft document={document} />
          {retry}
        </span>
      </span>
    );
  }
  if (document.status === "ready") {
    const flagged = flaggedShort(document);
    // UI audit D4 (16 Oct 2026): a clean read is its badge alone; the second line stays laid out.
    const clean = !toReview(document);
    return (
      <span className={styles.statusContent} key="done">
        <span className={styles.statusLine}>
          <StatusChip status="ready" text="Scan complete" />
        </span>
        <span className={styles.statusLine}>
          {document.understanding === true ? (
            <span className={styles.statusText} title={UNDERSTANDING_TEXT}>
              {UNDERSTANDING_TEXT}
            </span>
          ) : (
            !clean && (
              <span className={styles.statusText} title={flagged.shown}>
                {flagged.shown}
              </span>
            )
          )}
          {!inTrash && toReview(document) && (
            <Link href={`/documents/${document.id}`} className={styles.statusAction} onClick={rememberListScroll}>
              Review<span className="visually-hidden">{` ${flagged.spoken} in ${document.display_name}`}</span>
            </Link>
          )}
        </span>
      </span>
    );
  }
  return (
    <span className={styles.statusContent}>
      <span className={styles.statusLine}>
        <StatusChip status={document.status} />
      </span>
      <span className={styles.statusLine}>
        <span className={styles.statusText} title={document.status_message}>
          {document.status_message}
        </span>
        {retry}
      </span>
    </span>
  );
}
