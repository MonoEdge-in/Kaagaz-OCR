"use client";

// The first admin sign-in, before 2FA is set up (PRD A2). The server has
// checked the password and sent a set-up key and a token that lasts 10
// minutes. This step shows the key in groups of 4 with a Copy button, the
// issuer and the account to type into the app, 3 short steps, then the code
// field. There is no QR code (PRD 9). The key and the token live only in this
// component's state: never in the address bar, storage or logs.

import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CopyButton } from "@/components/admin/CopyButton";
import { useHydrated } from "@/components/ui/useHydrated";
import { adminEnrol } from "@/lib/api";
import type { EnrolRequired } from "@/lib/types";
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
  onSignedIn,
  onRestart,
}: {
  enrol: EnrolRequired;
  email: string;
  onSignedIn(): void;
  onRestart(): void;
}) {
  const hydrated = useHydrated();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The step replaces the first form, so focus moves to its heading.
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
      await adminEnrol(enrol.enrol_token, normalizeCode(code));
      onSignedIn();
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
        <h1 id="enrol-title" ref={focusOnMount} tabIndex={-1} className={styles.title}>
          Set up your authenticator app
        </h1>
        <p className={styles.lead}>
          Admins sign in with a password and a 6-digit code from an app on their phone. Set it up
          once, now.
        </p>
      </div>

      <ol className={styles.steps}>
        {ENROL_STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>

      <div className={`card ${styles.keyCard}`}>
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
