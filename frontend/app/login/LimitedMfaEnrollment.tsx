"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { RequiredEnrol } from "./EnrolStep";
import styles from "./login.module.css";
import twoStep from "./twoStep.module.css";
import { PasswordField } from "@/components/ui/PasswordField";
import { currentSession, startTwoStep } from "@/lib/api";
import { useHydrated } from "@/components/ui/useHydrated";
import { twoStepError } from "@/components/shell/twoStepAccount";
import type { TwoStepSetup } from "@/lib/types";

/** A legacy_limited session can enroll MFA here, but cannot use the rest of the app. */
export function LimitedMfaEnrollment({ next }: { next: string }) {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [enrol, setEnrol] = useState<TwoStepSetup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    currentSession().then(
      (reply) => {
        if (live && reply.signed_in) setEmail(reply.me.user.email);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || password === "") return;
    setError(null);
    setBusy(true);
    try {
      setEnrol(await startTwoStep(password));
      setPassword("");
    } catch (caught) {
      setError(twoStepError(caught));
      setPassword("");
      setBusy(false);
    }
  }

  if (enrol) {
    return (
      <RequiredEnrol
        enrol={enrol}
        email={email || "your account"}
        next={next}
        onRestart={() => {
          setEnrol(null);
          setBusy(false);
        }}
      />
    );
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void submit(event)} className={styles.form} aria-labelledby="limited-mfa-title">
      <div className={twoStep.intro}>
        <h1 id="limited-mfa-title" className={twoStep.title}>Set up required two-step sign-in</h1>
        <p className={twoStep.lead}>
          Your sign-in is limited until this is complete. Enter your Kaagaz password to set up an authenticator app; no app access is granted before you enter its first code.
        </p>
      </div>
      <PasswordField
        id="limited-mfa-password"
        label="Kaagaz password"
        value={password}
        onChange={setPassword}
        autoComplete="current-password"
        required
      />
      {error && <p className="error-text" role="alert">{error}</p>}
      <button type="submit" className={`btn btn-primary btn-block ${styles.submit}`} disabled={!hydrated || busy || password === ""} aria-busy={busy || undefined}>
        {busy && <span className="spinner" aria-hidden="true" />}
        {busy ? "Starting setup…" : "Continue"}
      </button>
    </form>
  );
}
