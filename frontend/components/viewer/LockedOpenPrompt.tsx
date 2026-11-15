"use client";

// Opening locked files (PRD 7 section 6, decision 5): the person's own Kaagaz password, never a
// two-step code; one check opens every locked file they may open for 10 minutes in this browser.
// A person who signs in with Google only (`via` "google", or the server's 409
// google_confirm_required) confirms with a fresh Google sign-in instead: Google comes back to
// this page (`next`), with ?open_error= when it did not work. A wrong password says so; five in
// a row block opening for 15 minutes (429 open_blocked, with when to try again).

import { useState } from "react";
import type { FormEvent } from "react";
import { PasswordField } from "@/components/ui/PasswordField";
import { confirmWithGoogle, openLocked } from "@/lib/api";
import type { LockedOpen } from "@/lib/types";
import { LOCKED_TEXT, openErrorText, openFailure } from "./locked";
import styles from "./Locked.module.css";

export interface LockedOpenPromptProps {
  via: LockedOpen["via"];
  next: string; // this page's path, where Google comes back to
  openError: string | null; // ?open_error= after the Google confirm
  onOpened(state: LockedOpen): void;
}

export function LockedOpenPrompt({ via, next, openError, onOpened }: LockedOpenPromptProps) {
  const [google, setGoogle] = useState(via === "google");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(() => openErrorText(openError));

  async function open(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const state = await openLocked(password);
      onOpened(state);
    } catch (caught) {
      const failure = openFailure(caught, Date.now());
      setGoogle(failure.google);
      setError(failure.message || null);
      setPassword("");
      setBusy(false);
    }
  }

  async function goToGoogle() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await confirmWithGoogle(next);
      window.location.href = url; // Google's sign-in; it comes back to this page
    } catch (caught) {
      setError(openFailure(caught, Date.now()).message);
      setBusy(false);
    }
  }

  const alert = error && (
    <p className="error-text" role="alert">
      {error}
    </p>
  );
  if (google) {
    return (
      <div className={styles.prompt}>
        <h2>{LOCKED_TEXT.heading}</h2>
        <p>{LOCKED_TEXT.googlePrompt}</p>
        {alert}
        <div>
          <button type="button" className="btn btn-primary" onClick={() => void goToGoogle()} disabled={busy}>
            {busy ? LOCKED_TEXT.goingToGoogle : LOCKED_TEXT.google}
          </button>
        </div>
      </div>
    );
  }
  return (
    <form className={styles.prompt} onSubmit={(event) => void open(event)}>
      <h2>{LOCKED_TEXT.heading}</h2>
      <p>{LOCKED_TEXT.prompt}</p>
      <PasswordField
        id="locked-open-password"
        label={LOCKED_TEXT.password}
        autoComplete="current-password"
        value={password}
        onChange={setPassword}
        enterKeyHint="go"
      />
      {alert}
      <div>
        <button type="submit" className="btn btn-primary" disabled={busy || password === ""}>
          {busy ? LOCKED_TEXT.opening : LOCKED_TEXT.open}
        </button>
      </div>
    </form>
  );
}
