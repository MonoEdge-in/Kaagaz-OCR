"use client";

// Account, Two-step sign-in (#457 phase 3), for every role. Anyone turns it on with their
// password, then the set-up step every admin uses (EnrolStep: the QR code or the key, then a
// first code); and off with their password and a current code, after a warning, which signs
// every other browser out. An admin's then stays off until they turn it on again (Tushar,
// 30 Oct 2026); another admin resets an admin's lost code, an admin a member's. Like Change
// password: method="post", no name attributes, the password masked again on submit and never
// trimmed, the code cleared after a refusal. A missing or wrong password or code shows on its
// field, which takes focus (#457 review).

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { LOST_PHONE } from "@/app/(app)/admin/TwoStepPanel";
import { CodeField } from "@/app/login/CodeField";
import { EnrolStep } from "@/app/login/EnrolStep";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import styles from "@/app/login/twoStep.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { startTwoStep, turnOffTwoStep } from "@/lib/api";
import type { Me, TwoStepSetup } from "@/lib/types";
import { CURRENT_MISSING } from "./setPassword";
import {
  LOST_CODE,
  OFF_TEXT,
  OFF_WARNING,
  ON_TEXT,
  TURNED_OFF,
  TURNED_ON,
  proofField,
  twoStepError,
  twoStepView,
} from "./twoStepAccount";

const PASSWORD_ID = "two-step-password";
const CODE_ID = "two-step-code";

type Problems = { password: string | null; code: string | null };

/** onClosed: once it is shut, where focus goes (the account menu's avatar). */
export function TwoStepDialog({
  me,
  onClose,
  onClosed,
  onDone,
}: {
  me: Me;
  onClose(): void;
  onClosed?(): void;
  onDone(message: string): void;
}) {
  const view = twoStepView(me);
  const [password, setPassword] = useState("");
  const [masked, setMasked] = useState(true);
  const [code, setCode] = useState("");
  const [problems, setProblems] = useState<Problems>({ password: null, code: null });
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Turn on, after the password: the set-up key the server sent (nothing is stored yet).
  const [enrol, setEnrol] = useState<TwoStepSetup | null>(null);

  /** Commit the messages first, so the field is described when it gets focus. */
  function showOnFields(found: Problems) {
    flushSync(() => setProblems(found));
    document.getElementById(found.password ? PASSWORD_ID : CODE_ID)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = {
      password: password === "" ? CURRENT_MISSING : null,
      code: view === "on" ? codeError(code) : null,
    };
    if (found.password || found.code) {
      showOnFields(found);
      return;
    }
    setProblems(found);
    setBusy(true);
    try {
      if (view === "on") {
        await turnOffTwoStep(password, normalizeCode(code));
        onDone(TURNED_OFF);
        return;
      }
      setEnrol(await startTwoStep(password));
      setPassword("");
      setBusy(false);
    } catch (caught) {
      const field = proofField(caught);
      const message = twoStepError(caught);
      setCode(""); // a code works once
      setBusy(false);
      if (field) showOnFields({ password: field === "password" ? message : null, code: field === "code" ? message : null });
      else setFailure(message);
    }
  }

  if (enrol) {
    return (
      <ActionDialog title="Two-step sign-in" onClose={onClose} onClosed={onClosed}>
        <EnrolStep
          enrol={enrol}
          email={me.user.email}
          onTurnedOn={() => onDone(TURNED_ON)}
          onRestart={() => setEnrol(null)}
        />
      </ActionDialog>
    );
  }

  return (
    <ActionDialog title="Two-step sign-in" onClose={onClose} onClosed={onClosed} canClose={() => !busy}>
      <p className={styles.twoStepText}>
        Your two-step sign-in: <strong>{view === "on" ? "On" : "Off"}</strong>. {view === "on" ? ON_TEXT : OFF_TEXT}
      </p>
      {view === "on" && <p className={styles.note}>{me.user.role === "admin" ? LOST_PHONE : LOST_CODE}</p>}
      <form method="post" noValidate onSubmit={submit}>
        <PasswordField
          id={PASSWORD_ID}
          label="Current password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          required
          error={problems.password}
          masked={masked}
          onMaskedChange={setMasked}
        />
        {view === "on" && (
          <CodeField
            id={CODE_ID}
            label="6-digit code"
            value={code}
            onChange={(value) => {
              setCode(value);
              if (problems.code) setProblems((was) => ({ ...was, code: codeError(value) }));
            }}
            error={problems.code}
            hint="From your authenticator app."
            enterKeyHint="done"
          />
        )}
        {view === "on" && <p className={styles.warning}>{OFF_WARNING}</p>}
        {failure && (
          <p className="error-text" role="alert">
            {failure}
          </p>
        )}
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {view === "on" ? "Turn off" : "Turn on"}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
