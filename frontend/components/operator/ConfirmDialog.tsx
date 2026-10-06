"use client";

// One change on the operator screens, confirmed in a dialog: disable, enable,
// delete or cancel the deletion of an organisation; disable, enable, unlock or
// reset the two-step code of one of its admins. What the change does is said
// first. A change that takes something away asks for a fresh code from the
// operator's own authenticator (step-up; the code used to sign in does not
// work again); deleting also asks for the organisation's name, typed exactly.
// A refusal is said in plain words in the dialog, and the code is cleared.
// It cannot close while busy; after a change the focus goes to the row's name.

import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/admin/login/CodeField";
import { codeError, normalizeCode } from "@/app/admin/login/adminSignIn";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { canCloseDialog, nameError, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

export interface Confirm {
  title: string;
  /** What the change does, in plain words. */
  effect: string;
  /** The button's words, and what the status line says after ("Disabled Sharma Traders."). */
  action: string;
  done: string;
  danger: boolean;
  stepUp: boolean;
  /** Deleting: this name must be typed exactly. */
  typeName?: string;
  /** Where the focus goes after the change: the row's name. */
  focusId: string;
  run(code: string, name: string): Promise<unknown>;
}

const CODE_ID = "step-up-code";
const NAME_ID = "confirm-name";

export function ConfirmDialog({ confirm, onClose, onDone }: { confirm: Confirm; onClose(): void; onDone(message: string): void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [problems, setProblems] = useState<{ name: string | null; code: string | null }>({ name: null, code: null });
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changed = useRef(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = {
      name: confirm.typeName === undefined ? null : nameError(name, confirm.typeName),
      code: confirm.stepUp ? codeError(code) : null,
    };
    setProblems(found);
    if (found.name || found.code) {
      document.getElementById(found.name ? NAME_ID : CODE_ID)?.focus();
      return;
    }
    setBusy(true);
    try {
      await confirm.run(normalizeCode(code), name);
      changed.current = true;
      onDone(confirm.done);
    } catch (caught) {
      setFailure(operatorErrorMessage(caught));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <ActionDialog
      title={confirm.title}
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy }, way)}
      onClosed={() => changed.current && document.getElementById(confirm.focusId)?.focus()}
    >
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <p>{confirm.effect}</p>
        {confirm.typeName !== undefined && (
          <div className="field">
            <label htmlFor={NAME_ID}>Type the organisation name</label>
            <input
              id={NAME_ID}
              className="input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              data-autofocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={problems.name ? true : undefined}
              aria-describedby={problems.name ? `${NAME_ID}-error` : undefined}
            />
            {problems.name && (
              <span id={`${NAME_ID}-error`} className="error-text">
                {problems.name}
              </span>
            )}
          </div>
        )}
        {confirm.stepUp && (
          <CodeField
            id={CODE_ID}
            label="A new 6-digit code from your app"
            value={code}
            onChange={setCode}
            error={problems.code}
            autofocus={confirm.typeName === undefined}
            hint="Wait for a new code: the one you signed in with does not work again."
          />
        )}
        {failure && (
          <p className="error-text" role="alert">
            {failure}
          </p>
        )}
        <div className={styles.buttons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy} data-autofocus={!confirm.stepUp || undefined}>
            Cancel
          </button>
          <button
            type="submit"
            className={confirm.danger ? "btn btn-danger" : "btn btn-primary"}
            disabled={busy}
            aria-busy={busy || undefined}
          >
            {busy && <span className="spinner" aria-hidden="true" />}
            {confirm.action}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
