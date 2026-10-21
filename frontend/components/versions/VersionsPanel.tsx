"use client";

// The document page's Versions (File management V12, V9, V10): every version of the family,
// newest first, each with its number, note (editable), who uploaded it and when, how it
// joined (uploaded, or linked from a suggestion), a Current chip, and View, Compare and
// Make current. Notes are plain text.

import Link from "next/link";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { sortedVersions } from "@/components/viewer/versions";
import { ApiError, makeCurrent, updateDocument } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { DocumentDetail, DocumentVersion } from "@/lib/types";
import { compareHref } from "./VersionBanner";
import { NOTE_MAX, VERSION_TEXT, canMakeCurrent, cleanNote, joinedText } from "./words";
import styles from "./Versions.module.css";

export function VersionsPanel({ detail, onChanged }: { detail: DocumentDetail; onChanged(message: string): void }) {
  const versions = sortedVersions(detail.versions).reverse();
  const inTrash = detail.deleted_at !== null;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  async function act(versionId: string, work: () => Promise<unknown>, said: string) {
    setBusy(versionId);
    setError(null);
    try {
      await work();
      setEditing(null);
      onChanged(said);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="versions-heading">
      <h2 id="versions-heading" className="visually-hidden">
        {VERSION_TEXT.versions}
      </h2>
      {versions.length < 2 && <p className="muted">{VERSION_TEXT.onlyOne}</p>}
      <ol className={styles.versions}>
        {versions.map((version) => (
          <li key={version.id} className={styles.version} aria-current={version.id === detail.id ? "true" : undefined}>
            <div className={styles.versionHead}>
              <span className={styles.versionNo}>{`Version ${version.version_no}`}</span>
              {version.is_current && <span className="status-chip" data-tone="ready">{VERSION_TEXT.current}</span>}
              <span className={styles.versionMeta}>
                {joinedText(version.joined)}
                {version.uploaded_by?.name ? ` by ${version.uploaded_by.name}` : ""}
                {" · "}
                <time dateTime={version.created_at}>{formatDateTime(version.created_at)}</time>
              </span>
            </div>
            {editing === version.id ? (
              <NoteForm
                version={version}
                busy={busy === version.id}
                onCancel={() => setEditing(null)}
                onSave={(note) =>
                  void act(version.id, () => updateDocument(version.id, { version_note: note }), VERSION_TEXT.noteSaved)
                }
              />
            ) : (
              <p className={styles.note}>
                <span className="visually-hidden">{`${VERSION_TEXT.note}: `}</span>
                {version.version_note ?? <span className="muted">{VERSION_TEXT.noNote}</span>}
              </p>
            )}
            <div className={styles.versionActions}>
              {version.id !== detail.id && (
                <Link className="btn btn-quiet" href={`/documents/${version.id}`}>
                  {VERSION_TEXT.view}
                  <span className="visually-hidden">{` version ${version.version_no}`}</span>
                </Link>
              )}
              {version.id !== detail.id && (
                <Link className="btn btn-quiet" href={compareHref(detail.id, version.id)}>
                  {VERSION_TEXT.compare}
                  <span className="visually-hidden">{` with version ${version.version_no}`}</span>
                </Link>
              )}
              {canMakeCurrent(version, inTrash) && (
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={busy !== null}
                  onClick={() =>
                    void act(version.id, () => makeCurrent(version.id), VERSION_TEXT.madeCurrent(version.version_no))
                  }
                >
                  {VERSION_TEXT.makeCurrent}
                  <span className="visually-hidden">{`: version ${version.version_no}`}</span>
                </button>
              )}
              {!inTrash && editing !== version.id && (
                <button type="button" className="btn btn-quiet" onClick={() => setEditing(version.id)}>
                  {version.version_note ? VERSION_TEXT.editNote : VERSION_TEXT.addNote}
                  <span className="visually-hidden">{` for version ${version.version_no}`}</span>
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function NoteForm({
  version,
  busy,
  onSave,
  onCancel,
}: {
  version: DocumentVersion;
  busy: boolean;
  onSave(note: string | null): void;
  onCancel(): void;
}) {
  const id = useId();
  const [text, setText] = useState(version.version_note ?? "");
  const { note, problem } = cleanNote(text);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!problem) onSave(note);
  }
  return (
    <form className={styles.noteForm} onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor={id}>{`${VERSION_TEXT.note} for version ${version.version_no}`}</label>
        <input
          id={id}
          className="input"
          value={text}
          maxLength={NOTE_MAX + 20}
          autoComplete="off"
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-error` : undefined}
          onChange={(event) => setText(event.target.value)}
        />
        {problem && (
          <p id={`${id}-error`} className="error-text" role="alert">
            {problem}
          </p>
        )}
      </div>
      <button type="button" className="btn" onClick={onCancel}>
        Cancel
      </button>
      <button type="submit" className="btn" disabled={busy || problem !== null}>
        Save
      </button>
    </form>
  );
}
