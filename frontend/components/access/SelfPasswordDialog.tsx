"use client";

// "Confirm it's you": the admin types their own password to give themselves access (PRD 7
// decision 11). A refused password keeps the dialog open, says why under the box and puts focus
// back in it; any other failure closes it and goes to the caller; Cancel sends nothing. Screens
// use it through usePasswordStep: `run(what, send, first)` and render `dialog`.

import { useCallback, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { useOptionalSession } from "@/lib/session";
import { beginKeycloakReauth, isKeycloakManaged, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";
import { passwordRetryText, stepAttempt, withPassword } from "./passwordStep";
import { PASSWORD_BUTTON, PASSWORD_LABEL, PASSWORD_TITLE, selfGrantText } from "./words";

export function SelfPasswordDialog({
  what,
  attempt,
  onCancel,
}: {
  what: string;
  /** Sends with the password; throws a refused password's error to stay open. */
  attempt(password: string): Promise<void>;
  onCancel(): void;
}) {
  const id = useId();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || password === "") return;
    setBusy(true);
    setError(null);
    try {
      await attempt(password);
    } catch (caught) {
      setError(passwordRetryText(caught));
      setPassword("");
      setBusy(false);
      document.getElementById(id)?.focus();
    }
  }

  return (
    <ActionDialog title={PASSWORD_TITLE} onClose={onCancel} canClose={() => !busy}>
      <form className="stack" onSubmit={submit} noValidate>
        <p>{selfGrantText(what)}</p>
        <PasswordField
          id={id}
          label={PASSWORD_LABEL}
          autoComplete="current-password"
          value={password}
          onChange={setPassword}
          error={error}
          enterKeyHint="go"
        />
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || password === ""} aria-busy={busy || undefined}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {PASSWORD_BUTTON}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}

interface OpenStep {
  key: number;
  what: string;
  attempt(password: string): Promise<void>;
  cancel(): void;
}

/** `run(what, send, first)`: sends `send`, through the password step when `first` (a self-grant
 * the screen knows of) or when the server asks for it; resolves with its reply, or null when the
 * admin cancelled. `dialog`: render it where the screen keeps its dialogs. */
export function usePasswordStep() {
  const me = useOptionalSession()?.me ?? null;
  const [open, setOpen] = useState<OpenStep | null>(null);
  // One step at a time: a second ask waits for the first to close, so the dialog never says the
  // wrong "access to …".
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const steps = useRef(0);

  const run = useCallback(
    async <T,>(what: string, send: (password?: string) => Promise<T>, first: boolean): Promise<T | null> => {
      const ask = <R,>(attempt: (password: string) => Promise<R>) => {
        const turn = queue.current.then(
          () =>
            new Promise<R | null>((resolve, reject) => {
              const close = () => setOpen(null);
              steps.current += 1;
              setOpen({
                key: steps.current,
                what,
                attempt: stepAttempt(attempt, { close, resolve, reject }),
                cancel: () => {
                  close();
                  resolve(null);
                },
              });
            }),
        );
        queue.current = turn.catch(() => undefined);
        return turn;
      };
      try {
        if (isKeycloakManaged(me)) return await send();
        return await withPassword(send, ask, first);
      } catch (caught) {
        if (!isKeycloakReauthRequired(caught)) throw caught;
        try {
          await beginKeycloakReauth("access", window.location.pathname);
          return null;
        } catch (failed) {
          throw new Error(keycloakStartFailure(failed));
        }
      }
    },
    [me],
  );

  // A new key each step: nothing typed for one step is left in the next.
  const dialog = open && <SelfPasswordDialog key={open.key} what={open.what} attempt={open.attempt} onCancel={open.cancel} />;
  return { run, dialog };
}
