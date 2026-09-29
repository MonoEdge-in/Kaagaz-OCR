"use client";

// Turning on two-step sign-in (PRD A2), in Admin, Settings
// (app/(app)/admin/TwoStepPanel.tsx). The server has sent a set-up key and a
// token that lasts 10 minutes; nothing is stored yet. This step shows the key
// as a QR code to scan, and in groups of 4 with a Copy button, the issuer and the
// account to type into the app, 3 short steps, then the code field. The key and the token live only
// in this component's state: never in the address bar, storage or logs.

import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CopyButton } from "@/components/admin/CopyButton";
import { QrCode } from "@/components/admin/QrCode";
import { useHydrated } from "@/components/ui/useHydrated";
import { adminEnrol } from "@/lib/api";
import type { Me, TwoStepSetup } from "@/lib/types";
import login from "../../login/login.module.css";
import {
  ENROL_STEPS,
  codeError,
  enrolDetails,
  enrolErrorMessage,
  groupSecret,
  normalizeCode,
  secretForCopy,
} from "./adminSignIn";
import { CodeField } from "./CodeField";
import styles from "./adminLogin.module.css";

const CODE_ID = "enrol-code";

export function EnrolStep({
  enrol,
  email,
  onTurnedOn,
  onRestart,
}: {
  enrol: TwoStepSetup;
  email: string;
  onTurnedOn(me: Me): void;
  onRestart(): void;
}) {
  const hydrated = useHydrated();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The step replaces the Turn on button, so focus moves to its heading.
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => {
    element?.focus();
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = codeError(code);
    if (found) {
      flushSync(() => setError(found));
      document.getElementById(CODE_ID)?.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      onTurnedOn(await adminEnrol(enrol.enrol_token, normalizeCode(code)));
    } catch (caught) {
      setFailure(enrolErrorMessage(caught));
      setCode(""); // a code works once and changes every 30 seconds
      setBusy(false);
    }
  }

  return (
    <form
      method="post"
      noValidate
      onSubmit={submit}
      className={login.form}
      aria-labelledby="enrol-title"
    >
      <div className={styles.intro}>
        <h3 id="enrol-title" ref={focusOnMount} tabIndex={-1} className={styles.stepTitle}>
          Set up your authenticator app
        </h3>
        <p className={styles.lead}>
          Once it is on, admin sign-in asks for your password and a 6-digit code from an app on
          your phone.
        </p>
      </div>

      <ol className={styles.steps}>
        {ENROL_STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>

      <div className={`card ${styles.keyCard}`}>
        <div className={styles.qr}>
          <QrCode value={enrol.otpauth_uri} label="QR code to scan with your authenticator app" />
        </div>
        <p className={styles.note}>No camera to scan with? Type the account and key below instead.</p>
        <dl className={styles.details}>
          {enrolDetails(email).map((detail) => (
            <div key={detail.label} className={styles.detail}>
              <dt>{detail.label}</dt>
              <dd>{detail.value}</dd>
            </div>
          ))}
          <div className={styles.detail}>
            <dt>Key</dt>
            <dd>
              <code className={styles.key} translate="no">
                {groupSecret(enrol.secret)}
              </code>
            </dd>
          </div>
        </dl>
        <CopyButton text={secretForCopy(enrol.secret)} what="Key" buttonText="Copy key" />
        <p className={styles.note}>Keep this key private. Anyone who has it can make your codes.</p>
      </div>

      <CodeField
        id={CODE_ID}
        label="6-digit code from the app"
        value={code}
        onChange={(value) => {
          setCode(value);
          if (error) setError(codeError(value));
        }}
        error={error}
      />

      {failure && (
        <div className={login.banner} role="alert">
          {failure}
        </div>
      )}

      <button
        type="submit"
        className={`btn btn-primary btn-block ${login.submit}`}
        disabled={!hydrated || busy}
        aria-busy={busy || undefined}
      >
        {busy && <span className="spinner" aria-hidden="true" />}
        {busy ? "Finishing…" : "Finish setup"}
      </button>

      <button type="button" className="btn btn-quiet" onClick={onRestart} disabled={busy}>
        Start again
      </button>
    </form>
  );
}
