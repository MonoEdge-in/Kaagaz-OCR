"use client";

// The new-version banner on a document (File management V6, V7; #521): 'This looks like a new
// version of "<name>" (86% the same text).', or the name and identifier in plain words when
// they led (NB-17), an optional note (V9), Upload as a new version, Upload as a new file and See
// the differences (VersionChoice). The choice's words go to the page's toast, with Undo after a
// version. After a link (by a person, or on its own before #521) it says so, with Undo while
// this is still the family's newest version; a refused Undo (409 not_newest) is said calmly.
// The states are worked out in words.ts (bannerState).

import { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, undoVersionLink } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";
import { VersionChoice } from "./VersionChoice";
import { VERSION_TEXT, bannerState } from "./words";
import styles from "./Versions.module.css";

export { compareHref } from "./VersionChoice";

export function VersionBanner({
  detail,
  onChanged,
}: {
  detail: DocumentDetail;
  onChanged(message: string, undo?: () => Promise<unknown>): void;
}) {
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

  async function undo() {
    setBusy(true);
    setError(null);
    try {
      await undoVersionLink(detail.id);
      setDone(VERSION_TEXT.undone);
      onChanged(VERSION_TEXT.undone);
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
            <button type="button" className="btn" disabled={busy} onClick={() => void undo()}>
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

  return (
    <div className={styles.banner}>
      <VersionChoice suggestion={suggestion} withNote onDone={(chosen) => onChanged(chosen.message, chosen.undo)} />
    </div>
  );
}
