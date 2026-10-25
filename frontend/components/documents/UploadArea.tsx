"use client";

// Uploading. Files come from the "Upload files" button (the hidden file input
// the documents screen owns) or are dropped on the dropzone. Each file is
// checked first (kind and size, against the limits from GET /upload-limits),
// then, just before its turn, a PDF's or TIFF's pages are counted (it stays
// "Waiting" meanwhile), then it is sent, 3 at a time in all (PRD 5 Q2), with its own
// progress bar, into the folder that was being viewed when they were added.
// Each drop is one batch (Q1): the browser makes its id, and the batch tray
// (U1, batchTray.ts) shows one line and one bar for it, then its files in the
// order they were dropped. The tray opens from the status bar (UploadStatus), in
// a fixed corner, so it never pushes the list. Each file's bar moves through
// Uploading, Saving and Scanning and stays under 100% until its document is ready (U10, uploadStages.ts): a stored file
// is followed through GET /api/batches/{id} (lib/poll.ts back-off) while it is
// read. After a reload the tray comes back for the drops of the last 24 hours;
// selecting the same file again resumes its saved parts, including on another device.
// A new version of a document ("Upload new version") goes through the same
// queue, with no batch. The outcome, including "already uploaded" and any error, stays next
// to its file until "Clear finished"; a duplicate whose earlier copy is in
// Trash offers Restore, and a file not sent offers Retry. A finished file also links to its story section in
// the viewer (a link, never a dialog: nothing waits on it). Each batch is
// summed up once when it settles (uploadQueue.ts); the documents screen says it.
// Each file is sent with who was signed in when it was added, so the server
// refuses it if another account has signed in since (critical #5); then
// no file still waiting is sent, and each says why.
// Auto-filing (feature 28): a file Kaagaz filed says "Filed into Invoices · Undo"
// (AutofileNote, also on the document screen); one it could not place says so.
// "Read with" (ReaderSelect, readers.ts) picks how the scans of the files added
// next are read (Dynamic, Standard or Careful; Ensemble is offered only after the
// scan, Tushar 16 Oct 2026), new versions included; it is kept for this visit
// only (module variables: a reload starts at the organisation's default again).

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { InStatusBar } from "@/components/shell/StatusBar";
import { VERSION_TEXT, suggestionsText } from "@/components/versions/words";
import { Icon } from "@/components/ui/Icon";
import { beingRead } from "@/components/viewer/reprocess";
import { storyHref, storyTarget } from "@/components/viewer/story";
import {
  ApiError,
  getBatch,
  getUploadLimits,
  restoreDocument,
  undoAutofile,
  uploadDocument,
  uploadVersion,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { advance, barValue } from "@/lib/progress";
import type { ShownProgress } from "@/lib/progress";
import { useSession } from "@/lib/session";
import { useTick } from "@/lib/tick";
import type { BatchView, DocumentSummary, UploadLimits, UploadReader, UploadResult } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import {
  MAX_FILES_PER_BATCH,
  NOT_FILED_TEXT,
  TOO_MANY_FILES,
  UNDONE_TEXT,
  batchFraction,
  batchStore,
  filedText,
  leftText,
  newBatchId,
  readStored,
  rowKind,
  statusBarText,
  summaryText,
  trayCounts,
  undoErrorText,
  unreadableWords,
  verifiedWords,
  writeStored,
} from "./batchTray";
import type { StoredBatch } from "./batchTray";
import { BusyLabel } from "./BusyLabel";
import { pathLabel } from "./folderTree";
import type { FolderTree } from "./folderTree";
import { ALL_DOCUMENTS, libraryHref } from "./libraryQuery";
import {
  FALLBACK_UPLOAD_LIMITS,
  estimateIngestSeconds,
  flaggedText,
  scanPhrase,
  sessionChangedMessage,
  toReview,
  uploadErrorText,
  uploadKind,
  uploadLimitsHint,
  uploadStateText,
  validateUpload,
} from "./logic";
import type { UploadState } from "./logic";
import { checkPages } from "./pageCount";
import { capReaders, effectiveOf, startReader, useAiSettings } from "@/lib/aiSettings";
import { DUPLICATE_READER_NOTE, offersChoice, pickedReader, readerOptions, readingToSend } from "./readers";
import { EMPTY_QUEUE, UPLOADS_AT_ONCE, askBatches, createSlots, followed, isBusy, queueReducer } from "./uploadQueue";
import type { Followed, UploadEntry } from "./uploadQueue";
import { NO_TIMES, jobFraction, nextShown, stageOf, valueText } from "./uploadStages";
import type { Shown, Stage, StageTimes } from "./uploadStages";
import { WrapName } from "./WrapName";
import styles from "./Documents.module.css";

type Start = (onProgress: (fraction: number) => void, onSent: () => void, onWaiting: (waiting: boolean) => void) => Promise<UploadResult>;

/** Not sent because of the connection or the server, not because the file was refused: it can be sent again. */
const canRetry = (caught: unknown) =>
  !(caught instanceof ApiError) || caught.status === 0 || caught.status === 429 || caught.status >= 500;

// The reading picked in "Read with" during this visit; null: the organisation's default.
let sessionReader: string | null = null;

/** Failed rounds in a row before the tray stops asking about a batch (about 50 s). */
const MAX_FAILED_ROUNDS = 10;

/** The upload queue: add files, watch them go, clear the finished ones. */
export function useUploads(onUploaded: () => void) {
  const { me } = useSession();
  const nextKey = useRef(0);
  // The server's words once another account has signed in here; then nothing more is sent.
  const stopped = useRef<string | null>(null);
  const [queue, dispatch] = useReducer(queueReducer, EMPTY_QUEUE);
  const [loadedLimits, setLimits] = useState<UploadLimits>(FALLBACK_UPLOAD_LIMITS);
  const [limitsLoaded, setLimitsLoaded] = useState(false); // D1: "Read with" holds its room until then
  const [reader, setReader] = useState<string | null>(sessionReader);
  // AI settings (spec 17 Oct 2026): "Read with" offers nothing above the effective scan
  // level and starts from it; nothing picked sends nothing, so the server reads at it.
  const scanLevel = effectiveOf(useAiSettings(), "scan");
  const limits = useMemo(
    () => ({ ...loadedLimits, readers: capReaders(loadedLimits.readers, scanLevel) }),
    [loadedLimits, scanLevel],
  );
  // The files this tab still holds, so a file not sent can be sent again (Retry).
  const kept = useRef(new Map<string, Start>());
  // The drops of the last 24 hours (batchTray.ts), and the batch API's latest view of each.
  const [stored, setStored] = useState<StoredBatch[]>(() => readStored(batchStore(), Date.now()));
  // Drops from before a reload: every file the batch API lists gets a row.
  const [restored] = useState(() => new Set(stored.map((batch) => batch.id)));
  const [views, setViews] = useState<Record<string, BatchView>>({});
  const [tooMany, setTooMany] = useState(false);
  const [slots] = useState(() => createSlots(UPLOADS_AT_ONCE)); // Q2: 3 files at a time in all

  function chooseReader(id: string) {
    sessionReader = id;
    setReader(id);
  }

  // Asked once. Until it answers, or if it fails, the platform's limits apply;
  // the server checks every file again either way.
  useEffect(() => {
    let cancelled = false;
    getUploadLimits().then(
      (loaded) => {
        if (cancelled) return;
        setLimits(loaded);
        setLimitsLoaded(true);
      },
      () => {
        if (!cancelled) setLimitsLoaded(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  function update(key: string, state: UploadState) {
    dispatch({ type: "change", key, change: { state } });
  }

  // The batches with a file still being read, and the drops from before a reload not seen yet.
  const following = useMemo(() => {
    const ids = new Set<string>();
    for (const entry of queue.entries) if (entry.batchId && entry.state.kind === "reading") ids.add(entry.batchId);
    for (const batch of stored) if (restored.has(batch.id) && !views[batch.id]) ids.add(batch.id);
    return [...ids];
  }, [queue.entries, stored, views, restored]);

  // A batch that keeps failing (not a lost connection: the poller waits those out while
  // offline) is asked at most this many rounds in a row, not every few seconds for ever.
  const failedRounds = useRef(0);
  const asked = useRef(new Map<string, number>()); // the round each batch was last asked in
  const poll = usePoll<Followed>({
    load: () => askBatches(following, asked.current, getBatch), // one request a round, however many drops
    onResult: ({ views: answers, gone, failed }) => {
      failedRounds.current = failed ? failedRounds.current + 1 : 0;
      for (const view of answers) {
        dispatch({ type: "batch", batchId: view.id, documents: view.documents, add: restored.has(view.id) });
      }
      setViews((shown) => ({ ...shown, ...Object.fromEntries(answers.map((view) => [view.id, view])) }));
      if (gone.length > 0) setStored((batches) => batches.filter((batch) => !gone.includes(batch.id) || !restored.has(batch.id)));
    },
    onFailure: () => undefined, // it keeps trying; the rows show what they have
    again: (round) =>
      round.skipped ||
      (round.failed && failedRounds.current < MAX_FAILED_ROUNDS) ||
      round.views.some((view) => view.documents.some((document) => beingRead(document.status))),
    signature: (round) => JSON.stringify(round.views.map((view) => [view.id, progressSignature(view.documents)])),
  });
  // Driven by the list, not by send(): the poll reads the task of the last render, so it
  // must ask only once a new batch to follow has rendered (a refresh asked for before
  // that would load the old list and stop).
  const followKey = following.join(",");
  useEffect(() => {
    if (followKey) poll.refresh();
  }, [poll, followKey]);

  // Remember each drop with the counts the batch API does not keep: already uploaded, not sent.
  useEffect(() => {
    const counted = stored.map((batch) => {
      const rows = queue.entries.filter((entry) => entry.batchId === batch.id);
      if (rows.length === 0) return batch;
      const already = rows.filter((entry) => rowKind(entry) === "already").length;
      const notSent = rows.filter((entry) => entry.state.kind === "error").length;
      return { ...batch, already, notSent };
    });
    writeStored(batchStore(), counted, Date.now());
  }, [stored, queue.entries]);

  /** Send it: sending, saving, then (follow) followed while it is read; or say why not. */
  async function send(key: string, start: Start, follow: boolean) {
    const times: StageTimes = { startedAt: null, sentAt: null, repliedAt: null };
    let fraction = 0;
    dispatch({ type: "change", key, change: { state: { kind: "sending", fraction: 0 }, times } });
    try {
      const result = await start(
        (progress) => {
          // Whole percents only: a browser reports about every 50 ms a file, and each report
          // redraws every screen that shows the queue.
          const step = Math.floor(progress * 100) !== Math.floor(fraction * 100);
          fraction = progress;
          if (progress > 0 && times.startedAt === null) times.startedAt = performance.now();
          if (step) update(key, { kind: "sending", fraction: progress });
        },
        () => {
          times.sentAt = performance.now();
          dispatch({ type: "change", key, change: { state: { kind: "saving" }, times: { ...times } } });
        },
        (waiting) => {
          if (!waiting) times.startedAt = fraction > 0 ? performance.now() : null;
          if (waiting) update(key, { kind: "offline", fraction });
          else if (times.sentAt !== null) update(key, { kind: "saving" });
          else update(key, { kind: "sending", fraction });
        },
      );
      times.repliedAt = performance.now();
      times.sentAt ??= times.repliedAt;
      kept.current.delete(key);
      const state: UploadState = result.duplicate
        ? {
            kind: "duplicate",
            documentId: result.document.id,
            existingName: result.document.display_name,
            inTrash: result.in_trash,
          }
        : follow
          ? followed(result.document)
          : { kind: "done", documentId: result.document.id };
      dispatch({ type: "change", key, change: { state, times: { ...times } } });
      onUploaded();
    } catch (caught) {
      stopped.current ??= sessionChangedMessage(caught);
      const retry = stopped.current === null && canRetry(caught) && kept.current.has(key);
      update(key, { kind: "error", message: uploadErrorText(caught, navigator.onLine), retry });
    }
  }

  /** Count a PDF's or TIFF's pages (kept for the time estimate), then send it, unless it has too many. */
  async function checkThenSend(key: string, file: File, start: Start, follow: boolean) {
    if (notSent(key)) return;
    const { pages, problem } = await checkPages(file, limits.max_pages);
    dispatch({ type: "change", key, change: { pages } });
    if (problem) update(key, { kind: "error", message: problem });
    else if (!notSent(key)) await send(key, start, follow);
  }

  /** True, and the file says why, once another account has signed in here. */
  function notSent(key: string): boolean {
    if (stopped.current === null) return false;
    update(key, { kind: "error", message: stopped.current });
    return true;
  }

  /** Send a file that was not sent once more, while this tab still holds it; only a batch's file is followed. */
  async function retry(key: string) {
    const start = kept.current.get(key);
    const follow = queue.entries.find((entry) => entry.key === key)?.batchId !== undefined;
    if (start && !notSent(key)) await slots(() => send(key, start, follow));
  }

  /**
   * Queue one drop of files for upload into folderId (undefined: the top level): one
   * batch of at most MAX_FILES_PER_BATCH files; the rest are refused and the tray says so.
   * departmentId (File management F2): the department picked, used when no folder is named.
   */
  async function addFiles(files: File[], folderId?: string, departmentId?: string) {
    const userId = me.user.id;
    const reading = readingToSend(limits.readers, reader);
    const batchId = newBatchId();
    setTooMany(files.length > MAX_FILES_PER_BATCH);
    const added = files.slice(0, MAX_FILES_PER_BATCH).map((file) => {
      nextKey.current += 1;
      const problem = validateUpload(file, limits);
      const entry: UploadEntry = {
        key: `upload-${nextKey.current}`,
        name: file.name,
        size: file.size,
        reading,
        batchId,
        state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
      };
      const start: Start = (onProgress, onSent, onWaiting) =>
        uploadDocument(file, userId, onProgress, folderId, reading, batchId, onSent, departmentId, onWaiting);
      return { entry, file, start };
    });
    dispatch({ type: "add", entries: added.map((item) => item.entry) });
    setStored((batches) => [{ id: batchId, at: Date.now(), already: 0, notSent: 0 }, ...batches]);
    const sending = added.filter((item) => item.entry.state.kind === "waiting");
    for (const { entry, start } of sending) kept.current.set(entry.key, start);
    // A few at a time across every drop, so many drops do not multiply them.
    await Promise.all(sending.map(({ entry, file, start }) => slots(() => checkThenSend(entry.key, file, start, true))));
  }

  /** Queue one file as the next version of a document, with its note (V9, optional). It has
   * no batch: its row ends at the reply. */
  async function addVersion(file: File, of: { id: string; display_name: string }, note?: string) {
    const userId = me.user.id;
    const reading = readingToSend(limits.readers, reader);
    nextKey.current += 1;
    const key = `upload-${nextKey.current}`;
    const problem = validateUpload(file, limits);
    dispatch({
      type: "add",
      entries: [
        {
          key,
          name: file.name,
          size: file.size,
          versionOf: of.display_name,
          reading,
          state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
        },
      ],
    });
    if (!problem) {
      const start: Start = (onProgress, onSent, onWaiting) =>
        uploadVersion(of.id, file, userId, onProgress, reading, onSent, note, onWaiting);
      kept.current.set(key, start);
      await slots(() => checkThenSend(key, file, start, false));
    }
  }

  /** Bring back the copy in Trash that a duplicate upload matched. */
  async function restore(key: string, documentId: string, existingName: string) {
    update(key, { kind: "restoring", documentId, existingName });
    try {
      await restoreDocument(documentId);
      update(key, { kind: "restored", documentId, existingName });
      onUploaded();
    } catch (caught) {
      const restoreError =
        caught instanceof ApiError
          ? caught.message
          : "Could not restore it. Check your connection, then try again.";
      update(key, { kind: "duplicate", documentId, existingName, inTrash: true, restoreError });
    }
  }

  /** The finished rows go, and so do the drops with nothing left to follow. */
  function clearFinished() {
    const busy = new Set(queue.entries.filter(isBusy).map((entry) => entry.batchId));
    dispatch({ type: "clear" });
    setStored((batches) => batches.filter((batch) => busy.has(batch.id)));
    setTooMany(false);
  }

  const { entries, finished, settled } = queue;
  return {
    entries,
    finished,
    settled,
    limits,
    limitsLoaded,
    reader: reader ?? startReader(limits.readers, scanLevel),
    views,
    stored,
    tooMany,
    chooseReader,
    addFiles,
    addVersion,
    restore,
    retry,
    clearFinished,
  };
}

/**
 * Auto-filing (feature 28): "Filed into Invoices · Undo" (``filed``, linked to the folder
 * when ``folderHref`` is given), then after Undo "Moved back..."; "Not filed..." for a file
 * Kaagaz could not place; nothing otherwise. Undo moves the whole family back to "Not in a
 * folder" and keeps the folder; a refusal (moved since, in Trash) is said in plain words.
 */
export function AutofileNote({
  document,
  name,
  filed,
  folderHref,
  onUndone,
}: {
  document: Pick<DocumentSummary, "id" | "autofile">;
  name: string;
  filed: string;
  folderHref: string | null;
  onUndone: (summary: DocumentSummary) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [undone, setUndone] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function undo() {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const summary = await undoAutofile(document.id);
      setUndone(true);
      onUndone(summary);
    } catch (caught) {
      setFailure(undoErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  if (undone) {
    return (
      <p className={styles.small} role="status">
        {UNDONE_TEXT}
      </p>
    );
  }
  if (document.autofile === "not_filed") return <p className={`muted ${styles.small}`}>{NOT_FILED_TEXT}</p>;
  if (document.autofile !== "filed") return null;
  return (
    <p className={styles.small}>
      {folderHref ? <Link href={folderHref}>{filed}</Link> : filed}
      {" · "}
      <button
        type="button"
        className={`btn btn-quiet ${styles.inlineAction}`}
        aria-disabled={busy || undefined}
        onClick={() => void undo()}
      >
        <BusyLabel busy={busy} idle="Undo" working="Undo" />
        <span className="visually-hidden">{` the filing of ${name}`}</span>
      </button>
      {failure && (
        <span className="error-text" role="alert">
          {" "}
          {failure}
        </span>
      )}
    </p>
  );
}

/**
 * "Read with": a labelled native select of the reading choices the server
 * offers ("Dynamic", "Standard", "Careful"), with the picked one's hint below
 * it. Not shown unless two or more can be used. Also the whole document's Read
 * again (ReadAgain.tsx).
 */
export function ReaderSelect({
  readers,
  picked,
  onPick,
}: {
  readers: readonly UploadReader[];
  picked: string | null;
  onPick: (id: string) => void;
}) {
  const id = useId();
  if (!offersChoice(readers)) return null;
  const current = pickedReader(readers, picked);
  return (
    <div className={styles.reader}>
      <label htmlFor={`${id}-select`} className={styles.readerLabel}>
        Read with
      </label>
      <select
        id={`${id}-select`}
        className={`input ${styles.readerSelect}`}
        value={current?.id}
        aria-describedby={`${id}-hint`}
        onChange={(event) => onPick(event.target.value)}
      >
        {readerOptions(readers).map((option) => (
          <option key={option.id} value={option.id} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
      <p id={`${id}-hint`} className={styles.readerHint} title={current?.hint}>
        {current?.hint}
      </p>
    </div>
  );
}

/**
 * Where files can be dropped. With no documents yet it is a full dashed card
 * holding the empty state. Once there are documents (UI audit D10, 16 Oct 2026) it
 * is the whole screen (`variant="page"`, while `enabled`): nothing is drawn until
 * files are dragged over it, then an overlay says to drop them, with the limits.
 * Only a drag carrying files counts, never a dragged link or text.
 */
export function Dropzone({
  variant,
  limits,
  onFiles,
  enabled = true,
  className,
  children,
}: {
  variant: "full" | "page";
  limits: UploadLimits;
  onFiles: (files: File[]) => void;
  enabled?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const [dragging, setDragging] = useState(false);
  const takes = (event: DragEvent<HTMLDivElement>) => enabled && event.dataTransfer.types.includes("Files");

  function onDrop(event: DragEvent<HTMLDivElement>) {
    if (!takes(event)) return;
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  }

  const classes =
    variant === "page"
      ? className
      : [styles.drop, styles.dropFull, dragging ? styles.dropActive : "", className].filter(Boolean).join(" ");

  return (
    <div
      className={classes}
      onDragOver={(event) => {
        if (!takes(event)) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        // Moving onto a child element also fires dragleave; ignore that.
        const next = event.relatedTarget;
        if (next instanceof Node && event.currentTarget.contains(next)) return;
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      {children}
      {variant === "full" && <p className={styles.dropHint}>Or drag files here. {uploadLimitsHint(limits)}</p>}
      {variant === "page" && dragging && enabled && (
        <div className={styles.dropOverlay} aria-hidden="true">
          <Icon name="upload" size={20} className={styles.dropIcon} />
          <p className={styles.dropText}>Drop the files to upload them.</p>
          <p className={styles.dropHint}>{uploadLimitsHint(limits)}</p>
        </div>
      )}
    </div>
  );
}

/**
 * The batch tray (PRD 5 U1): for each drop, newest first, one line, the time left, one
 * bar (pages read over all pages), then its files in the order they were dropped, so a
 * file never moves as it changes state. A new version, which has no batch, is listed after
 * them. No live region of its own: the status bar's line speaks for the uploads
 * (UploadStatus). "Up to 100 files at a time" is said as an alert. ``onHide``: the Hide
 * button of the status bar's panel.
 */
export function BatchTray({
  entries,
  views,
  stored,
  tooMany,
  onClearFinished,
  onRestore,
  onRetry,
  tree,
  onUndone,
  canReread,
  onHide,
}: {
  entries: UploadEntry[];
  views: Record<string, BatchView>;
  stored: StoredBatch[];
  tooMany: boolean;
  onClearFinished: () => void;
  onRestore: (key: string, documentId: string, existingName: string) => void;
  onRetry: (key: string) => void;
  tree: FolderTree; // for "Filed into Invoices" (feature 28)
  onUndone: () => void;
  canReread: boolean; // a reviewer or an admin: may read a document again
  onHide?: () => void;
}) {
  if (entries.length === 0 && !tooMany) return null;
  const settled = entries.some((entry) => !isBusy(entry));
  const batches = new Map<string, UploadEntry[]>();
  const loose: UploadEntry[] = [];
  for (const entry of entries) {
    if (entry.batchId) batches.set(entry.batchId, [...(batches.get(entry.batchId) ?? []), entry]);
    else loose.push(entry);
  }
  const row = (entry: UploadEntry) => (
    <UploadRow
      key={entry.key}
      entry={entry}
      onRestore={onRestore}
      onRetry={onRetry}
      tree={tree}
      onUndone={onUndone}
      canReread={canReread}
    />
  );

  return (
    <section className={styles.uploadsSection} aria-labelledby="uploads-heading">
      <div className={styles.uploadsHead}>
        <h2 id="uploads-heading" className={styles.smallHeading}>
          Uploads
        </h2>
        <span className={styles.uploadsActions}>
          {settled && (
            <button type="button" className="btn btn-quiet" onClick={onClearFinished}>
              Clear finished
            </button>
          )}
          {onHide && (
            <button type="button" className="btn btn-quiet" onClick={onHide}>
              <Icon name="close" />
              Hide
            </button>
          )}
        </span>
      </div>
      {tooMany && (
        <p className={`error-text ${styles.small}`} role="alert">
          {TOO_MANY_FILES}
        </p>
      )}
      {[...batches].map(([id, rows]) => {
        const view = views[id] ?? null;
        const busy = rows.some(isBusy);
        const left = busy ? leftText(view) : null;
        // File management V5: the files of this drop that look like new versions, and Review.
        const versions = suggestionsText(view?.version_suggestion_count ?? view?.version_suggestions?.length ?? 0);
        const found = view?.version_suggestions?.[0];
        const first = found ? { document_id: found.document.id, display_name: found.document.name } : undefined;
        return (
          <div key={id} className={styles.batch}>
            <div className={styles.batchHead}>
              <p className={styles.batchSummary}>
                {summaryText(trayCounts(rows, stored.find((batch) => batch.id === id)))}
              </p>
              {left && <span className={`muted ${styles.small} ${styles.timeLeft}`}>{left}</span>}
            </div>
            {versions && first && (
              <p className={styles.small}>
                {versions} ·{" "}
                <Link href={`/documents/${first.document_id}`}>
                  {VERSION_TEXT.review}
                  <span className="visually-hidden">{` ${first.display_name}`}</span>
                </Link>
              </p>
            )}
            {busy && <BatchBar view={view} rows={rows} />}
            <ul className={styles.uploads}>{rows.map(row)}</ul>
          </div>
        );
      })}
      {loose.length > 0 && <ul className={styles.uploads}>{loose.map(row)}</ul>}
    </section>
  );
}

/**
 * One drop's bar: its files' own fractions weighed by their pages, with the bytes of the
 * files still sending (batchFraction), on the shared tick; never moving back, at most 99% (lib/progress.ts).
 */
function BatchBar({ view, rows }: { view: BatchView | null; rows: UploadEntry[] }) {
  const now = useTick();
  const [shown, setShown] = useState<ShownProgress | null>(null);
  const next = advance(shown, 0, batchFraction(view, rows, now));
  if (next !== shown) setShown(next);
  return (
    <div className={styles.progressRow}>
      <progress
        className={styles.readMeter}
        max={100}
        value={barValue(next.percent)}
        aria-label="Pages read in this batch"
        aria-valuetext={`${next.percent}% of pages read`}
      />
    </div>
  );
}

/**
 * A file's bar through Uploading, Saving (indeterminate: the server gives no number) and
 * Scanning, over the whole job (uploadStages.ts). It moves on the shared tick, one step at
 * most. No percent beside it: the row's badge says the stage.
 */
function RowBar({ entry, stage }: { entry: UploadEntry; stage: Stage }) {
  const now = useTick();
  const [shown, setShown] = useState<Shown | null>(null);
  const kind = uploadKind(entry.name);
  const readSeconds = kind === null ? 0 : estimateIngestSeconds(kind, entry.size, 1, entry.pages ?? null);
  const fraction = jobFraction(entry.state, entry.size, readSeconds, entry.times ?? NO_TIMES, now);
  const next = nextShown(shown, fraction, now);
  if (next !== shown) setShown(next);
  const saving = stage === "saving";
  return (
    <div className={styles.progressRow}>
      <progress
        className={styles.readMeter}
        max={100}
        value={saving ? undefined : next.percent}
        aria-label={`Upload of ${entry.name}`}
        aria-valuetext={valueText(entry.state, next.percent)}
      />
    </div>
  );
}

function UploadRow({
  entry,
  onRestore,
  onRetry,
  tree,
  onUndone,
  canReread,
}: {
  entry: UploadEntry;
  onRestore: (key: string, documentId: string, existingName: string) => void;
  onRetry: (key: string) => void;
  tree: FolderTree;
  onUndone: () => void;
  canReread: boolean;
}) {
  const { state } = entry;
  // After Restore, the Restore button gives way to "Open it": focus follows.
  const focusOnMount = useCallback((element: HTMLAnchorElement | null) => {
    element?.focus();
  }, []);
  const kind = rowKind(entry);
  const failed = kind === "failed" || kind === "stopped";
  const stage = stageOf(state);
  const story = storyTarget(state); // a finished file offers its story section, never a dialog
  const document = state.kind === "done" ? state.document : undefined;
  const ready = document?.status === "ready" ? document : undefined;
  const unreadable = document?.status === "ready" ? unreadableWords(document, canReread) : null; // U1
  const verified = document ? verifiedWords(document) : null; // U1: only with values
  let icon: "alert" | "eye-alert" | "check-seal" | "file" = "file";
  if (failed) icon = "alert";
  else if (kind === "attention") icon = "eye-alert";
  else if (document) icon = "check-seal";

  return (
    <li className={styles.upload} data-state={failed ? "error" : state.kind}>
      <div className={styles.uploadIconWrap}>
        <Icon name={icon} className={styles.uploadIcon} />
      </div>
      <div className={styles.uploadBody}>
        <div className={styles.uploadHead}>
          <span className={styles.fileName}>
            <WrapName name={entry.name} />
          </span>
          <span className={`muted tabular ${styles.small}`}>{formatBytes(entry.size)}</span>
          <span className={failed ? `error-text ${styles.small} ${styles.badge}` : `muted ${styles.small} ${styles.badge}`}>
            {uploadStateText(state)}
          </span>
        </div>
        {entry.versionOf !== undefined && (
          <p className={`muted ${styles.small}`}>New version of “{entry.versionOf}”</p>
        )}
        {stage !== null && <RowBar entry={entry} stage={stage} />}
        {state.kind === "reading" && <p className={`muted ${styles.small}`}>{scanPhrase(state.document)}</p>}
        {unreadable?.detail && <p className={styles.small}>{unreadable.detail}</p>}
        {ready && <p className={styles.small}>{`Scan complete · ${flaggedText(ready)}`}</p>}
        {verified && <p className={`muted ${styles.small}`}>{verified}</p>}
        {document?.status === "ready" && (
          <AutofileNote
            document={document}
            name={entry.name}
            filed={document.folder_id ? filedText(pathLabel(tree, document.folder_id)) : ""}
            folderHref={document.folder_id ? libraryHref({ ...ALL_DOCUMENTS, folderId: document.folder_id }) : null}
            onUndone={onUndone}
          />
        )}
        {document && document.status !== "ready" && (
          <p className={`error-text ${styles.small}`}>{document.status_message}</p>
        )}
        {state.kind === "done" && (
          <Link href={`/documents/${state.documentId}`} className={styles.inlineAction}>
            {ready && toReview(ready) ? "Open to review" : "Open"}
            <span className="visually-hidden">{` ${entry.name}`}</span>
          </Link>
        )}
        {state.kind === "duplicate" && !state.inTrash && (
          <p className={styles.small}>
            This file was uploaded before, as “{state.existingName}”. Nothing new was stored.{" "}
            <Link href={`/documents/${state.documentId}`}>Open it</Link>
          </p>
        )}
        {state.kind === "duplicate" && entry.reading !== undefined && (
          <p className={`muted ${styles.small}`}>{DUPLICATE_READER_NOTE}</p>
        )}
        {(state.kind === "restoring" || (state.kind === "duplicate" && state.inTrash)) && (
          <>
            <p className={styles.small}>
              This file was uploaded before, as “{state.existingName}”, and that copy is in Trash.
              Nothing new was stored.
            </p>
            <button
              type="button"
              className={`btn ${styles.inlineAction}`}
              aria-disabled={state.kind === "restoring" ? true : undefined}
              onClick={() => {
                if (state.kind === "duplicate") onRestore(entry.key, state.documentId, state.existingName);
              }}
            >
              <BusyLabel busy={state.kind === "restoring"} idle="Restore it from Trash" working="Restoring…" />
            </button>
            {state.kind === "duplicate" && state.restoreError && (
              <p className={`error-text ${styles.small}`} role="alert">
                {state.restoreError}
              </p>
            )}
          </>
        )}
        {state.kind === "restored" && (
          <p className={styles.small}>
            Restored “{state.existingName}” from Trash.{" "}
            <Link ref={focusOnMount} href={`/documents/${state.documentId}`}>
              Open it
            </Link>
          </p>
        )}
        {state.kind === "error" && <p className={`error-text ${styles.small}`}>{state.message}</p>}
        {state.kind === "error" && state.retry && (
          <button type="button" className={`btn ${styles.inlineAction}`} onClick={() => onRetry(entry.key)}>
            <Icon name="refresh" />
            Retry<span className="visually-hidden">{` ${entry.name}`}</span>
          </button>
        )}
        {story !== null && (
          <Link href={storyHref(story)} className={`${styles.inlineAction} ${styles.storyLink}`}>
            Add a story so you can find this later
            <span className="visually-hidden">{`: ${entry.name}`}</span>
          </Link>
        )}
      </div>
    </li>
  );
}

/**
 * The uploads in the shell's status bar (scan states, 16 Oct 2026; brief C: progress there,
 * never a modal): one line, "Scanning 2 documents · page 3 of 14 · About 1 min left"
 * (statusBarText), in a polite live region, and an Uploads button that opens the tray as
 * a panel in a fixed corner above the bar (a bottom sheet below 768 px), so it never pushes
 * the list under the pointer. Not a modal: no backdrop; Hide, Escape or the button again
 * close it, and focus goes back to the button. The line is always there on this screen, so
 * a live region exists before it first speaks.
 */
export function UploadStatus({
  open,
  onOpenChange,
  ...tray
}: Omit<Parameters<typeof BatchTray>[0], "onHide"> & { open: boolean; onOpenChange: (open: boolean) => void }) {
  const id = useId();
  const toggle = useRef<HTMLButtonElement>(null);
  const shown = tray.entries.length > 0 || tray.tooMany;
  // The time left counts down only while a file is sending or being read; settled, the line
  // stays still instead of redrawing on the shared tick.
  const busy = tray.entries.some(isBusy);
  function hide() {
    onOpenChange(false);
    toggle.current?.focus();
  }
  return (
    <InStatusBar>
      <p className={styles.barLine} aria-live="polite">
        {busy ? (
          <TickingLine entries={tray.entries} views={tray.views} />
        ) : (
          <LineText text={statusBarText(tray.entries, tray.views, 0)} />
        )}
      </p>
      {shown && (
        <button
          ref={toggle}
          type="button"
          className={styles.barToggle}
          aria-expanded={open}
          aria-controls={`${id}-uploads`}
          onClick={() => onOpenChange(!open)}
        >
          Uploads
        </button>
      )}
      {shown && (
        <div
          id={`${id}-uploads`}
          className={`motion-pop ${styles.trayPanel}`}
          hidden={!open}
          onKeyDown={(event) => {
            if (event.key === "Escape") hide();
          }}
        >
          {/* Drawn only while open: closed, nobody sees its rows redraw. */}
          {open && <BatchTray {...tray} onHide={hide} />}
        </div>
      )}
    </InStatusBar>
  );
}

function TickingLine({ entries, views }: { entries: UploadEntry[]; views: Record<string, BatchView> }) {
  const now = useTick();
  return <LineText text={statusBarText(entries, views, now)} />;
}

/** The line's words, the whole of them in the tooltip when the bar cuts them. */
function LineText({ text }: { text: string }) {
  return <span title={text || undefined}>{text}</span>;
}
