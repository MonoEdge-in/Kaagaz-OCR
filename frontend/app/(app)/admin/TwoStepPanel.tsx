"use client";

// The signed-in admin's own two-step sign-in (2FA), in Admin, Settings: On or
// Off, and one button to change it. "Turn on" gets a new key and shows the
// set-up step; the first right code turns it on. "Turn off" asks for the
// current password (a wrong one counts toward the lockout); this browser stays
// signed in, the admin's other browsers are signed out. Like the sign-in
// forms: method="post", no name attributes, the password masked on submit.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { EnrolStep } from "@/app/admin/login/EnrolStep";
import styles from "@/app/admin/login/adminLogin.module.css";
import admin from "@/components/admin/Admin.module.css";
import {
  TURN_OFF_QUESTION,
  TWO_STEP_TEXT,
  turnOffPasswordError,
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

type Step = { kind: "idle" } | { kind: "turning_on"; setup: TwoStepSetup } | { kind: "turning_off" };

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

  async function turnOn() {
    show({ kind: "idle" });
    setBusy(true);
    try {
      show({ kind: "turning_on", setup: await startTwoStep() });
    } catch (caught) {
      setFailure(twoStepError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = turnOffPasswordError(password);
    if (found) {
      flushSync(() => setError(found));
      document.getElementById(PASSWORD_ID)?.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      changed(await turnOffTwoStep(password));
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

      {step.kind === "turning_off" && (
        <form
          method="post"
          noValidate
          onSubmit={turnOff}
          className="stack"
          aria-labelledby="turn-off-question"
        >
          <p id="turn-off-question" className={styles.twoStepText}>
            <strong>{TURN_OFF_QUESTION}</strong>
          </p>
          <PasswordField
            id={PASSWORD_ID}
            label="Current password"
            value={password}
            onChange={(value) => {
              setPassword(value);
              if (error) setError(turnOffPasswordError(value));
            }}
            autoComplete="current-password"
            required
            error={error}
            masked={masked}
            onMaskedChange={setMasked}
          />
          <div className="row">
            <button
              type="submit"
              className="btn btn-danger"
              disabled={!hydrated || busy}
              aria-busy={busy || undefined}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              {busy ? "Turning off…" : "Yes, turn off"}
            </button>
            <button type="button" className="btn" onClick={() => show({ kind: "idle" })} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {step.kind === "idle" && (
        <div>
          <button
            type="button"
            className={state === "off" ? "btn btn-primary" : "btn"}
            onClick={() => (state === "off" ? void turnOn() : show({ kind: "turning_off" }))}
            disabled={!hydrated || busy}
            aria-busy={busy || undefined}
          >
            {busy && <span className="spinner" aria-hidden="true" />}
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
