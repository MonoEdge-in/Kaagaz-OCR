"use client";

// Shown instead of the app while the account has a temporary password (PRD
// A5): an admin made the account or reset the password, and the server refuses
// everything but this change, sign-out and "who am I" until it is done. On
// success the server re-signs this browser's cookie and signs out the others;
// onChanged() reloads who is signed in, and the app opens.
//
// It takes props rather than useSession() so lib/session.tsx can render it
// without an import cycle. Like the sign-in form: method="post", no name
// attributes, passwords masked again on submit and never trimmed.

import { usePathname } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { PasswordField } from "@/components/ui/PasswordField";
import { useHydrated } from "@/components/ui/useHydrated";
import { MIN_PASSWORD_LENGTH } from "@/components/admin/logic";
import { changePassword, logout } from "@/lib/api";
import { announceSession, leavePage } from "@/lib/sessionChannel";
import type { Me } from "@/lib/types";
import { signedOutPath } from "./gate";
import {
  NEW_PASSWORD_FIELD_IDS,
  firstInvalidNewPassword,
  setPasswordError,
  validateNewPassword,
} from "./setPassword";
import type {
  NewPasswordErrors,
  NewPasswordField,
  NewPasswordInput,
  SetPasswordError,
} from "./setPassword";
import { Wordmark } from "./Wordmark";
import styles from "./Shell.module.css";

const ALL_MASKED: Record<NewPasswordField, boolean> = { current: true, next: true, again: true };

export function SetPasswordScreen({ me, onChanged }: { me: Me; onChanged(): void }) {
  const pathname = usePathname();
  const hydrated = useHydrated();
  const [input, setInput] = useState<NewPasswordInput>({ current: "", next: "", again: "" });
  const [errors, setErrors] = useState<NewPasswordErrors>({});
  const [masked, setMasked] = useState(ALL_MASKED);
  const [failure, setFailure] = useState<SetPasswordError | null>(null);
  const [busy, setBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  function change(field: NewPasswordField, value: string) {
    const next = { ...input, [field]: value };
    setInput(next);
    // Once an error shows, it updates as the person fixes it.
    if (errors[field]) setErrors((was) => ({ ...was, [field]: validateNewPassword(next)[field] }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(ALL_MASKED);
    setFailure(null);
    const found = validateNewPassword(input);
    const first = firstInvalidNewPassword(found);
    if (first) {
      flushSync(() => setErrors(found));
      document.getElementById(NEW_PASSWORD_FIELD_IDS[first])?.focus();
      return;
    }
    setErrors(found);
    setBusy(true);
    try {
      await changePassword(input.current, input.next);
      // Stays busy: this screen goes away once the session is reloaded.
      onChanged();
    } catch (caught) {
      setFailure(setPasswordError(caught));
      setBusy(false);
    }
  }

  async function signOut() {
    setSigningOut(true);
    try {
      await logout();
    } catch {
      // Already signed out, or unreachable: the sign-in page is right either way.
    }
    // A full page load, as in AppShell: nothing of this person stays in memory.
    announceSession(null);
    leavePage(signedOutPath(pathname));
  }

  const field = (id: NewPasswordField) => ({
    id: NEW_PASSWORD_FIELD_IDS[id],
    value: input[id],
    onChange: (value: string) => change(id, value),
    error: errors[id] ?? null,
    masked: masked[id],
    onMaskedChange: (value: boolean) => setMasked((was) => ({ ...was, [id]: value })),
  });

  return (
    <main className={styles.centered}>
      <div className={`card ${styles.centeredBox} ${styles.gate}`}>
        <Wordmark reader={false} />
        <form
          method="post"
          noValidate
          onSubmit={submit}
          className={styles.gateForm}
          aria-labelledby="set-password-title"
        >
          <div className={styles.gateIntro}>
            <h1 id="set-password-title" className={styles.gateTitle}>
              Set a new password
            </h1>
            <p className={styles.gateLead}>
              You signed in with a temporary password. Choose your own to keep going. Your other
              devices are signed out when you save it.
            </p>
          </div>

          <PasswordField
            {...field("current")}
            label="Temporary password"
            autoComplete="current-password"
            enterKeyHint="next"
            required
          />
          <PasswordField
            {...field("next")}
            label="New password"
            autoComplete="new-password"
            hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
            enterKeyHint="next"
            required
          />
          <PasswordField
            {...field("again")}
            label="New password again"
            autoComplete="new-password"
            enterKeyHint="done"
            required
          />

          {failure && (
            <div className={styles.gateBanner} role="alert">
              {failure.message}
            </div>
          )}

          <button
            type="submit"
            className={`btn btn-primary btn-block ${styles.gateSubmit}`}
            disabled={!hydrated || busy || signingOut}
            aria-busy={busy || undefined}
          >
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Saving…" : "Save new password"}
          </button>
        </form>

        <div className={styles.gateFooter}>
          <p className={styles.userMeta}>Signed in as {me.user.email}</p>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={signOut}
            disabled={signingOut || busy}
            aria-busy={signingOut || undefined}
          >
            {failure?.signedOut ? "Go to sign-in" : signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </div>
    </main>
  );
}
