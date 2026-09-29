"use client";

// Uploading. Files come from the "Upload files" button (the hidden file input
// the documents screen owns) or are dropped on the dropzone. Each file is
// checked first (kind and size, against the limits from GET /upload-limits),
// then, just before its turn, a PDF's or TIFF's pages are counted (it stays
// "Waiting" meanwhile), then it is sent, one after another with its own
// progress bar, into the folder that was being viewed when they were added.
// A new version of a document ("Upload new version") goes through the same
// queue. The outcome, including "already uploaded" and any error, stays next
// to its file; a duplicate whose earlier copy is in Trash offers Restore.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import {
  ApiError,
  getUploadLimits,
  restoreDocument,
  uploadDocument,
  uploadVersion,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { UploadLimits, UploadResult } from "@/lib/types";
import {
  FALLBACK_UPLOAD_LIMITS,
  estimateIngestSeconds,
  formatEtaText,
  uploadLimitsHint,
  uploadStateText,
  validateUpload,
} from "./logic";
import type { UploadState } from "./logic";
import { pageProblem } from "./pageCount";
import styles from "./Documents.module.css";

export interface UploadEntry {
  key: string;
  name: string;
  size: number;
  versionOf?: string; // for a new version: the name of the document it updates
  state: UploadState;
}

const isBusy = (entry: UploadEntry) =>
  entry.state.kind === "waiting" ||
  entry.state.kind === "uploading" ||
  entry.state.kind === "restoring";

type Start = (onProgress: (fraction: number) => void) => Promise<UploadResult>;

/** The upload queue: add files, watch them go, clear the finished ones. */
export function useUploads(onUploaded: () => void) {
  const nextKey = useRef(0);
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [limits, setLimits] = useState<UploadLimits>(FALLBACK_UPLOAD_LIMITS);

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
    setEntries((current) =>
      current.map((entry) => (entry.key === key ? { ...entry, state } : entry)),
    );
  }

  async function send(key: string, start: Start) {
    update(key, { kind: "uploading", fraction: 0 });
    try {
      const result = await start((fraction) => update(key, { kind: "uploading", fraction }));
      update(
        key,
        result.duplicate
          ? {
              kind: "duplicate",
              documentId: result.document.id,
              existingName: result.document.display_name,
              inTrash: result.in_trash,
            }
          : { kind: "done", documentId: result.document.id },
      );
      onUploaded();
    } catch (caught) {
      const message =
        caught instanceof ApiError
          ? caught.message
          : "Upload failed. Check your connection, then try again.";
      update(key, { kind: "error", message });
    }
  }

  /** Count a PDF's or TIFF's pages, then send it, unless it has too many. */
  async function checkThenSend(key: string, file: File, start: Start) {
    const problem = await pageProblem(file, limits.max_pages);
    if (problem) update(key, { kind: "error", message: problem });
    else await send(key, start);
  }

  /** Queue files for upload into folderId (undefined: the top level). */
  async function addFiles(files: File[], folderId?: string) {
    const added = files.map((file) => {
      nextKey.current += 1;
      const problem = validateUpload(file, limits);
      const entry: UploadEntry = {
        key: `upload-${nextKey.current}`,
        name: file.name,
        size: file.size,
        state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
      };
      return { entry, file };
    });
    setEntries((current) => [...added.map((item) => item.entry), ...current]);
    // One at a time, so a big batch does not trip the rate limit.
    for (const { entry, file } of added) {
      if (entry.state.kind === "waiting") {
        await checkThenSend(entry.key, file, (onProgress) =>
          uploadDocument(file, onProgress, folderId),
        );
      }
    }
  }

  /** Queue one file as the next version of a document. */
  async function addVersion(file: File, of: { id: string; display_name: string }) {
    nextKey.current += 1;
    const key = `upload-${nextKey.current}`;
    const problem = validateUpload(file, limits);
    setEntries((current) => [
      {
        key,
        name: file.name,
        size: file.size,
        versionOf: of.display_name,
        state: problem ? { kind: "error", message: problem } : { kind: "waiting" },
      },
      ...current,
    ]);
    if (!problem) {
      await checkThenSend(key, file, (onProgress) => uploadVersion(of.id, file, onProgress));
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

  function clearFinished() {
    setEntries((current) => current.filter(isBusy));
  }

  return { entries, limits, addFiles, addVersion, restore, clearFinished };
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

export function UploadList({
  entries,
  onClearFinished,
  onRestore,
}: {
  entries: UploadEntry[];
  onClearFinished: () => void;
  onRestore: (key: string, documentId: string, existingName: string) => void;
}) {
  if (entries.length === 0) return null;
  const settled = entries.some((entry) => !isBusy(entry));
  const active = entries.filter(isBusy).length;
  const done = entries.filter((entry) => entry.state.kind === "done").length;
  const failed = entries.filter((entry) => entry.state.kind === "error").length;

  return (
    <section className={styles.uploadsSection} aria-labelledby="uploads-heading">
      <div className={styles.uploadsHead}>
        <div className={styles.uploadsTitleWrap}>
          <h2 id="uploads-heading" className={styles.smallHeading}>
            Uploads
          </h2>
          <span className={styles.queueSummary} aria-live="polite">
            {active} active · {done} done · {failed} failed
          </span>
        </div>
        {settled && (
          <button type="button" className="btn btn-quiet" onClick={onClearFinished}>
            Clear finished
          </button>
        )}
      </div>
      <ul className={styles.uploads} aria-live="polite">
        {entries.map((entry) => (
          <UploadRow key={entry.key} entry={entry} onRestore={onRestore} />
        ))}
      </ul>
    </section>
  );
}

function UploadRow({
  entry,
  onRestore,
}: {
  entry: UploadEntry;
  onRestore: (key: string, documentId: string, existingName: string) => void;
}) {
  const { state } = entry;
  // After Restore, the Restore button gives way to "Open it": focus follows.
  const focusOnMount = useCallback((element: HTMLAnchorElement | null) => {
    element?.focus();
  }, []);
  const failed = state.kind === "error";
  const estimateSeconds = estimateIngestSeconds(entry.size);
  const etaText =
    state.kind === "uploading"
      ? formatEtaText(Math.max(0, estimateSeconds * (1 - state.fraction)))
      : formatEtaText(estimateSeconds);

  return (
    <li className={styles.upload} data-state={state.kind}>
      <div className={styles.uploadIconWrap}>
        <Icon name={failed ? "alert" : "file"} className={styles.uploadIcon} />
      </div>
      <div className={styles.uploadBody}>
        <div className={styles.uploadHead}>
          <span className={styles.fileName}>{entry.name}</span>
          <span className={`muted tabular ${styles.small}`}>{formatBytes(entry.size)}</span>
          <span className={failed ? `error-text ${styles.small} ${styles.badge}` : `muted ${styles.small} ${styles.badge}`}>
            {uploadStateText(state)}
          </span>
        </div>
        {entry.versionOf !== undefined && (
          <p className={`muted ${styles.small}`}>New version of “{entry.versionOf}”</p>
        )}
        {state.kind === "uploading" && (
          <>
            <div className={styles.progressRow}>
              <div className={styles.progressTrack}>
                <span
                  className={styles.progressFill}
                  style={{ width: `${Math.max(0, Math.min(100, state.fraction * 100))}%` }}
                />
              </div>
              <span className={`muted tabular ${styles.small}`} aria-hidden="true">
                {Math.round(state.fraction * 100)}%
              </span>
            </div>
            <p className={`muted ${styles.small}`}>ETA: {etaText}</p>
          </>
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
        {failed && <p className={`error-text ${styles.small}`}>{state.message}</p>}
      </div>
    </li>
  );
}
