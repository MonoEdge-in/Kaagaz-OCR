"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { PasswordField } from "@/components/ui/PasswordField";
import { startKeycloakLink } from "@/lib/api";
import { keycloakAuthorizationUrl, keycloakCallbackError } from "@/lib/keycloak";
import type { Me } from "@/lib/types";
import styles from "./WaysToSignIn.module.css";

function linkFailure(caught: unknown): string {
  if (caught instanceof Error && "status" in caught && caught.status === 429) {
    return "Too many cutover attempts. Wait a minute, then try again.";
  }
  if (caught instanceof Error && "status" in caught && caught.status === 400) {
    return "Your Kaagaz password or 6-digit code could not be confirmed. Check them and try again.";
  }
  if (caught instanceof Error && "code" in caught && caught.code === "mfa_enrol_required") {
    return "Set up two-step sign-in before moving to company sign-in.";
  }
  return "Could not start company sign-in. Try again in a minute.";
}

/** Explicit legacy-to-Keycloak cutover. Neither proof value survives this component or the redirect. */
export function KeycloakLinkPanel({ me, callbackError, linked }: { me: Me; callbackError: string | null; linked: boolean }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [failure, setFailure] = useState<string | null>(() => keycloakCallbackError(callbackError));
  const [busy, setBusy] = useState(false);

  if (me.user.auth_authority === "keycloak") {
    return (
      <section className={styles.cutover} aria-label="Company sign-in">
        {linked && (
          <p className={styles.done} role="status">
            Company sign-in is connected. Your old Kaagaz password and local authenticator code no longer work for sign-in.
          </p>
        )}
        <h2>Company sign-in</h2>
        <p>Your organization manages your sign-in and multi-factor authentication.</p>
      </section>
    );
  }

  if (!me.keycloak_enabled) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || password === "" || !accepted) return;
    const invalid = codeError(code);
    if (invalid) {
      setFailure(invalid);
      return;
    }
    setFailure(null);
    setBusy(true);
    try {
      const { url } = await startKeycloakLink(
        password,
        normalizeCode(code),
        "/settings/sign-in?keycloak=linked",
      );
      window.location.assign(keycloakAuthorizationUrl(url));
    } catch (caught) {
      setFailure(linkFailure(caught));
      setPassword("");
      setCode("");
      setBusy(false);
    }
  }

  return (
    <section className={styles.cutover} aria-labelledby="keycloak-cutover-title">
      <h2 id="keycloak-cutover-title">Move to company sign-in</h2>
      <p>Use your local Kaagaz password and current authenticator code to prove this account before linking it.</p>
      {!me.user.totp_enabled ? (
        <p className="hint">Set up required two-step sign-in first. It protects your existing Kaagaz sign-in while migration is in progress.</p>
      ) : (
        <form className="stack" method="post" noValidate onSubmit={(event) => void submit(event)}>
          <PasswordField
            id="keycloak-link-password"
            label="Current Kaagaz password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
          <CodeField
            id="keycloak-link-code"
            label="Current 6-digit authenticator code"
            value={code}
            onChange={setCode}
            error={null}
            hint="This proves your existing Kaagaz sign-in."
          />
          <label className={styles.cutoverConfirm}>
            <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
            I understand that after this move succeeds, my Kaagaz password and local authenticator code will stop working for sign-in.
          </label>
          {failure && <p className="error-text" role="alert">{failure}</p>}
          <button type="submit" className="btn btn-primary" disabled={busy || password === "" || !accepted} aria-busy={busy || undefined}>
            {busy ? "Opening company sign-in…" : "Verify and move to company sign-in"}
          </button>
        </form>
      )}
    </section>
  );
}
