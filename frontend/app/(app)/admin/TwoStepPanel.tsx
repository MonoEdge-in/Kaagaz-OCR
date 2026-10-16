"use client";

// The signed-in admin's own two-step sign-in (2FA), in Admin, Settings: On or
// Off, and one button to change it. Both ask for the current password first (a
// wrong one counts toward the lockout). "Turn on" then shows the new key and
// its QR code; the first right code turns it on. "Turn off" keeps this browser
// signed in and signs out the admin's other browsers. Like the sign-in forms:
// method="post", no name attributes, the password masked again on submit.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { EnrolStep } from "@/app/admin/login/EnrolStep";
import styles from "@/app/admin/login/adminLogin.module.css";
import admin from "@/components/admin/Admin.module.css";
import {
  PASSWORD_STEP,
  TWO_STEP_TEXT,
  currentPasswordError,
  twoStepError,
  twoStepState,
} from "@/components/admin/twoStep";
import type { TwoStepState } from "@/components/admin/twoStep";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { startTwoStep, turnOffTwoStep } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Me, TwoStepSetup } from "@/lib/types";

const PASSWORD_ID = "two-step-password";

// "password": the current password, to turn it on or off (`to`).
type Step =
  | { kind: "idle" }
  | { kind: "password"; to: TwoStepState }
  | { kind: "turning_on"; setup: TwoStepSetup };

export function TwoStepPanel() {
  const { me, refresh } = useSession();
  const hydrated = useHydrated();
  // What the server said last, so On or Off is right before the session reloads.
  const [latest, setLatest] = useState<TwoStepState | null>(null);
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [password, setPassword] = useState("");
  const [masked, setMasked] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const state = latest ?? twoStepState(me);
  const text = TWO_STEP_TEXT[state];

  function show(next: Step) {
    setStep(next);
    setPassword("");
    setError(null);
    setFailure(null);
  }

  function changed(result: Me) {
    show({ kind: "idle" });
    setLatest(twoStepState(result));
    refresh();
  }

  async function submit(event: FormEvent<HTMLFormElement>, to: TwoStepState) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = currentPasswordError(password);
    if (found) {
      flushSync(() => setError(found));
      document.getElementById(PASSWORD_ID)?.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      if (to === "on") show({ kind: "turning_on", setup: await startTwoStep(password) });
      else changed(await turnOffTwoStep(password));
    } catch (caught) {
      setFailure(twoStepError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="two-step-heading">
      <h2 id="two-step-heading" className={admin.sectionHeading}>
        Two-step sign-in
      </h2>
      <p className={styles.twoStepText} aria-live="polite">
        Your two-step sign-in: <strong>{text.label}</strong>. {text.meaning}
      </p>

      {step.kind === "turning_on" && (
        <EnrolStep
          enrol={step.setup}
          email={me.user.email}
          onTurnedOn={changed}
          onRestart={() => show({ kind: "idle" })}
        />
      )}

      {step.kind === "password" && (
        <form
          method="post"
          noValidate
          onSubmit={(event) => void submit(event, step.to)}
          className="stack"
          aria-labelledby="two-step-question"
        >
          <p id="two-step-question" className={styles.twoStepText}>
            <strong>{PASSWORD_STEP[step.to].question}</strong>
          </p>
          <PasswordField
            id={PASSWORD_ID}
            label="Current password"
            value={password}
            onChange={(value) => {
              setPassword(value);
              if (error) setError(currentPasswordError(value));
            }}
            autoComplete="current-password"
            required
            error={error}
            masked={masked}
            onMaskedChange={setMasked}
          />
          {/* [Cancel] [Show my key / Yes, turn off]: the action last. */}
          <div className={admin.formButtons}>
            <button type="button" className="btn" onClick={() => show({ kind: "idle" })} disabled={busy}>
              Cancel
            </button>
            <button
              type="submit"
              className={`${step.to === "on" ? "btn btn-primary" : "btn btn-danger"} ${busy ? admin.busy : ""}`}
              disabled={!hydrated || busy}
              aria-busy={busy || undefined}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              {PASSWORD_STEP[step.to].button}
            </button>
          </div>
        </form>
      )}

      {step.kind === "idle" && (
        <div>
          <button
            type="button"
            className={state === "off" ? "btn btn-primary" : "btn"}
            onClick={() => show({ kind: "password", to: state === "off" ? "on" : "off" })}
            disabled={!hydrated || busy}
          >
            {state === "off" ? "Turn on" : "Turn off"}
          </button>
        </div>
      )}

      {failure && (
        <p className="error-text" role="alert">
          {failure}
        </p>
      )}
    </section>
  );
}
