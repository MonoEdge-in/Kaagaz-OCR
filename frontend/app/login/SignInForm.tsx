"use client";

// The sign-in form. Security details (council 2026-09-27, security-auditor):
// - method="post" and no name attributes, so even a submit before React runs
//   can never put the e-mail or password in the address bar or history;
// - the button is disabled only until the page has hydrated, then only while
//   busy;
// - the password is masked again as soon as the form is submitted;
// - typed values stay after an error, so nothing has to be typed twice.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { login } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import {
  emailError,
  firstInvalid,
  normalizeEmail,
  passwordError,
  signInErrorMessage,
  validateSignIn,
} from "./signIn";
import type { SignInErrors, SignInField } from "./signIn";
import { SignedOutNotice } from "./SignedOutNotice";
import styles from "./login.module.css";

const FIELD_IDS: Record<SignInField, string> = {
  email: "login-email",
  password: "login-password",
};

/** note: what the page says after signing out or a session ending (SignedOutNotice). */
export function SignInForm({ note }: { note: string | null }) {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [masked, setMasked] = useState(true);
  const [errors, setErrors] = useState<SignInErrors>({ email: null, password: null });
  // A field is checked on blur only once it has been typed in, so tabbing
  // through an empty form does not fill it with errors.
  const [edited, setEdited] = useState<Record<SignInField, boolean>>({
    email: false,
    password: false,
  });
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function changeEmail(value: string) {
    setEmail(value);
    setEdited((was) => ({ ...was, email: true }));
    // Once an error shows, it updates as the person fixes it.
    if (errors.email) setErrors((was) => ({ ...was, email: emailError(value) }));
  }

  function changePassword(value: string) {
    setPassword(value);
    setEdited((was) => ({ ...was, password: true }));
    if (errors.password) setErrors((was) => ({ ...was, password: passwordError(value) }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setServerError(null);
    const found = validateSignIn(email, password);
    const invalid = firstInvalid(found);
    if (invalid) {
      // Commit the messages first, so the field is described when it gets focus.
      flushSync(() => setErrors(found));
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }
    setErrors(found);
    setBusy(true);
    try {
      const signedIn = await login(normalizeEmail(email), password);
      // A full page load, and the other tabs hear who is signed in now, so
      // nothing of anyone signed in before stays in memory (critical #5).
      announceSession(signedIn.user.id);
      leavePage("/documents");
    } catch (caught) {
      setServerError(signInErrorMessage(caught));
      setBusy(false);
    }
  }

  const emailDescribedBy = errors.email ? "login-email-error" : undefined;

  return (
    <form method="post" noValidate onSubmit={submit} className={styles.form} aria-labelledby="login-title">
      <div className={styles.intro}>
        <h1 id="login-title" className={styles.title}>
          Sign in to Kaagaz
        </h1>
        <p className={styles.lead}>Use the e-mail your admin set up for you.</p>
      </div>
      <SignedOutNotice message={note} />

      <div className="field">
        <label htmlFor={FIELD_IDS.email}>E-mail</label>
        <input
          id={FIELD_IDS.email}
          className="input"
          type="email"
          autoComplete="username"
          inputMode="email"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="next"
          required
          value={email}
          onChange={(event) => changeEmail(event.target.value)}
          onBlur={() => {
            if (edited.email) setErrors((was) => ({ ...was, email: emailError(email) }));
          }}
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={emailDescribedBy}
        />
        {errors.email && (
          <p id="login-email-error" className={`error-text ${styles.fieldError}`}>
            {errors.email}
          </p>
        )}
      </div>

      <PasswordField
        id={FIELD_IDS.password}
        label="Password"
        value={password}
        onChange={changePassword}
        onBlur={() => {
          if (edited.password) setErrors((was) => ({ ...was, password: passwordError(password) }));
        }}
        autoComplete="current-password"
        enterKeyHint="go"
        required
        error={errors.password}
        masked={masked}
        onMaskedChange={setMasked}
      />

      {serverError && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {serverError}
        </div>
      )}

      <button
        type="submit"
        className={`btn btn-primary btn-block ${styles.submit}`}
        disabled={!hydrated || busy}
        aria-busy={busy || undefined}
      >
        {busy && <span className="spinner" aria-hidden="true" />}
        {busy ? "Signing in…" : "Sign in"}
      </button>

      <p className={styles.help}>Forgot your password? Ask your account admin to reset it.</p>
    </form>
  );
}
