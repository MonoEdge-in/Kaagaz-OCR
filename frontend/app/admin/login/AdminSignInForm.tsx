"use client";

// The admin sign-in form (PRD A1, A2). It keeps the normal form's security
// details (app/login/SignInForm.tsx):
// - method="post" and no name attributes, so nothing typed can reach the
//   address bar or history;
// - the button is disabled only until the page has hydrated, then only while
//   busy;
// - the password is masked again as soon as the form is submitted;
// - typed values stay after an error, except the code: a code works once and
//   changes every 30 seconds.
// The code is optional: an admin who has not turned on two-step sign-in
// leaves it empty (they can turn it on in Admin, Settings). Signed in, the
// browser goes to /admin.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { adminLogin } from "@/lib/api";
import { emailError, normalizeEmail, passwordError } from "../../login/signIn";
import login from "../../login/login.module.css";
import {
  adminSignInError,
  firstInvalidAdmin,
  normalizeCode,
  optionalCodeError,
  validateAdminSignIn,
} from "./adminSignIn";
import type { AdminSignInError, AdminSignInErrors, AdminSignInField } from "./adminSignIn";
import { CodeField } from "./CodeField";
import styles from "./adminLogin.module.css";

const FIELD_IDS: Record<AdminSignInField, string> = {
  email: "admin-login-email",
  password: "admin-login-password",
  code: "admin-login-code",
};

const NO_ERRORS: AdminSignInErrors = { email: null, password: null, code: null };

const CODE_HINT = "From your authenticator app. Leave it empty if two-step sign-in is off.";

export function AdminSignInForm() {
  const router = useRouter();
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [masked, setMasked] = useState(true);
  const [errors, setErrors] = useState<AdminSignInErrors>(NO_ERRORS);
  // A field is checked on blur only once it has been typed in.
  const [edited, setEdited] = useState<Record<AdminSignInField, boolean>>({
    email: false,
    password: false,
    code: false,
  });
  const [failure, setFailure] = useState<AdminSignInError | null>(null);
  const [busy, setBusy] = useState(false);

  function change(field: AdminSignInField, value: string) {
    if (field === "email") setEmail(value);
    if (field === "password") setPassword(value);
    if (field === "code") setCode(value);
    setEdited((was) => ({ ...was, [field]: true }));
    // Once an error shows, it updates as the person fixes it.
    if (errors[field]) setErrors((was) => ({ ...was, [field]: fieldError(field, value) }));
  }

  function blur(field: AdminSignInField, value: string) {
    if (edited[field]) setErrors((was) => ({ ...was, [field]: fieldError(field, value) }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = validateAdminSignIn(email, password, code);
    const invalid = firstInvalidAdmin(found);
    if (invalid) {
      // Commit the messages first, so the field is described when it gets focus.
      flushSync(() => setErrors(found));
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }
    setErrors(found);
    setBusy(true);
    const typedCode = normalizeCode(code);
    try {
      await adminLogin(normalizeEmail(email), password, typedCode === "" ? undefined : typedCode);
      router.replace("/admin");
    } catch (caught) {
      setFailure(adminSignInError(caught));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <form
      method="post"
      noValidate
      onSubmit={submit}
      className={login.form}
      aria-labelledby="admin-login-title"
    >
      <div className={styles.intro}>
        <h1 id="admin-login-title" className={styles.title}>
          Admin sign-in
        </h1>
        <p className={styles.lead}>For account admins: users, settings and activity.</p>
      </div>

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
          onBlur={() => blur("email", email)}
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={errors.email ? "admin-login-email-error" : undefined}
        />
        {errors.email && (
          <p id="admin-login-email-error" className={`error-text ${login.fieldError}`}>
            {errors.email}
          </p>
        )}
      </div>

      <PasswordField
        id={FIELD_IDS.password}
        label="Password"
        value={password}
        onChange={(value) => change("password", value)}
        onBlur={() => blur("password", password)}
        autoComplete="current-password"
        enterKeyHint="next"
        required
        error={errors.password}
        masked={masked}
        onMaskedChange={setMasked}
      />

      <CodeField
        id={FIELD_IDS.code}
        label="6-digit code, if you turned on two-step sign-in"
        value={code}
        onChange={(value) => change("code", value)}
        onBlur={() => blur("code", code)}
        error={errors.code}
        hint={CODE_HINT}
      />

      {failure && (
        <div className={login.banner} role="alert">
          <p className={styles.bannerText}>{failure.message}</p>
          {failure.changePasswordFirst && (
            <p className={styles.bannerText}>
              <Link href="/login">Go to the normal sign-in</Link>
            </p>
          )}
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

      <p className={login.help}>
        Not an admin, or here for documents and questions?{" "}
        <Link href="/login">Use the normal sign-in</Link>.
      </p>
    </form>
  );
}

function fieldError(field: AdminSignInField, value: string): string | null {
  if (field === "email") return emailError(value);
  if (field === "password") return passwordError(value);
  return optionalCodeError(value);
}
