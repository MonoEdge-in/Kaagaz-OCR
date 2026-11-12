"use client";

// A near match's question (#521, Pattern A): 'This looks like a new version of "X" (86% the same
// text).' and two buttons, Upload as a new version (Accept, V6) and Upload as a new file (Keep
// separate: the pair is never asked about again), with See the differences. The same name
// highlights the version, another name the file. Used by the document's banner (with the
// version's note, V9), the upload tray's row and the compare screen (no link there: it is it).

import Link from "next/link";
import { useId, useState } from "react";
import { ApiError, acceptSuggestion, rejectSuggestion, undoVersionLink } from "@/lib/api";
import type { VersionSuggestion } from "@/lib/types";
import { NOTE_MAX, VERSION_TEXT, bannerTitle, cleanNote, highlightVersion, reasonsText } from "./words";
import styles from "./Versions.module.css";

/** The address of the compare screen for two documents (V11, #521 See the differences). */
export const compareHref = (id: string, withId: string) =>
  `/documents/${encodeURIComponent(id)}/compare?with=${encodeURIComponent(withId)}`;

/** What a choice did, in words, and how to undo it (after Upload as a new version only). */
export interface Chosen {
  message: string;
  undo?: () => Promise<unknown>;
}

/** Upload as a new version: the suggestion accepted, with its note (V9). */
export async function chooseVersion(suggestion: VersionSuggestion, note?: string): Promise<Chosen> {
  await acceptSuggestion(suggestion.id, note);
  return {
    message: VERSION_TEXT.linked(suggestion.next_version_no, suggestion.candidate.name),
    undo: () => undoVersionLink(suggestion.document.id),
  };
}

/** Upload as a new file: the pair kept apart for good. */
export async function chooseFile(suggestion: VersionSuggestion): Promise<Chosen> {
  await rejectSuggestion(suggestion.id);
  return { message: VERSION_TEXT.kept };
}

export function VersionChoice({
  suggestion,
  onDone,
  withNote = false,
  showCompare = true,
}: {
  suggestion: VersionSuggestion;
  onDone(chosen: Chosen): void;
  withNote?: boolean;
  showCompare?: boolean;
}) {
  const id = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cleaned = cleanNote(note);
  const versionFirst = highlightVersion(suggestion);

  async function act(work: () => Promise<Chosen>) {
    setBusy(true);
    setError(null);
    try {
      onDone(await work());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.choiceBlock} role="group" aria-labelledby={`${id}-title`}>
      <p id={`${id}-title`} className={styles.bannerTitle}>
        {bannerTitle(suggestion.candidate.name, reasonsText(suggestion))}
      </p>
      {withNote && (
        <div className="field">
          <label htmlFor={`${id}-note`}>{VERSION_TEXT.noteLabel}</label>
          <input
            id={`${id}-note`}
            className="input"
            value={note}
            maxLength={NOTE_MAX + 20}
            placeholder={VERSION_TEXT.notePlaceholder}
            autoComplete="off"
            aria-invalid={cleaned.problem ? true : undefined}
            aria-describedby={cleaned.problem ? `${id}-note-error` : undefined}
            onChange={(event) => setNote(event.target.value)}
          />
          {cleaned.problem && (
            <p id={`${id}-note-error`} className="error-text" role="alert">
              {cleaned.problem}
            </p>
          )}
        </div>
      )}
      <div className={styles.bannerButtons}>
        <button
          type="button"
          className={versionFirst ? `btn ${styles.choice}` : "btn"}
          disabled={busy || cleaned.problem !== null}
          data-highlight={versionFirst ? "true" : "false"}
          onClick={() => void act(() => chooseVersion(suggestion, cleaned.note ?? undefined))}
        >
          {VERSION_TEXT.asVersion}
        </button>
        <button
          type="button"
          className={versionFirst ? "btn" : `btn ${styles.choice}`}
          disabled={busy}
          data-highlight={versionFirst ? "false" : "true"}
          onClick={() => void act(() => chooseFile(suggestion))}
        >
          {VERSION_TEXT.asFile}
        </button>
        {showCompare && (
          <Link className="btn btn-quiet" href={compareHref(suggestion.document.id, suggestion.candidate.id)}>
            {VERSION_TEXT.seeDifferences}
          </Link>
        )}
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
