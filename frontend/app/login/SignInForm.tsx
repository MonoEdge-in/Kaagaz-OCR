"use client";

// The sign-in form, the one sign-in for everyone (16 Oct 2026). Security details
// (council 2026-09-27, security-auditor):
// - method="post" and no name attributes, so even a submit before React runs
//   can never put the e-mail or password in the address bar or history;
// - the button is disabled only until the page has hydrated, then only while
//   busy;
// - the password is masked again as soon as the form is submitted;
// - typed values stay after an error, so nothing has to be typed twice, except
//   the code: a code works once and changes every 30 seconds.
// First the e-mail and password. An admin must have two-step sign-in, so only
// after a right password the server may ask for more: the 6-digit code (401
// code_required: a second step sends all three again), or setting two-step up
// first (403 mfa_enrol_required: the set-up step, RequiredEnrol). Signed in, the
// browser goes to `next` (/documents, or /admin when it came from there).
// Ways to sign in (#457 phase 1): the provider buttons and the divider above the fields
// (ProviderButtons), and after Google for anyone with codes on, only the code
// (ProviderCodeStep). The providers and whether email is on come from the server page.

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { login } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import type { TwoStepSetup } from "@/lib/types";
import { CodeField } from "./CodeField";
import { ForgotPasswordLink } from "./ForgotPasswordLink";
import { ProviderButtons } from "./ProviderButtons";
import { ProviderCodeStep } from "./ProviderCodeStep";
import { RequiredEnrol } from "./EnrolStep";
import { ResendConfirm } from "./ResendConfirm";
import {
  emailError,
  firstInvalid,
  normalizeEmail,
  orgPending,
  passwordError,
  signInErrorMessage,
  validateSignIn,
} from "./signIn";
import type { SignInErrors, SignInField } from "./signIn";
import { SignedOutNotice } from "./SignedOutNotice";
import { CODE_ADVICE, LOST_PHONE_HELP, codeError, codeNeeded, enrolOffer, normalizeCode } from "./twoStep";
import styles from "./login.module.css";

const FIELD_IDS: Record<SignInField | "code", string> = {
  email: "login-email",
  password: "login-password",
  code: "login-code",
};

const CODE_HINT = "From your authenticator app. A new code shows every 30 seconds.";

/** note: what the page says after signing out or a session ending (SignedOutNotice).
 * next: where to go once signed in (pathAfterSignIn, components/shell/gate.ts).
 * oidcError: a failed provider sign-in's code; providerStep: the code after a provider.
 * providers, emailOn: what the server switched on (signInOptions.ts). */
export function SignInForm({
  note,
  next,
  oidcError = null,
  providerStep = false,
  providers,
  emailOn,
}: {
  note: string | null;
  next: string;
  oidcError?: string | null;
  providerStep?: boolean;
  providers: readonly string[];
  emailOn: boolean;
}) {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  // The second step: the server asked for the code (codeNeeded).
  const [askCode, setAskCode] = useState(false);
  // Or for setting two-step sign-in up first (enrolOffer).
  const [enrol, setEnrol] = useState<TwoStepSetup | null>(null);
  const [masked, setMasked] = useState(true);
  const [errors, setErrors] = useState<SignInErrors>({ email: null, password: null });
  // A field is checked on blur only once it has been typed in, so tabbing
  // through an empty form does not fill it with errors.
  const [edited, setEdited] = useState<Record<SignInField, boolean>>({
    email: false,
    password: false,
  });
  const [serverError, setServerError] = useState<string | null>(null);
  // The organisation is a sign-up waiting for approval (#457): "Send the confirm link again" shows.
  const [pending, setPending] = useState(false);
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

  function back() {
    setAskCode(false);
    setEnrol(null);
    setCode("");
    setCodeProblem(null);
    setServerError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setServerError(null);
    setPending(false);
    if (askCode) {
      const problem = codeError(code);
      if (problem) {
        flushSync(() => setCodeProblem(problem));
        document.getElementById(FIELD_IDS.code)?.focus();
        return;
      }
      setCodeProblem(null);
    } else {
      const found = validateSignIn(email, password);
      const invalid = firstInvalid(found);
      if (invalid) {
        // Commit the messages first, so the field is described when it gets focus.
        flushSync(() => setErrors(found));
        document.getElementById(FIELD_IDS[invalid])?.focus();
        return;
      }
      setErrors(found);
    }
    setBusy(true);
    try {
      const signedIn = await login(normalizeEmail(email), password, askCode ? normalizeCode(code) : undefined);
      // A full page load, and the other tabs hear who is signed in now, so
      // nothing of anyone signed in before stays in memory (critical #5).
      announceSession(signedIn.user.id);
      leavePage(next);
    } catch (caught) {
      const offer = enrolOffer(caught);
      if (offer) {
        setEnrol(offer);
        setBusy(false);
        return;
      }
      if (!askCode && codeNeeded(caught)) {
        flushSync(() => {
          setAskCode(true);
          setBusy(false);
        });
        document.getElementById(FIELD_IDS.code)?.focus();
        return;
      }
      const message = signInErrorMessage(caught);
      flushSync(() => {
        setPending(orgPending(caught));
        setServerError(askCode ? `${message} ${CODE_ADVICE}` : message);
        setCode("");
        setBusy(false);
      });
      // The pressed button was disabled while busy, which dropped focus to the page: put it back
      // in the field to fix, so the keyboard carries on from there (QA pass, 25 Oct 2026).
      document.getElementById(askCode ? FIELD_IDS.code : FIELD_IDS.password)?.focus();
    }
  }

  if (enrol) {
    return <RequiredEnrol enrol={enrol} email={normalizeEmail(email)} next={next} onRestart={back} />;
  }
  if (providerStep) return <ProviderCodeStep next={next} />;

  const emailDescribedBy = errors.email ? "login-email-error" : undefined;

  return (
    <form method="post" noValidate onSubmit={submit} className={styles.form} aria-labelledby="login-title">
      <div className={styles.intro}>
        <h1 id="login-title" className={styles.title}>
          Sign in to Kaagaz
        </h1>
        <p className={styles.lead}>
          {askCode
            ? `Two-step sign-in is on for ${normalizeEmail(email)}. Enter the 6-digit code from your authenticator app.`
            : "Use the email your admin set up for you, or the one you signed up with."}
        </p>
      </div>
      <SignedOutNotice message={note} />

      {!askCode && <ProviderButtons next={next} error={oidcError} providers={providers} />}
      {!askCode && (
        <>
          <div className="field">
            <label htmlFor={FIELD_IDS.email}>Email</label>
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
        </>
      )}

      {askCode && (
        <CodeField
          id={FIELD_IDS.code}
          label="6-digit code"
          value={code}
          onChange={(value) => {
            setCode(value);
            if (codeProblem) setCodeProblem(codeError(value));
          }}
          error={codeProblem}
          hint={CODE_HINT}
        />
      )}
      {askCode && <p className="hint">{LOST_PHONE_HELP}</p>}

      {serverError && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {serverError}
        </div>
      )}
      {pending && <ResendConfirm email={normalizeEmail(email)} />}

      <button
        type="submit"
        className={`btn btn-primary btn-block ${styles.submit}`}
        disabled={!hydrated || busy}
        aria-busy={busy || undefined}
      >
        {busy && <span className="spinner" aria-hidden="true" />}
        {busy ? "Signing in…" : "Sign in"}
      </button>

      {askCode && (
        <button type="button" className="btn btn-block" disabled={busy} onClick={back}>
          Back to email and password
        </button>
      )}

      <ForgotPasswordLink open={emailOn} />
    </form>
  );
}
