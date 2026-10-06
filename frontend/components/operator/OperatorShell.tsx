"use client";

// The frame of the operator (super admin) screens: a top bar with "Kaagaz ·
// Platform", the signed-in operator's e-mail, the theme switch and Sign out.
// Separate from the customer AppShell: no documents, chat or admin links.
// Sign out ends every session of this operator on the server, then a full
// page load to the sign-in page.

import { useState } from "react";
import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { ApiError, operatorLogout } from "@/lib/api";
import { OPERATOR_SIGN_IN, useOperator } from "@/lib/operatorSession";
import { leavePage } from "@/lib/sessionChannel";
import shell from "@/components/shell/Shell.module.css";
import styles from "./Operator.module.css";

export function OperatorShell({ children }: { children: ReactNode }) {
  const { operator } = useOperator();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signOut() {
    setBusy(true);
    setError(null);
    try {
      await operatorLogout();
    } catch (caught) {
      // Already signed out on the server (401): go to the sign-in page anyway.
      if (!(caught instanceof ApiError && caught.status === 401)) {
        setError("Could not sign out. Check your connection and try again.");
        setBusy(false);
        return;
      }
    }
    leavePage(`${OPERATOR_SIGN_IN}?signed_out=1`);
  }

  return (
    <>
      <a className={shell.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.topbar}>
        <span className={styles.brand}>Kaagaz · Platform</span>
        <span className={styles.who}>
          <span className="visually-hidden">Signed in as </span>
          {operator.email}
        </span>
        <ThemeToggle compact />
        <button type="button" className="btn" onClick={signOut} disabled={busy} aria-busy={busy || undefined}>
          {busy && <span className="spinner" aria-hidden="true" />}
          Sign out
        </button>
        {error && (
          <p className={`error-text ${styles.topbarError}`} role="alert">
            {error}
          </p>
        )}
      </header>
      <main id="main" className={styles.main}>
        {children}
      </main>
    </>
  );
}
