"use client";

// Choosing a password from an emailed link (#457 phase 0): "reset" (/reset-password, a
// forgotten one) or "invite" (/invite, a new person). The token is read from the address's
// fragment once, on the first load, and cleared from the address bar (takeToken). The new
// password twice; saved, every browser of the account is signed out, the email counts as
// confirmed, and the page sends them to sign in (an admin still types their code there).
// As the sign-in form: method="post", no name attributes, masked again on submit. A page opened
// without the link's token says so at once; once saved, focus moves to the result's heading.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { MIN_PASSWORD_LENGTH } from "@/components/admin/logic";
import { validateNewPassword } from "@/components/shell/setPassword";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { acceptInvite, resetPassword } from "@/lib/api";
import { LINK_GONE, LINK_MISSING, linkError, linkGone, takeToken } from "@/lib/emailLinks";
import login from "../login/login.module.css";

const WORDS = {
  reset: {
    title: "Choose a new password",
    lead: "Every browser signed in to your account is signed out when you save it.",
    done: "Your password is changed. Sign in with your new password.",
    send: resetPassword,
  },
  invite: {
    title: "Choose your password",
    lead: "Welcome to Kaagaz. Choose the password you will sign in with.",
    done: "Your password is set. Sign in with your email and your new password.",
    send: acceptInvite,
  },
} as const;

const IDS = { next: "link-password-new", again: "link-password-again" } as const;

export function LinkPasswordForm({ kind }: { kind: keyof typeof WORDS }) {
  const words = WORDS[kind];
  const hydrated = useHydrated();
  const token = useRef<string | null>(null);
  const taken = useRef(false);
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [errors, setErrors] = useState<{ next?: string; again?: string }>({});
  const [masked, setMasked] = useState({ next: true, again: true });
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => element?.focus(), []);

  useEffect(() => {
    if (taken.current) return; // once: the second run of a development double effect finds no token
    taken.current = true;
    token.current = takeToken();
    if (token.current === null) setFailure(LINK_MISSING);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked({ next: true, again: true });
    setFailure(null);
    // Change password's rules; a link has no current password, so that message is never used.
    const { next: nextProblem, again: againProblem } = validateNewPassword({ current: "", next, again });
    const found = { next: nextProblem, again: againProblem };
    const first = found.next ? "next" : found.again ? "again" : null;
    if (first) {
      flushSync(() => setErrors(found));
      document.getElementById(IDS[first])?.focus();
      return;
    }
    setErrors({});
    if (token.current === null) {
      setFailure(LINK_MISSING);
      return;
    }
    setBusy(true);
    try {
      await words.send(token.current, next);
      setDone(true);
    } catch (caught) {
      setFailure(linkError(caught, kind));
    }
    setBusy(false);
  }

  if (done) {
    return (
      <div className={login.form}>
        <div className={login.intro}>
          <h1 ref={focusOnMount} tabIndex={-1} className={login.title}>
            {words.title}
          </h1>
          <p className={login.lead} role="status">
            {words.done}
          </p>
        </div>
        <Link href="/login" className={`btn btn-primary btn-block ${login.submit}`}>
          Sign in
        </Link>
      </div>
    );
  }

  return (
    <form method="post" noValidate onSubmit={submit} className={login.form} aria-labelledby="link-password-title">
      <div className={login.intro}>
        <h1 id="link-password-title" className={login.title}>
          {words.title}
        </h1>
        <p className={login.lead}>{words.lead}</p>
      </div>
      <PasswordField
        id={IDS.next}
        label="New password"
        value={next}
        onChange={setNext}
        autoComplete="new-password"
        hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
        enterKeyHint="next"
        required
        error={errors.next ?? null}
        masked={masked.next}
        onMaskedChange={(value) => setMasked((was) => ({ ...was, next: value }))}
      />
      <PasswordField
        id={IDS.again}
        label="New password again"
        value={again}
        onChange={setAgain}
        autoComplete="new-password"
        enterKeyHint="done"
        required
        error={errors.again ?? null}
        masked={masked.again}
        onMaskedChange={(value) => setMasked((was) => ({ ...was, again: value }))}
      />
      {failure && (
        <div className={login.banner} role="alert">
          <Icon name="alert" />
          {failure === linkGone("reset") ? (
            <span>
              {LINK_GONE} Ask for a new one from <Link href="/forgot-password">Forgot password</Link>.
            </span>
          ) : (
            failure
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
        {busy ? "Saving…" : "Save password"}
      </button>
      <p className={login.switch}>
        <Link href="/login">Back to sign in</Link>
      </p>
    </form>
  );
}
