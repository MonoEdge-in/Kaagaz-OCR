"use client";

// The document page's Versions (File management V12, V9, V10): every version of the family,
// newest first, each with its number, note (editable), who uploaded it and when, how it
// joined (uploaded, or linked from a suggestion), a Current chip, and View, Compare and
// Make current. Notes are plain text. With two versions or more, each row's ⋯ menu offers Make
// it a separate file (#521, SeparateDialog).
// #648 (V13, V16), for Admins and Management (`canLink`): "Add an older or newer version" opens the
// link dialog with this document chosen, and "Undo linking" undoes the family's newest link while
// it can (`version_link.can_undo`), starting the company sign-in when the server asks.

import Link from "next/link";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { RowMenu } from "@/components/admin/RowMenu";
import { sortedVersions } from "@/components/viewer/versions";
import { withProof } from "@/components/access/proofStep";
import { ApiError, makeCurrent, undoLinkVersions, updateDocument } from "@/lib/api";
import { withFrom } from "@/lib/backLink";
import { formatDateTime } from "@/lib/format";
import type { DocumentDetail, DocumentVersion } from "@/lib/types";
import { LINK_TEXT, fromSummary, linkErrorText, undoneText } from "./link";
import { LinkDialog } from "./LinkDialog";
import { SeparateDialog } from "./SeparateDialog";
import { compareHref } from "./VersionChoice";
import { NOTE_MAX, VERSION_TEXT, canMakeCurrent, cleanNote, joinedText } from "./words";
import styles from "./Versions.module.css";

export function VersionsPanel({
  detail,
  onChanged,
  canLink = false,
}: {
  detail: DocumentDetail;
  onChanged(message: string, undo?: () => Promise<unknown>): void;
  canLink?: boolean;
}) {
  const versions = sortedVersions(detail.versions).reverse();
  const inTrash = detail.deleted_at !== null;
  const currentSeen = versions.some((version) => version.is_current); // PRD 9 F-07: the list holds only versions seen
  const familyName = versions.find((version) => version.is_current)?.display_name ?? detail.display_name;
  const [separating, setSeparating] = useState<DocumentVersion | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const noteId = useId();
  const linkable = canLink && !inTrash && detail.locked !== true;
  const canUndoLink = canLink && !inTrash && detail.version_link?.can_undo === true;

  async function undoLink() {
    setBusy("link");
    setError(null);
    try {
      const reply = await withProof(() => undoLinkVersions(detail.id));
      if (reply !== null) onChanged(undoneText(reply)); // null: the company sign-in was started
    } catch (caught) {
      setError(linkErrorText(caught));
    } finally {
      setBusy(null);
    }
  }

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
      {!canLink && !inTrash && detail.locked !== true && (
        <div className={styles.versionActions}>
          {/* #690: not hidden without a word; who may link is unchanged (mayLinkVersions). */}
          <button type="button" className="btn" disabled aria-describedby={`${noteId}-link`}>
            {LINK_TEXT.addVersion}
          </button>
          <p id={`${noteId}-link`} className={styles.bannerText}>
            {LINK_TEXT.onlyManagers}
          </p>
        </div>
      )}
      {(linkable || canUndoLink) && (
        <div className={styles.versionActions}>
          {linkable && (
            <button type="button" className="btn" onClick={() => setLinking(true)}>
              {LINK_TEXT.addVersion}
            </button>
          )}
          {canUndoLink && (
            <button type="button" className="btn" disabled={busy !== null} onClick={() => void undoLink()}>
              {LINK_TEXT.undoLink}
            </button>
          )}
        </div>
      )}
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
                <Link className="btn btn-quiet" href={withFrom(`/documents/${version.id}`, `/documents/${encodeURIComponent(detail.id)}`)}>
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
              {canMakeCurrent(version, inTrash, currentSeen) && (
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
              {/* Never version 1: its id is its family's (review, 6 Nov 2026; 409 first_version). */}
              {!inTrash && versions.length > 1 && version.id !== detail.family_id && (
                <RowMenu
                  compact
                  name={`version ${version.version_no}`}
                  items={[{ label: VERSION_TEXT.separate, onSelect: () => setSeparating(version) }]}
                />
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
      {linking && (
        <LinkDialog
          files={[fromSummary(detail)]}
          start="pick"
          fixed={detail.id}
          onClose={() => setLinking(false)}
          onLinked={(done) => {
            setLinking(false);
            onChanged(done.message, done.undo);
          }}
        />
      )}
      {separating && (
        <SeparateDialog
          version={separating}
          familyName={familyName}
          onClose={() => setSeparating(null)}
          onDone={(done) => {
            setSeparating(null);
            onChanged(done.message, done.undo);
          }}
        />
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
