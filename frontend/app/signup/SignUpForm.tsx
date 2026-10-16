"use client";

// The sign-up form (16 Oct 2026): a new organisation and its first admin. It
// keeps the sign-in forms' security details (app/login/SignInForm.tsx):
// method="post" and no name attributes, the button disabled until the page has
// hydrated and while busy, the password masked again on submit, and typed
// values kept after an error. Signed up, the new admin sets up two-step sign-in
// (every admin must have it, 16 Oct 2026: RequiredEnrol), then the browser goes
// to /admin.

import Link from "next/link";
import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { signUp } from "@/lib/api";
import { leavePage } from "@/lib/sessionChannel";
import type { TwoStepSetup } from "@/lib/types";
import { RequiredEnrol } from "../login/EnrolStep";
import login from "../login/login.module.css";
import {
  MAX_EMAIL,
  MAX_NAME,
  MAX_ORGANISATION,
  PASSWORD_HINT,
  firstInvalidSignUp,
  signUpBody,
  signUpError,
  signUpFieldError,
  validateSignUp,
} from "./signUp";
import type { SignUpError, SignUpErrors, SignUpField, SignUpInput } from "./signUp";

const FIELD_IDS: Record<SignUpField, string> = {
  organisation: "signup-organisation",
  name: "signup-name",
  email: "signup-email",
  password: "signup-password",
};

const EMPTY: SignUpInput = { organisation: "", name: "", email: "", password: "" };
const NO_ERRORS: SignUpErrors = { organisation: null, name: null, email: null, password: null };
const NOT_EDITED: Record<SignUpField, boolean> = {
  organisation: false,
  name: false,
  email: false,
  password: false,
};

export function SignUpForm() {
  const hydrated = useHydrated();
  const [input, setInput] = useState<SignUpInput>(EMPTY);
  const [masked, setMasked] = useState(true);
  const [errors, setErrors] = useState<SignUpErrors>(NO_ERRORS);
  // A field is checked on blur only once it has been typed in.
  const [edited, setEdited] = useState(NOT_EDITED);
  const [failure, setFailure] = useState<SignUpError | null>(null);
  const [busy, setBusy] = useState(false);
  // Signed up: the two-step set-up the server sent (no session before the first code).
  const [enrol, setEnrol] = useState<TwoStepSetup | null>(null);

  function change(field: SignUpField, value: string) {
    setInput((was) => ({ ...was, [field]: value }));
    setEdited((was) => ({ ...was, [field]: true }));
    // Once an error shows, it updates as the person fixes it.
    if (errors[field]) setErrors((was) => ({ ...was, [field]: signUpFieldError(field, value) }));
  }

  function blur(field: SignUpField) {
    if (edited[field]) setErrors((was) => ({ ...was, [field]: signUpFieldError(field, input[field]) }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = validateSignUp(input);
    const invalid = firstInvalidSignUp(found);
    if (invalid) {
      // Commit the messages first, so the field is described when it gets focus.
      flushSync(() => setErrors(found));
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }
    setErrors(found);
    setBusy(true);
    try {
      setEnrol(await signUp(signUpBody(input)));
      setBusy(false);
    } catch (caught) {
      setFailure(signUpError(caught));
      setBusy(false);
    }
  }

  if (enrol) {
    // An expired set-up (10 minutes) starts again from the sign-in: the account exists now.
    return (
      <RequiredEnrol
        enrol={enrol}
        email={signUpBody(input).email}
        next="/admin"
        onRestart={() => leavePage("/login?next=/admin&two_step=1")}
      />
    );
  }

  function textField(
    field: Exclude<SignUpField, "password">,
    label: string,
    maxLength: number,
    autoComplete: string,
  ) {
    const errorId = `${FIELD_IDS[field]}-error`;
    const isEmail = field === "email";
    return (
      <div className="field">
        <label htmlFor={FIELD_IDS[field]}>{label}</label>
        <input
          id={FIELD_IDS[field]}
          className="input"
          type={isEmail ? "email" : "text"}
          autoComplete={autoComplete}
          inputMode={isEmail ? "email" : undefined}
          autoCapitalize={isEmail ? "none" : "words"}
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="next"
          maxLength={maxLength}
          required
          value={input[field]}
          onChange={(event) => change(field, event.target.value)}
          onBlur={() => blur(field)}
          aria-invalid={errors[field] ? true : undefined}
          aria-describedby={errors[field] ? errorId : undefined}
        />
        {errors[field] && (
          <p id={errorId} className={`error-text ${login.fieldError}`}>
            {errors[field]}
          </p>
        )}
      </div>
    );
  }

  return (
    <form method="post" noValidate onSubmit={submit} className={login.form} aria-labelledby="signup-title">
      <div className={login.intro}>
        <h1 id="signup-title" className={login.title}>
          Create an account
        </h1>
        <p className={login.lead}>Set up your organisation. You will be its admin.</p>
      </div>

      {textField("organisation", "Organisation name", MAX_ORGANISATION, "organization")}
      {textField("name", "Your name", MAX_NAME, "name")}
      {textField("email", "Work e-mail", MAX_EMAIL, "username")}

      <PasswordField
        id={FIELD_IDS.password}
        label="Password"
        value={input.password}
        onChange={(value) => change("password", value)}
        onBlur={() => blur("password")}
        autoComplete="new-password"
        enterKeyHint="go"
        required
        error={errors.password}
        hint={PASSWORD_HINT}
        masked={masked}
        onMaskedChange={setMasked}
      />

      {failure && (
        <div className={login.banner} role="alert">
          {failure.message}
          {failure.signInInstead && (
            <>
              {" "}
              <Link href="/login">Sign in</Link>
            </>
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
        {busy ? "Creating account…" : "Create account"}
      </button>

      <p className={login.switch}>
        Already have an account? <Link href="/login">Sign in</Link>
      </p>
    </form>
  );
}
