"use client";

// Uploading. Files come from the "Upload files" button (the hidden file input
// the documents screen owns) or are dropped on the dropzone. Each file is
// checked first (kind and size), then sent one after another with its own
// progress bar. The outcome, including "already uploaded" and any error, stays
// next to its file.

import Link from "next/link";
import { useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, uploadDocument } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import {
  UPLOAD_LIMITS_HINT,
  estimateIngestSeconds,
  formatEtaText,
  uploadStateText,
  validateUpload,
} from "./logic";
import type { UploadState } from "./logic";
import styles from "./Documents.module.css";

export interface UploadEntry {
  key: string;
  name: string;
  size: number;
  state: UploadState;
}

const isBusy = (entry: UploadEntry) =>
  entry.state.kind === "waiting" || entry.state.kind === "uploading";

/** The upload queue: add files, watch them go, clear the finished ones. */
export function useUploads(onUploaded: () => void) {
  const nextKey = useRef(0);
  const [entries, setEntries] = useState<UploadEntry[]>([]);

  function update(key: string, state: UploadState) {
    setEntries((current) =>
      current.map((entry) => (entry.key === key ? { ...entry, state } : entry)),
    );
  }

  async function send(key: string, file: File) {
    update(key, { kind: "uploading", fraction: 0 });
    try {
      const result = await uploadDocument(file, (fraction) =>
        update(key, { kind: "uploading", fraction }),
      );
      update(
        key,
        result.duplicate
          ? {
              kind: "duplicate",
              documentId: result.document.id,
              existingName: result.document.display_name,
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

  async function addFiles(files: File[]) {
    const added = files.map((file) => {
      nextKey.current += 1;
      const problem = validateUpload(file);
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
      if (entry.state.kind === "waiting") await send(entry.key, file);
    }
  }

  function clearFinished() {
    setEntries((current) => current.filter(isBusy));
  }

  return { entries, addFiles, clearFinished };
}

/**
 * Where files can be dropped. With no documents yet it is a full dashed card
 * holding the empty state; once there are documents it is a slim strip.
 */
export function Dropzone({
  variant,
  onFiles,
  onChoose,
  children,
}: {
  variant: "full" | "slim";
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
          <p className={styles.dropHint}>Or drag files here. {UPLOAD_LIMITS_HINT}</p>
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
          <p className={styles.dropHint}>{UPLOAD_LIMITS_HINT}</p>
        </>
      )}
    </div>
  );
}

export function UploadList({
  entries,
  onClearFinished,
}: {
  entries: UploadEntry[];
  onClearFinished: () => void;
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
          <UploadRow key={entry.key} entry={entry} />
        ))}
      </ul>
    </section>
  );
}

function UploadRow({ entry }: { entry: UploadEntry }) {
  const { state } = entry;
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
        {state.kind === "duplicate" && (
          <p className={styles.small}>
            This file was uploaded before, as “{state.existingName}”. Nothing new was stored.{" "}
            <Link href={`/documents/${state.documentId}`}>Open it</Link>
          </p>
        )}
        {failed && <p className={`error-text ${styles.small}`}>{state.message}</p>}
      </div>
    </li>
  );
}
