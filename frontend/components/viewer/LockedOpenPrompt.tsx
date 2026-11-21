"use client";

// Opening locked files (PRD 7 section 6, decision 5): a fresh company sign-in for "locked_open"
// (Keycloak's own page; the person comes back to this page and chooses Open). One check opens every
// locked file they may open for 10 minutes in this browser. There is no password to type.

import { useState } from "react";
import type { FormEvent } from "react";
import { openLocked } from "@/lib/api";
import { beginKeycloakReauth, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";
import type { LockedOpen } from "@/lib/types";
import { LOCKED_TEXT, openFailure } from "./locked";
import styles from "./Locked.module.css";

export interface LockedOpenPromptProps {
  next: string; // this page's path, where the company sign-in comes back to
  onOpened(state: LockedOpen): void;
}

export function LockedOpenPrompt({ next, onOpened }: LockedOpenPromptProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onOpened(await openLocked());
    } catch (caught) {
      if (isKeycloakReauthRequired(caught)) {
        try {
          await beginKeycloakReauth("locked_open", next);
          return;
        } catch (failed) {
          setError(keycloakStartFailure(failed));
          setBusy(false);
          return;
        }
      }
      setError(openFailure(caught).message || null);
      setBusy(false);
    }
  }

  return (
    <form className={styles.prompt} onSubmit={(event) => void open(event)}>
      <h2>{LOCKED_TEXT.heading}</h2>
      <p>Verify with company sign-in to open locked files. After returning, choose Open again; verification does not open the file by itself.</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Checking…" : "Open"}
        </button>
      </div>
    </form>
  );
}
