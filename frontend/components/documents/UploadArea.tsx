"use client";

// Uploading. Files come from the "Upload files" button (the hidden file input
// the documents screen owns) or are dropped on the dropzone. Each file is
// checked first (kind and size, against the limits from GET /upload-limits),
// then, just before its turn, a PDF's or TIFF's pages are counted (it stays
// "Waiting" meanwhile), then it is sent, 3 at a time (PRD 5 Q2), with its own
// progress bar, into the folder that was being viewed when they were added.
// Each drop is one batch (Q1): the browser makes its id, and the batch tray
// (U1, batchTray.ts) shows one line and one bar for it, then its files worst
// first. Each file's bar moves through Sending, Saving and Reading and stays
// under 100% until its document is ready (U10, uploadStages.ts): a stored file
// is followed through GET /api/batches/{id} (lib/poll.ts back-off) while it is
// read. After a reload the tray comes back for the drops of the last 24 hours.
// A new version of a document ("Upload new version") goes through the same
// queue, with no batch. The outcome, including "already uploaded" and any error, stays next
// to its file until "Clear finished"; a duplicate whose earlier copy is in
// Trash offers Restore, and a file not sent offers Retry. A finished file also links to its story section in
// the viewer (a link, never a dialog: nothing waits on it). Each batch is
// summed up once when it settles (uploadQueue.ts); the documents screen says it.
// Each file is sent with who was signed in when it was added, so the server
// refuses it if another account has signed in since (critical #5); then
// no file still waiting is sent, and each says why.
// "Read with" (ReaderSelect, readers.ts) picks the model that reads the scans
// of the files added next, new versions included; it is kept for this visit
// only (a module variable: a reload starts at the organisation's default again).

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { beingRead } from "@/components/viewer/reprocess";
import { storyHref, storyTarget } from "@/components/viewer/story";
import {
  ApiError,
  getBatch,
  getUploadLimits,
  restoreDocument,
  uploadDocument,
  uploadVersion,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { progressSignature } from "@/lib/poll";
import { advance, barValue } from "@/lib/progress";
import type { ShownProgress } from "@/lib/progress";
import { useSession } from "@/lib/session";
import { useTick } from "@/lib/tick";
import type { BatchView, UploadLimits, UploadReader, UploadResult } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import {
  MAX_FILES_PER_BATCH,
  TOO_MANY_FILES,
  announcement,
  batchFraction,
  batchStore,
  leftText,
  newBatchId,
  readStored,
  rowKind,
  summaryText,
  trayCounts,
  unreadableWords,
  verifiedWords,
  worstFirst,
  writeStored,
} from "./batchTray";
import type { StoredBatch } from "./batchTray";
import { attentionWords } from "./library";
import {
  FALLBACK_UPLOAD_LIMITS,
  estimateIngestSeconds,
  sessionChangedMessage,
  uploadErrorText,
  uploadKind,
  uploadLimitsHint,
  uploadStateText,
  validateUpload,
} from "./logic";
import type { UploadState } from "./logic";
import { checkPages } from "./pageCount";
import { DUPLICATE_READER_NOTE, offersChoice, pickedReader, readerOptions, readerToSend } from "./readers";
import { EMPTY_QUEUE, UPLOADS_AT_ONCE, followed, isBusy, queueReducer, runLimited } from "./uploadQueue";
import type { UploadEntry } from "./uploadQueue";
import { NO_TIMES, jobFraction, nextShown, stageOf, stageText, valueText } from "./uploadStages";
import type { Shown, Stage, StageTimes } from "./uploadStages";
import { WrapName } from "./WrapName";
import styles from "./Documents.module.css";

type Start = (onProgress: (fraction: number) => void, onSent: () => void) => Promise<UploadResult>;

/** What one round of following the batches brought back. */
interface Followed {
  views: BatchView[];
  gone: string[]; // 404: not this person's batch, or none of its files was stored
  failed: boolean; // a batch did not answer: ask again
}

/** Not sent because of the connection or the server, not because the file was refused: it can be sent again. */
const canRetry = (caught: unknown) =>
  !(caught instanceof ApiError) || caught.status === 0 || caught.status === 429 || caught.status >= 500;

// The model picked in "Read with" during this visit; null: the organisation's default.
let sessionReader: string | null = null;

/** The upload queue: add files, watch them go, clear the finished ones. */
export function useUploads(onUploaded: () => void) {
  const { me } = useSession();
  const nextKey = useRef(0);
  // The server's words once another account has signed in here; then nothing more is sent.
  const stopped = useRef<string | null>(null);
  const [queue, dispatch] = useReducer(queueReducer, EMPTY_QUEUE);
  const [limits, setLimits] = useState<UploadLimits>(FALLBACK_UPLOAD_LIMITS);
  const [reader, setReader] = useState<string | null>(sessionReader);
  // The files this tab still holds, so a file not sent can be sent again (Retry).
  const kept = useRef(new Map<string, Start>());
  // The drops of the last 24 hours (batchTray.ts), and the batch API's latest view of each.
  const [stored, setStored] = useState<StoredBatch[]>(() => readStored(batchStore(), Date.now()));
  // Drops from before a reload: every file the batch API lists gets a row.
  const [restored] = useState(() => new Set(stored.map((batch) => batch.id)));
  const [views, setViews] = useState<Record<string, BatchView>>({});
  const [tooMany, setTooMany] = useState(false);

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
        if (!cancelled) setLimits(loaded);
      },
      () => undefined,
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

  const poll = usePoll<Followed>({
    load: () =>
      Promise.allSettled(following.map((id) => getBatch(id))).then((results) => {
        const round: Followed = { views: [], gone: [], failed: false };
        results.forEach((result, index) => {
          if (result.status === "fulfilled") round.views.push(result.value);
          else if (result.reason instanceof ApiError && result.reason.status === 404) round.gone.push(following[index] ?? "");
          else round.failed = true;
        });
        return round;
      }),
    onResult: ({ views: answers, gone }) => {
      for (const view of answers) {
        dispatch({ type: "batch", batchId: view.id, documents: view.documents, add: restored.has(view.id) });
      }
      setViews((shown) => ({ ...shown, ...Object.fromEntries(answers.map((view) => [view.id, view])) }));
      if (gone.length > 0) setStored((batches) => batches.filter((batch) => !gone.includes(batch.id) || !restored.has(batch.id)));
    },
    onFailure: () => undefined, // it keeps trying; the rows show what they have
    again: (round) =>
      round.failed || round.views.some((view) => view.documents.some((document) => beingRead(document.status))),
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
    const times: StageTimes = { startedAt: performance.now(), sentAt: null, repliedAt: null };
    dispatch({ type: "change", key, change: { state: { kind: "sending", fraction: 0 }, times } });
    try {
      const result = await start(
        (fraction) => update(key, { kind: "sending", fraction }),
        () => {
          times.sentAt = performance.now();
          dispatch({ type: "change", key, change: { state: { kind: "saving" }, times: { ...times } } });
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
    if (start && !notSent(key)) await send(key, start, follow);
  }

  /**
   * Queue one drop of files for upload into folderId (undefined: the top level): one
   * batch of at most MAX_FILES_PER_BATCH files; the rest are refused and the tray says so.
   */
  async function addFiles(files: File[], folderId?: string) {
    const userId = me.user.id;
    const ocrChoice = readerToSend(limits.readers, reader);
    const batchId = newBatchId();
    setTooMany(files.length > MAX_FILES_PER_BATCH);
    const added = files.slice(0, MAX_FILES_PER_BATCH).map((file) => {
      nextKey.current += 1;
      const problem = validateUpload(file, limits);
      const entry: UploadEntry = {
        key: `upload-${nextKey.current}`,
        name: file.name,
        size: file.size,
        ocrChoice,
        batchId,
        state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
      };
      const start: Start = (onProgress, onSent) =>
        uploadDocument(file, userId, onProgress, folderId, ocrChoice, batchId, onSent);
      return { entry, file, start };
    });
    dispatch({ type: "add", entries: added.map((item) => item.entry) });
    setStored((batches) => [{ id: batchId, at: Date.now(), already: 0, notSent: 0 }, ...batches]);
    const sending = added.filter((item) => item.entry.state.kind === "waiting");
    for (const { entry, start } of sending) kept.current.set(entry.key, start);
    // A few at a time, so a big batch does not trip the rate limit.
    await runLimited(sending, UPLOADS_AT_ONCE, ({ entry, file, start }) => checkThenSend(entry.key, file, start, true));
  }

  /** Queue one file as the next version of a document. It has no batch: its row ends at the reply. */
  async function addVersion(file: File, of: { id: string; display_name: string }) {
    const userId = me.user.id;
    const ocrChoice = readerToSend(limits.readers, reader);
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
          ocrChoice,
          state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
        },
      ],
    });
    if (!problem) {
      const start: Start = (onProgress, onSent) => uploadVersion(of.id, file, userId, onProgress, ocrChoice, onSent);
      kept.current.set(key, start);
      await checkThenSend(key, file, start, false);
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
    reader,
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
 * "Read with": a labelled native select of the reading choices the server
 * offers ("Standard", "Careful"), with the picked one's hint
 * below it. Not shown unless two or more can be used. Also the whole
 * document's Read again (ReadAgain.tsx).
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
      <p id={`${id}-hint`} className={styles.readerHint}>
        {current?.hint}
      </p>
    </div>
  );
}

/**
 * Where files can be dropped. With no documents yet it is a full dashed card
 * holding the empty state; once there are documents it is a slim strip.
 */
export function Dropzone({
  variant,
  limits,
  onFiles,
  onChoose,
  children,
}: {
  variant: "full" | "slim";
  limits: UploadLimits;
  onFiles: (files: File[]) => void;
  onChoose: () => void;
  children?: ReactNode;
}) {
  const [dragging, setDragging] = useState(false);

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  }

  const classes = [
    styles.drop,
    variant === "slim" ? styles.dropSlim : styles.dropFull,
    dragging ? styles.dropActive : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      onDragOver={(event) => {
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
      {variant === "full" ? (
        <>
          {children}
          <p className={styles.dropHint}>Or drag files here. {uploadLimitsHint(limits)}</p>
        </>
      ) : (
        <>
          <Icon name="upload" className={styles.dropIcon} />
          <p className={styles.dropText}>
            {dragging ? "Drop the files to upload them." : "Drag files here to upload, or"}
          </p>
          <button type="button" className="btn" onClick={onChoose}>
            Choose files
          </button>
          <p className={styles.dropHint}>{uploadLimitsHint(limits)}</p>
        </>
      )}
    </div>
  );
}

/**
 * The batch tray (PRD 5 U1): for each drop, newest first, one line, the time left, one
 * bar (pages read over all pages), then its files worst first. A new version, which has
 * no batch, is listed after them. One live region for the whole tray (role=status, kept
 * while the tray is shown) says the ready, attention and failed totals, so it speaks
 * only when one of them changes, never as files change stage or by percent (WCAG 4.1.3).
 * "Up to 100 files at a time" is said as an alert.
 */
export function BatchTray({
  entries,
  views,
  stored,
  tooMany,
  onClearFinished,
  onRestore,
  onRetry,
}: {
  entries: UploadEntry[];
  views: Record<string, BatchView>;
  stored: StoredBatch[];
  tooMany: boolean;
  onClearFinished: () => void;
  onRestore: (key: string, documentId: string, existingName: string) => void;
  onRetry: (key: string) => void;
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
    <UploadRow key={entry.key} entry={entry} onRestore={onRestore} onRetry={onRetry} />
  );

  return (
    <section className={styles.uploadsSection} aria-labelledby="uploads-heading">
      <div className={styles.uploadsHead}>
        <h2 id="uploads-heading" className={styles.smallHeading}>
          Uploads
        </h2>
        {settled && (
          <button type="button" className="btn btn-quiet" onClick={onClearFinished}>
            Clear finished
          </button>
        )}
      </div>
      <p className="visually-hidden" role="status">
        {announcement([...batches.values(), loose])}
      </p>
      {tooMany && (
        <p className={`error-text ${styles.small}`} role="alert">
          {TOO_MANY_FILES}
        </p>
      )}
      {[...batches].map(([id, rows]) => {
        const view = views[id] ?? null;
        const busy = rows.some(isBusy);
        const left = busy ? leftText(view) : null;
        return (
          <div key={id} className={styles.batch}>
            <div className={styles.batchHead}>
              <p className={styles.batchSummary}>
                {summaryText(trayCounts(rows, stored.find((batch) => batch.id === id)))}
              </p>
              {left && <span className={`muted ${styles.small}`}>{left}</span>}
            </div>
            {busy && <BatchBar view={view} rows={rows} />}
            <ul className={styles.uploads}>{worstFirst(rows).map(row)}</ul>
          </div>
        );
      })}
      {loose.length > 0 && <ul className={styles.uploads}>{loose.map(row)}</ul>}
    </section>
  );
}

/**
 * One drop's bar: pages read over all its pages, with the bytes of the files still
 * sending (batchFraction), on the shared tick; never moving back, at most 99% (lib/progress.ts).
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
      <span className={`muted tabular ${styles.small}`} aria-hidden="true">
        {next.percent}%
      </span>
    </div>
  );
}

/**
 * A file's bar through Sending, Saving (no number: the server gives none) and Reading,
 * over the whole job (uploadStages.ts). It moves on the shared tick, one step at most.
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
        aria-valuetext={valueText(stage, next.percent)}
      />
      <span className={`muted tabular ${styles.small}`} aria-hidden="true">
        {saving ? stageText(stage) : `${next.percent}%`}
      </span>
    </div>
  );
}

function UploadRow({
  entry,
  onRestore,
  onRetry,
}: {
  entry: UploadEntry;
  onRestore: (key: string, documentId: string, existingName: string) => void;
  onRetry: (key: string) => void;
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
  const problems = document?.status === "ready" ? attentionWords(document) : [];
  const unreadable = document?.status === "ready" ? unreadableWords(document) : null; // U1
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
        {state.kind === "reading" && <p className={`muted ${styles.small}`}>{state.document.status_message}</p>}
        {unreadable?.detail && <p className={styles.small}>{unreadable.detail}</p>}
        {document && problems.length > 0 && (
          <p className={styles.small}>
            {problems.join(" · ")}.{" "}
            <Link href={`/documents/${document.id}`}>
              Check them<span className="visually-hidden">{` in ${entry.name}`}</span>
            </Link>
          </p>
        )}
        {verified && <p className={`muted ${styles.small}`}>{verified}</p>}
        {document && document.status !== "ready" && (
          <p className={`error-text ${styles.small}`}>{document.status_message}</p>
        )}
        {state.kind === "done" && (
          <Link href={`/documents/${state.documentId}`} className={styles.inlineAction}>
            Open
            <span className="visually-hidden">{` ${entry.name}`}</span>
          </Link>
        )}
        {state.kind === "duplicate" && !state.inTrash && (
          <p className={styles.small}>
            This file was uploaded before, as “{state.existingName}”. Nothing new was stored.{" "}
            <Link href={`/documents/${state.documentId}`}>Open it</Link>
          </p>
        )}
        {state.kind === "duplicate" && entry.ocrChoice !== undefined && (
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
              {state.kind === "restoring" && <span className="spinner" aria-hidden="true" />}
              {state.kind === "restoring" ? "Restoring…" : "Restore it from Trash"}
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
