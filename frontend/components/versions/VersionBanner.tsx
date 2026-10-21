"use client";

// The new-version banner on a document (File management V6, V7): "This looks like a new
// version of <name>" with the reasons in plain words, an optional note (V9), and Make it
// version N, Keep separate and Compare first. After a link (by a person or on its own) it
// says so, with Undo while this is still the family's newest version; a refused Undo (409
// not_newest) is said calmly. The states are worked out in words.ts (bannerState).

import Link from "next/link";
import { useId, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, acceptSuggestion, rejectSuggestion, undoVersionLink } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";
import { NOTE_MAX, VERSION_TEXT, bannerState, bannerTitle, cleanNote, reasonsText } from "./words";
import styles from "./Versions.module.css";

/** The address of the Compare view for two documents (V11). */
export const compareHref = (id: string, withId: string) =>
  `/documents/${encodeURIComponent(id)}/compare?with=${encodeURIComponent(withId)}`;

export function VersionBanner({
  detail,
  onChanged,
}: {
  detail: DocumentDetail;
  onChanged(message: string): void;
}) {
  const id = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const state = bannerState(detail);
  if (done !== null) {
    return (
      <div className={styles.banner} role="status">
        <p className={styles.bannerText}>{done}</p>
      </div>
    );
  }
  if (state.kind === "none" || detail.deleted_at !== null) return null;
  const { suggestion } = state;
  const cleaned = cleanNote(note);

  async function act(work: () => Promise<unknown>, said: string) {
    setBusy(true);
    setError(null);
    try {
      await work();
      setDone(said);
      onChanged(said);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === "not_newest"
          ? VERSION_TEXT.notNewest
          : caught instanceof ApiError
            ? caught.message
            : "Check your connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "linked") {
    const said =
      suggestion.outcome === "auto"
        ? VERSION_TEXT.linkedOnItsOwn(detail.version_no, suggestion.candidate.name)
        : VERSION_TEXT.linked(detail.version_no, suggestion.candidate.name);
    return (
      <div className={styles.banner} role="region" aria-label={VERSION_TEXT.versions}>
        <p className={styles.bannerText}>{said}</p>
        {state.canUndo && (
          <div className={styles.bannerButtons}>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void act(() => undoVersionLink(detail.id), VERSION_TEXT.undone)}
            >
              <Icon name="history" />
              {VERSION_TEXT.undo}
            </button>
          </div>
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  const reasons = reasonsText(suggestion);
  return (
    <div className={styles.banner} role="region" aria-labelledby={`${id}-title`}>
      <p id={`${id}-title`} className={styles.bannerTitle}>
        {bannerTitle(suggestion.candidate.name)}
      </p>
      {reasons && <p className={styles.bannerText}>{`${reasons.charAt(0).toUpperCase()}${reasons.slice(1)}.`}</p>}
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
      <div className={styles.bannerButtons}>
        <button
          type="button"
          className={`btn ${styles.choice}`}
          disabled={busy || cleaned.problem !== null}
          onClick={() =>
            void act(
              () => acceptSuggestion(suggestion.id, cleaned.note ?? undefined),
              VERSION_TEXT.linked(suggestion.next_version_no, suggestion.candidate.name),
            )
          }
        >
          {VERSION_TEXT.makeIt(suggestion.next_version_no)}
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => void act(() => rejectSuggestion(suggestion.id), VERSION_TEXT.kept)}
        >
          {VERSION_TEXT.keepSeparate}
        </button>
        <Link className="btn" href={compareHref(detail.id, suggestion.candidate.id)}>
          {VERSION_TEXT.compareFirst}
        </Link>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
