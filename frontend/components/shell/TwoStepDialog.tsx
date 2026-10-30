"use client";

// "Two-step sign-in" from an admin's own Account menu (Tushar, 30 Oct 2026). It says
// whether it is on, with one button to change it. Turn off asks for the password and a
// code from the app, and warns first; it then stays off until the admin turns it on. Turn
// on asks for the password, then shows the sign-in's own set-up step (EnrolStep: the QR
// code, the key and the first code). An error goes on its field and the focus moves there.
// The key and token live only in this state. Never closes while a request runs.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CodeField } from "@/app/login/CodeField";
import { EnrolStep } from "@/app/login/EnrolStep";
import login from "@/app/login/login.module.css";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import admin from "@/components/admin/Admin.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { startTwoStep, turnOffTwoStep } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Me, TwoStepSetup } from "@/lib/types";
import { TWO_STEP_ACCOUNT, passwordError, refusal, statusText, twoStepState } from "./twoStepAccount";
import type { Field } from "./twoStepAccount";
import styles from "./Shell.module.css";

const PASSWORD_ID = "two-step-password";
const CODE_ID = "two-step-code";

const fieldId = (field: Field) => (field === "password" ? PASSWORD_ID : CODE_ID);
const focusOnMount = (element: HTMLElement | null) => element?.focus();

type Step = "status" | "off" | "on_password" | "on_enrol" | "done";

export function TwoStepDialog({ me, onClose, onClosed }: { me: Me; onClose(): void; onClosed(): void }) {
  const { refresh } = useSession();
  const [step, setStep] = useState<Step>("status");
  const [password, setPassword] = useState("");
  const [masked, setMasked] = useState(true);
  const [code, setCode] = useState("");
  const [problems, setProblems] = useState<Record<Field, string | null>>({ password: null, code: null });
  const [failure, setFailure] = useState<string | null>(null);
  const [setup, setSetup] = useState<TwoStepSetup | null>(null);
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const status = statusText(twoStepState(me));

  // A new step starts empty, with focus on its first field.
  function show(next: "off" | "on_password") {
    flushSync(() => {
      setStep(next);
      setPassword("");
      setCode("");
      setProblems({ password: null, code: null });
      setFailure(null);
    });
    document.getElementById(PASSWORD_ID)?.focus();
  }

  function finish(message: string) {
    setDone(message);
    setStep("done");
    refresh(); // the menu, Trash and the gate read the new state
  }

  function refused(caught: unknown, hasCode: boolean) {
    const found = refusal(caught, hasCode);
    if (hasCode) setCode(""); // a code works once and changes every 30 seconds
    if (found.field === null) {
      setFailure(found.message);
      return;
    }
    const { field, message } = found;
    flushSync(() =>
      setProblems({ password: field === "password" ? message : null, code: field === "code" ? message : null }),
    );
    document.getElementById(fieldId(field))?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const hasCode = step === "off";
    const found = { password: passwordError(password), code: hasCode ? codeError(code) : null };
    flushSync(() => setProblems(found));
    const first: Field | null = found.password ? "password" : found.code ? "code" : null;
    if (first) {
      document.getElementById(fieldId(first))?.focus();
      return;
    }
    setBusy(true);
    try {
      if (hasCode) {
        await turnOffTwoStep(password, normalizeCode(code));
        finish(TWO_STEP_ACCOUNT.offDone);
      } else {
        setSetup(await startTwoStep(password));
        setStep("on_enrol");
      }
      setPassword("");
    } catch (caught) {
      refused(caught, hasCode);
    }
    setBusy(false);
  }

  const failed = failure && (
    <p className="error-text" role="alert">
      {failure}
    </p>
  );

  return (
    <ActionDialog title={TWO_STEP_ACCOUNT.item} onClose={onClose} onClosed={onClosed} canClose={() => !busy}>
      <div className={styles.twoStep}>
        {step === "status" && (
          <>
            <p>{status.text}</p>
            <div className={styles.twoStepButtons}>
              <button
                type="button"
                className="btn btn-primary"
                data-autofocus
                onClick={() => show(twoStepState(me) === "on" ? "off" : "on_password")}
              >
                {status.action}
              </button>
            </div>
          </>
        )}

        {(step === "off" || step === "on_password") && (
          <form method="post" noValidate onSubmit={(event) => void submit(event)} className={login.form}>
            {step === "on_password" && <p>{TWO_STEP_ACCOUNT.onLead}</p>}
            <PasswordField
              id={PASSWORD_ID}
              label={TWO_STEP_ACCOUNT.password}
              value={password}
              onChange={(value) => {
                setPassword(value);
                if (problems.password) setProblems({ ...problems, password: passwordError(value) });
              }}
              autoComplete="current-password"
              required
              error={problems.password}
              masked={masked}
              onMaskedChange={setMasked}
              enterKeyHint={step === "off" ? "next" : "go"}
            />
            {step === "off" && (
              <>
                <CodeField
                  id={CODE_ID}
                  label={TWO_STEP_ACCOUNT.code}
                  value={code}
                  onChange={(value) => {
                    setCode(value);
                    if (problems.code) setProblems({ ...problems, code: codeError(value) });
                  }}
                  error={problems.code}
                />
                <p className={styles.twoStepWarning}>{TWO_STEP_ACCOUNT.offWarning}</p>
              </>
            )}
            {failed}
            {/* [Cancel] [the action]: the action last, on top when they stack. */}
            <div className={styles.twoStepButtons}>
              <button type="button" className="btn" onClick={onClose} disabled={busy}>
                {TWO_STEP_ACCOUNT.cancel}
              </button>
              <button
                type="submit"
                className={`btn btn-primary ${busy ? admin.busy : ""}`}
                disabled={busy}
                aria-busy={busy || undefined}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                {step === "off" ? TWO_STEP_ACCOUNT.offButton : TWO_STEP_ACCOUNT.onButton}
              </button>
            </div>
          </form>
        )}

        {step === "on_enrol" && setup && (
          <EnrolStep
            enrol={setup}
            email={me.user.email}
            onTurnedOn={() => {
              setSetup(null);
              finish(TWO_STEP_ACCOUNT.onDone);
            }}
            onRestart={() => {
              setSetup(null);
              show("on_password");
            }}
          />
        )}

        {step === "done" && (
          <>
            <p ref={focusOnMount} tabIndex={-1} role="status">
              {done}
            </p>
            <div className={styles.twoStepButtons}>
              <button type="button" className="btn btn-primary" onClick={onClose}>
                {TWO_STEP_ACCOUNT.done}
              </button>
            </div>
          </>
        )}
      </div>
    </ActionDialog>
  );
}
