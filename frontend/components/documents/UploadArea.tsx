"use client";

// Drop files here or pick them with the button. Each file is checked first
// (kind and size), then sent one after another with its own progress bar. The
// outcome, including "already uploaded" and any error, stays next to its file.

import Link from "next/link";
import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";
import { ApiError, uploadDocument } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { ACCEPT_ATTRIBUTE, uploadStateText, validateUpload } from "./logic";
import type { UploadState } from "./logic";
import styles from "./Documents.module.css";

interface Entry {
  key: string;
  name: string;
  size: number;
  state: UploadState;
}

export function UploadArea({ onUploaded }: { onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [dragging, setDragging] = useState(false);

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
      const message = caught instanceof ApiError ? caught.message : "Upload failed.";
      update(key, { kind: "error", message });
    }
  }

  async function addFiles(files: File[]) {
    const added = files.map((file) => {
      nextKey.current += 1;
      const problem = validateUpload(file);
      const entry: Entry = {
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

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // so choosing the same file again still fires
    if (files.length > 0) void addFiles(files);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) void addFiles(files);
  }

  const settled = entries.some(
    (entry) => entry.state.kind !== "waiting" && entry.state.kind !== "uploading",
  );

  return (
    <section className="card stack" aria-labelledby="upload-heading">
      <h2 id="upload-heading">Upload</h2>
      <div
        className={dragging ? `${styles.drop} ${styles.dropActive}` : styles.drop}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p>Drag files here, or</p>
        <button type="button" className="btn btn-primary" onClick={() => inputRef.current?.click()}>
          Choose files
        </button>
        <label htmlFor="upload-input" className="visually-hidden">
          Files to upload
        </label>
        <input
          ref={inputRef}
          id="upload-input"
          className="visually-hidden"
          type="file"
          multiple
          accept={ACCEPT_ATTRIBUTE}
          tabIndex={-1}
          onChange={onChoose}
        />
        <p className="muted">PDF, PNG, JPG, TIFF, CSV or XLSX, up to 50 MB each.</p>
      </div>

      {entries.length > 0 && (
        <>
          <ul className={styles.uploads} aria-live="polite" aria-label="Uploads">
            {entries.map((entry) => (
              <UploadRow key={entry.key} entry={entry} />
            ))}
          </ul>
          {settled && (
            <div>
              <button
                type="button"
                className="btn"
                onClick={() =>
                  setEntries((current) =>
                    current.filter(
                      (entry) =>
                        entry.state.kind === "waiting" || entry.state.kind === "uploading",
                    ),
                  )
                }
              >
                Clear finished
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function UploadRow({ entry }: { entry: Entry }) {
  const { state } = entry;
  return (
    <li className={styles.upload}>
      <div className={styles.uploadHead}>
        <span className={styles.fileName}>{entry.name}</span>
        <span className="muted">{formatBytes(entry.size)}</span>
        <span className={state.kind === "error" ? "error-text" : "muted"}>
          {uploadStateText(state)}
        </span>
      </div>
      {state.kind === "uploading" && (
        <div className={styles.progressRow}>
          <progress
            className={styles.progress}
            value={state.fraction}
            max={1}
            aria-label={`Upload progress for ${entry.name}`}
          />
          <span className="muted" aria-hidden="true">
            {Math.round(state.fraction * 100)}%
          </span>
        </div>
      )}
      {state.kind === "done" && (
        <span>
          <Link href={`/documents/${state.documentId}`}>Open</Link>
        </span>
      )}
      {state.kind === "duplicate" && (
        <span>
          This file was uploaded before, as “{state.existingName}”. Nothing new was stored.{" "}
          <Link href={`/documents/${state.documentId}`}>Open it</Link>
        </span>
      )}
      {state.kind === "error" && <span className="error-text">{state.message}</span>}
    </li>
  );
}
