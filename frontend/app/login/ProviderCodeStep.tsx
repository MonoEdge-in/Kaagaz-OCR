"use client";

// After Google (#457 phase 1), for anyone with two-step sign-in on (always an admin): only the
// 6-digit code, sent with POST /auth/oidc/code. The server holds which account in a 5-minute
// cookie, so nothing else is typed; once it has run out, "That sign-in expired" and the way
// back to the full sign-in.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { useHydrated } from "@/components/ui/useHydrated";
import { ApiError, oidcCode } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import { CodeField } from "./CodeField";
import { EXPIRED } from "./providerMessages";
import { signInErrorMessage } from "./signIn";
import { CODE_ADVICE, LOST_PHONE_HELP, codeError, normalizeCode } from "./twoStep";
import styles from "./login.module.css";

const CODE_ID = "login-provider-code";

export function ProviderCodeStep({ next }: { next: string }) {
  const hydrated = useHydrated();
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const found = codeError(code);
    if (found) {
      flushSync(() => setProblem(found));
      document.getElementById(CODE_ID)?.focus();
      return;
    }
    setProblem(null);
    setServerError(null);
    setBusy(true);
    try {
      const signedIn = await oidcCode(normalizeCode(code));
      announceSession(signedIn.user.id);
      leavePage(next);
    } catch (caught) {
      const gone = caught instanceof ApiError && caught.code === "not_signed_in";
      flushSync(() => {
        setExpired(gone);
        setServerError(gone ? EXPIRED : `${signInErrorMessage(caught)} ${CODE_ADVICE}`);
        setCode("");
        setBusy(false);
      });
      document.getElementById(CODE_ID)?.focus();
    }
  }

  return (
    <form method="post" noValidate onSubmit={submit} className={styles.form} aria-labelledby="login-title">
      <div className={styles.intro}>
        <h1 id="login-title" className={styles.title}>
          Sign in to Kaagaz
        </h1>
        <p className={styles.lead}>Two-step sign-in is on. Enter the 6-digit code from your authenticator app.</p>
      </div>
      {!expired && (
        <CodeField
          id={CODE_ID}
          label="6-digit code"
          value={code}
          onChange={(value) => {
            setCode(value);
            if (problem) setProblem(codeError(value));
          }}
          error={problem}
          hint="From your authenticator app. A new code shows every 30 seconds."
        />
      )}
      {!expired && <p className="hint">{LOST_PHONE_HELP}</p>}
      {serverError && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {serverError}
        </div>
      )}
      {!expired && (
        <button
          type="submit"
          className={`btn btn-primary btn-block ${styles.submit}`}
          disabled={!hydrated || busy}
          aria-busy={busy || undefined}
        >
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? "Signing in…" : "Sign in"}
        </button>
      )}
      <a className="btn btn-block" href="/login">
        Back to email and password
      </a>
    </form>
  );
}
