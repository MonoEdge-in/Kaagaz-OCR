"use client";

// The frame of the operator (super admin) screens: a top bar with "Kaagaz ·
// Platform", the signed-in operator's e-mail, the theme switch and Sign out.
// Separate from the customer AppShell: no documents, chat or admin links.
// Sign out ends every session of this operator on the server, tells the other
// operator tabs, then a full page load to the sign-in page.

import { useState } from "react";
import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { operatorLogout } from "@/lib/api";
import { announceSignOut, useOperator } from "@/lib/operatorSession";
import { leavePage } from "@/lib/sessionChannel";
import admin from "@/components/admin/Admin.module.css";
import shell from "@/components/shell/Shell.module.css";
import { signOut } from "./logic";
import styles from "./Operator.module.css";

export function OperatorShell({ children }: { children: ReactNode }) {
  const { operator } = useOperator();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSignOut() {
    setBusy(true);
    setError(null);
    const result = await signOut(operatorLogout); // a 401 here: already signed out, leave anyway
    if ("error" in result) {
      setError(result.error);
      setBusy(false);
      return;
    }
    announceSignOut();
    leavePage(result.leave);
  }

  return (
    <>
      <a className={shell.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.topbar}>
        <span className={styles.brand}>Kaagaz · Platform</span>
        <span className={styles.who} title={operator.email}>
          <span className="visually-hidden">Signed in as </span>
          {operator.email}
        </span>
        <ThemeToggle compact />
        <button
          type="button"
          className={`btn ${busy ? admin.busy : ""}`}
          onClick={onSignOut}
          disabled={busy}
          aria-busy={busy || undefined}
        >
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
