"use client";

// Forgot password (#457 phase 0): the email, then always the same words, so the page never
// shows who has an account. The link lasts 30 minutes and signs every browser out. As the
// sign-in form: method="post", no name attribute, typed value kept after an error.

import Link from "next/link";
import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { useHydrated } from "@/components/ui/useHydrated";
import { forgotPassword } from "@/lib/api";
import { FORGOT_SENT, linkError } from "@/lib/emailLinks";
import { emailError, normalizeEmail } from "../login/signIn";
import login from "../login/login.module.css";

const EMAIL_ID = "forgot-email";

export function ForgotForm() {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = emailError(email);
    if (found) {
      flushSync(() => setProblem(found));
      document.getElementById(EMAIL_ID)?.focus();
      return;
    }
    setProblem(null);
    setBusy(true);
    try {
      await forgotPassword(normalizeEmail(email));
      setSent(true);
    } catch (caught) {
      setFailure(linkError(caught));
    }
    setBusy(false);
  }

  if (sent) {
    return (
      <div className={login.form}>
        <div className={login.intro}>
          <h1 className={login.title}>Check your email</h1>
          <p className={login.lead} role="status">
            {FORGOT_SENT} The link works for 30 minutes.
          </p>
        </div>
        <p className={login.switch}>
          <Link href="/login">Back to sign in</Link>
        </p>
      </div>
    );
  }

  return (
    <form method="post" noValidate onSubmit={submit} className={login.form} aria-labelledby="forgot-title">
      <div className={login.intro}>
        <h1 id="forgot-title" className={login.title}>
          Forgot password?
        </h1>
        <p className={login.lead}>Type the email you sign in with. We will send a link to choose a new password.</p>
      </div>
      <div className="field">
        <label htmlFor={EMAIL_ID}>Email</label>
        <input
          id={EMAIL_ID}
          className="input"
          type="email"
          autoComplete="username"
          inputMode="email"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="send"
          required
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (problem) setProblem(emailError(event.target.value));
          }}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${EMAIL_ID}-error` : undefined}
        />
        {problem && (
          <p id={`${EMAIL_ID}-error`} className={`error-text ${login.fieldError}`}>
            {problem}
          </p>
        )}
      </div>
      {failure && (
        <div className={login.banner} role="alert">
          <Icon name="alert" />
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
        {busy ? "Sending…" : "Send link"}
      </button>
      <p className={login.switch}>
        <Link href="/login">Back to sign in</Link>
      </p>
    </form>
  );
}
