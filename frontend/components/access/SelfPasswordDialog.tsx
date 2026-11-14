"use client";

// "Confirm it's you": the admin types their own password to give themselves access (PRD 7
// decision 11). A refused password keeps the dialog open, says why under the box and puts focus
// back in it; any other failure closes it and goes to the caller; Cancel sends nothing. Screens
// use it through usePasswordStep: `run(what, send, first)` and render `dialog`.

import { useCallback, useId, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { passwordRetryText, withPassword } from "./passwordStep";
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
  what: string;
  attempt(password: string): Promise<void>;
  cancel(): void;
}

/** `run(what, send, first)`: sends `send`, through the password step when `first` (a self-grant
 * the screen knows of) or when the server asks for it; resolves with its reply, or null when the
 * admin cancelled. `dialog`: render it where the screen keeps its dialogs. */
export function usePasswordStep() {
  const [open, setOpen] = useState<OpenStep | null>(null);

  const run = useCallback(
    <T,>(what: string, send: (password?: string) => Promise<T>, first: boolean) =>
      withPassword(
        send,
        <R,>(attempt: (password: string) => Promise<R>) =>
          new Promise<R | null>((resolve, reject) => {
            setOpen({
              what,
              attempt: async (password) => {
                let reply: R;
                try {
                  reply = await attempt(password);
                } catch (caught) {
                  if (passwordRetryText(caught) !== null) throw caught; // the dialog stays open
                  setOpen(null);
                  reject(caught);
                  return;
                }
                setOpen(null);
                resolve(reply);
              },
              cancel: () => {
                setOpen(null);
                resolve(null);
              },
            });
          }),
        first,
      ),
    [],
  );

  const dialog = open && <SelfPasswordDialog what={open.what} attempt={open.attempt} onCancel={open.cancel} />;
  return { run, dialog };
}
