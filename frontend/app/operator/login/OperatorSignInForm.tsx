"use client";

// The operator sign-in form: e-mail, password and the 6-digit code, all
// needed (two-step is always on for operators). There is no set-up step: the
// two-step key is issued on the server when the account is made. As the
// other sign-in forms: method="post" and no name attributes; the password is
// masked again on submit; the code is cleared after a failure (it works once).
// Every 401 reads the same, whatever was wrong. Signed in, a full page load
// to /operator.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CodeField } from "@/app/admin/login/CodeField";
import { normalizeCode } from "@/app/admin/login/adminSignIn";
import { normalizeEmail } from "@/app/login/signIn";
import { SignedOutNotice } from "@/app/login/SignedOutNotice";
import { KEY_HELP, firstInvalidOperator, operatorSignInError, validateOperatorSignIn } from "@/components/operator/logic";
import type { OperatorErrors, OperatorField } from "@/components/operator/logic";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { operatorLogin } from "@/lib/api";
import { leavePage } from "@/lib/sessionChannel";
import login from "../../login/login.module.css";
import styles from "../../admin/login/adminLogin.module.css";

const FIELD_IDS: Record<OperatorField, string> = {
  email: "operator-login-email",
  password: "operator-login-password",
  code: "operator-login-code",
};

const NO_ERRORS: OperatorErrors = { email: null, password: null, code: null };

export function OperatorSignInForm({ note }: { note: string | null }) {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [masked, setMasked] = useState(true);
  const [errors, setErrors] = useState<OperatorErrors>(NO_ERRORS);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function change(field: OperatorField, value: string) {
    if (field === "email") setEmail(value);
    if (field === "password") setPassword(value);
    if (field === "code") setCode(value);
    // Once an error shows, it clears as soon as the field is typed in again.
    if (errors[field]) setErrors((was) => ({ ...was, [field]: null }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = validateOperatorSignIn(email, password, code);
    const invalid = firstInvalidOperator(found);
    flushSync(() => setErrors(found)); // described before it gets focus
    if (invalid) {
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }
    setBusy(true);
    try {
      await operatorLogin(normalizeEmail(email), password, normalizeCode(code));
      leavePage("/operator");
    } catch (caught) {
      setFailure(operatorSignInError(caught));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <form method="post" noValidate onSubmit={submit} className={login.form} aria-labelledby="operator-login-title">
      <div className={styles.intro}>
        <h1 id="operator-login-title" className={styles.title}>
          Platform sign-in
        </h1>
        <p className={styles.lead}>For Kaagaz platform operators: organisations and their admins.</p>
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
          onChange={(event) => change("email", event.target.value)}
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={errors.email ? "operator-login-email-error" : undefined}
        />
        {errors.email && (
          <p id="operator-login-email-error" className={`error-text ${login.fieldError}`}>
            {errors.email}
          </p>
        )}
      </div>

      <PasswordField
        id={FIELD_IDS.password}
        label="Password"
        value={password}
        onChange={(value) => change("password", value)}
        autoComplete="current-password"
        enterKeyHint="next"
        required
        error={errors.password}
        masked={masked}
        onMaskedChange={setMasked}
      />

      <CodeField
        id={FIELD_IDS.code}
        label="6-digit code"
        value={code}
        onChange={(value) => change("code", value)}
        error={errors.code}
        hint={`From your authenticator app. ${KEY_HELP}`}
      />

      {failure && (
        <div className={login.banner} role="alert">
          <p className={styles.bannerText}>{failure}</p>
        </div>
      )}

      <button
        type="submit"
        className={`btn btn-primary btn-block ${login.submit}`}
        disabled={!hydrated || busy}
        aria-busy={busy || undefined}
      >
        {busy && <span className="spinner" aria-hidden="true" />}
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
