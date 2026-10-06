"use client";

// Disable or enable one organisation. Disabling signs its users out and stops
// its jobs, so it asks for a fresh code from the operator's own authenticator
// (step-up; the code used to sign in does not work again). Enabling needs no
// code. A refusal is said in plain words in the dialog; the code is cleared.

import { useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/admin/login/CodeField";
import { codeError, normalizeCode } from "@/app/admin/login/adminSignIn";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { disableOrg, enableOrg } from "@/lib/api";
import type { Organisation } from "@/lib/types";
import { DISABLE_EFFECT, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

const CODE_ID = "org-step-up-code";

export function OrgStatusDialog({
  org,
  onClose,
  onChanged,
}: {
  org: Organisation;
  onClose(): void;
  onChanged(message: string): void;
}) {
  const disabling = org.status === "active";
  const [code, setCode] = useState("");
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const problem = disabling ? codeError(code) : null;
    setCodeProblem(problem);
    if (problem) {
      document.getElementById(CODE_ID)?.focus();
      return;
    }
    setBusy(true);
    try {
      await (disabling ? disableOrg(org.id, normalizeCode(code)) : enableOrg(org.id));
      onChanged(`${disabling ? "Disabled" : "Enabled"} ${org.name}.`);
    } catch (caught) {
      setFailure(operatorErrorMessage(caught));
      setCode("");
      setBusy(false);
    }
  }

  const action = disabling ? "Disable organisation" : "Enable organisation";
  return (
    <ActionDialog title={`${disabling ? "Disable" : "Enable"} ${org.name}?`} onClose={onClose}>
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <p>{disabling ? DISABLE_EFFECT : "Its users can sign in again. Their old sessions stay ended."}</p>
        {disabling && (
          <CodeField
            id={CODE_ID}
            label="A new 6-digit code from your app"
            value={code}
            onChange={setCode}
            error={codeProblem}
            autofocus
            hint="Wait for a new code: the one you signed in with does not work again."
          />
        )}
        {failure && (
          <p className="error-text" role="alert">
            {failure}
          </p>
        )}
        <div className={styles.buttons}>
          <button type="button" className="btn" onClick={onClose} data-autofocus={disabling ? undefined : true}>
            Cancel
          </button>
          <button
            type="submit"
            className={disabling ? "btn btn-danger" : "btn btn-primary"}
            disabled={busy}
            aria-busy={busy || undefined}
          >
            {busy && <span className="spinner" aria-hidden="true" />}
            {action}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
