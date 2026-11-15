"use client";

// Giving a place that holds locked files (PRD 7 section 6): when an Admin gives a person a
// department or a private folder, a pop-up lists its locked files with checkboxes, so the Admin
// chooses which ones that person may open. Ticked: they may; unticked: they still get the rest
// of the place. Names show because Admins see names, never contents; past SEARCH_FROM files a
// search box narrows the list. The files the person may open already come ticked, so the same
// pop-up serves the Access page's [Change]. It only chooses: `onGive` gets the ticked ids, and
// the caller sends them with the membership or named-list write (PR 10a's routes, locked_files).

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { BusyLabel } from "@/components/documents/BusyLabel";
import libraryStyles from "@/components/documents/Library.module.css";
import {
  SEARCH_FROM,
  chosenText,
  giveIntroText,
  lockFailureText,
  lockedFileLabel,
  matchingFiles,
  noneChosenText,
} from "@/components/viewer/locked";
import styles from "@/components/viewer/Locked.module.css";
import { departmentLockedFiles, folderLockedFiles } from "@/lib/api";
import type { PlaceLockedFiles } from "@/lib/types";

export interface LockedFilesPickerProps {
  person: { id: string; name: string };
  place: { kind: "department" | "folder"; id: string; name: string };
  head?: boolean; // a department's Head: its private folders' locked files too
  onGive(lockedFiles: string[]): Promise<void>;
  onClose(): void;
}

export function LockedFilesPicker({ person, place, head, onGive, onClose }: LockedFilesPickerProps) {
  const [loaded, setLoaded] = useState<PlaceLockedFiles | null>(null);
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load =
      place.kind === "department" ? departmentLockedFiles(place.id, person.id, head) : folderLockedFiles(place.id, person.id);
    load.then(
      (found) => {
        if (cancelled) return;
        setLoaded(found);
        setTicked(new Set(found.locked.filter((file) => file.open).map((file) => file.id)));
      },
      (caught: unknown) => {
        if (!cancelled) setError(lockFailureText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [place.kind, place.id, person.id, head]);

  function tick(id: string) {
    setTicked((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function give() {
    setBusy(true);
    setError(null);
    try {
      await onGive([...ticked]);
    } catch (caught) {
      setError(lockFailureText(caught));
      setBusy(false);
    }
  }

  const locked = loaded?.locked ?? [];
  const shown = matchingFiles(locked, search);
  return (
    <ActionDialog title="Give access" onClose={onClose} canClose={() => !busy}>
      <div className={libraryStyles.dialogForm}>
        {loaded === null ? (
          !error && <p className="muted">Loading the locked files…</p>
        ) : (
          <>
            <p>{giveIntroText(person.name, place.name, loaded.files, locked.length)}</p>
            {locked.length > SEARCH_FROM && (
              <input
                type="search"
                aria-label="Find a locked file by name"
                placeholder="Find a locked file"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            )}
            {locked.length > 0 && (
              <>
            <ul className={styles.ticks}>
              {shown.map((file) => (
                <li key={file.id}>
                  <label>
                    <input type="checkbox" checked={ticked.has(file.id)} onChange={() => tick(file.id)} />
                    {lockedFileLabel(file)}
                  </label>
                </li>
              ))}
            </ul>
            <div className={styles.tickBar}>
              <button type="button" className="btn btn-quiet" onClick={() => setTicked(new Set(locked.map((f) => f.id)))}>
                Select all
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => setTicked(new Set())}>
                Clear all
              </button>
              <span aria-live="polite">{chosenText(ticked.size, locked.length)}</span>
            </div>
            {ticked.size === 0 && <p>{noneChosenText(person.name, loaded.files - locked.length)}</p>}
              </>
            )}
          </>
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className={libraryStyles.formButtons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void give()} disabled={busy || loaded === null}>
            <BusyLabel busy={busy} idle="Give access" working="Giving…" />
          </button>
        </div>
      </div>
    </ActionDialog>
  );
}
