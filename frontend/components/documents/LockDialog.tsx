"use client";

// Lock and Unlock (PRD 7 section 6, Admins, PR 13's screens on PR 12's routes). The lock dialog
// lists everyone who can see the file (or any of the selection's files) today, unticked, and the
// Admin locking it ticked by default (decision 4); a fresh company sign-in covers the whole
// batch (403 keycloak_reauth_required starts it). A bulk lock is all or nothing: a 409 names each file it refused, and why. Unlocking
// warns that everyone who can see its folder can open it again, and Ask can use it again.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { BusyLabel } from "@/components/documents/BusyLabel";
import { ActionDialog } from "@/components/documents/ActionDialog";
import libraryStyles from "@/components/documents/Library.module.css";
import {
  LOCK_TEXT,
  lockExplainText,
  lockFailureText,
  lockTitle,
  lockedMessage,
  refusalsOf,
  refusedLines,
} from "@/components/viewer/locked";
import styles from "@/components/viewer/Locked.module.css";
import { lockDocument, lockDocuments, lockPreview, lockPreviewMany, unlockDocument } from "@/lib/api";
import { beginKeycloakReauth, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";
import type { LockPerson } from "@/lib/types";

interface Named {
  id: string;
  display_name: string;
}

export function LockDialog({
  documents,
  onClose,
  onClosed,
  onLocked,
}: {
  documents: readonly Named[]; // one from the document's menu, or the list's selection
  onClose(): void;
  onClosed?(): void;
  onLocked(message: string): void;
}) {
  const [people, setPeople] = useState<LockPerson[] | null>(null);
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const ids = documents.map((each) => each.id).join(",");

  useEffect(() => {
    let cancelled = false;
    const wanted = ids.split(",");
    const [first] = wanted;
    (wanted.length === 1 && first ? lockPreview(first) : lockPreviewMany(wanted)).then(
      ({ people: found }) => {
        if (cancelled) return;
        setPeople(found);
        setTicked(new Set(found.filter((person) => person.you).map((person) => person.id)));
      },
      (caught: unknown) => {
        if (!cancelled) setErrors([lockFailureText(caught)]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ids]);

  function tick(id: string) {
    setTicked((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function lock(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setErrors([]);
    const openTo = [...ticked];
    const [only] = documents;
    try {
      if (documents.length === 1 && only) {
        await lockDocument(only.id, openTo);
        onLocked(lockedMessage(1));
      } else {
        onLocked(lockedMessage((await lockDocuments(ids.split(","), openTo)).locked));
      }
    } catch (caught) {
      if (isKeycloakReauthRequired(caught)) {
        try {
          await beginKeycloakReauth("locks", window.location.pathname);
          return;
        } catch (failed) {
          setErrors([keycloakStartFailure(failed)]);
          setBusy(false);
          return;
        }
      }
      const refused = refusalsOf(caught);
      const names = new Map(documents.map((each) => [each.id, each.display_name]));
      setErrors(refused.length > 0 ? refusedLines(refused, names) : [lockFailureText(caught)]);
      setBusy(false);
    }
  }

  return (
    <ActionDialog
      title={lockTitle(documents.map((each) => each.display_name))}
      onClose={onClose}
      onClosed={onClosed}
      canClose={() => !busy}
    >
      <form className={libraryStyles.dialogForm} onSubmit={(event) => void lock(event)}>
        <p>{lockExplainText(documents.length)}</p>
        <p>{LOCK_TEXT.photograph}</p>
        <p className="hint">Company sign-in may ask you to verify. No files change during verification; reopen this dialog, reselect files if needed, and confirm again.</p>
        <fieldset className={styles.tickBar}>
          <legend>{LOCK_TEXT.who}</legend>
          {people === null ? (
            <p className="muted">{LOCK_TEXT.loadingPeople}</p>
          ) : (
            <ul className={styles.ticks}>
              {people.map((person) => (
                <li key={person.id}>
                  <label>
                    <input type="checkbox" checked={ticked.has(person.id)} onChange={() => tick(person.id)} />
                    {person.you ? `${person.name} (you)` : person.name}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        {errors.map((line) => (
          <p key={line} className="error-text" role="alert">
            {line}
          </p>
        ))}
        <div className={libraryStyles.formButtons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            {LOCK_TEXT.cancel}
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || people === null}>
            <BusyLabel busy={busy} idle={LOCK_TEXT.lock(documents.length)} working={LOCK_TEXT.locking} />
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}

export function UnlockDialog({
  document,
  onClose,
  onClosed,
  onUnlocked,
}: {
  document: Named;
  onClose(): void;
  onClosed?(): void;
  onUnlocked(message: string): void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function unlock(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unlockDocument(document.id);
      onUnlocked(LOCK_TEXT.unlocked);
    } catch (caught) {
      if (isKeycloakReauthRequired(caught)) {
        try {
          await beginKeycloakReauth("locks", window.location.pathname);
          return;
        } catch (failed) {
          setError(keycloakStartFailure(failed));
          setBusy(false);
          return;
        }
      }
      setError(lockFailureText(caught));
      setBusy(false);
    }
  }

  return (
    <ActionDialog title={LOCK_TEXT.unlockTitle(document.display_name)} onClose={onClose} onClosed={onClosed} canClose={() => !busy}>
      <form className={libraryStyles.dialogForm} onSubmit={(event) => void unlock(event)}>
        <p>{LOCK_TEXT.unlockWarning}</p>
        <p className="hint">Company sign-in may ask you to verify. No file changes until you return, reopen this dialog, and confirm again.</p>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className={libraryStyles.formButtons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            {LOCK_TEXT.cancel}
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            <BusyLabel busy={busy} idle={LOCK_TEXT.unlock} working={LOCK_TEXT.unlocking} />
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
