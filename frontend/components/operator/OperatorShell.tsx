"use client";

// The frame of the operator (super admin) screens: a top bar with "Kaagaz ·
// Platform", the signed-in operator's e-mail, the theme switch, "Move two-step
// to a new device" (MoveTwoStepDialog: a handover) and Sign out.
// Separate from the customer AppShell: no documents, chat or admin links.
// Sign out ends every session of this operator on the server, tells the other
// operator tabs, then a full page load to the sign-in page.
// Issue 468: the offline line and the session's warning come first after the skip link.

import { useState } from "react";
import type { ReactNode } from "react";
import { OfflineBanner } from "@/components/shell/OfflineBanner";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { operatorLogout } from "@/lib/api";
import { SessionEndingNotice, announceSignOut, useOperator } from "@/lib/operatorSession";
import { leavePage } from "@/lib/sessionChannel";
import admin from "@/components/admin/Admin.module.css";
import shell from "@/components/shell/Shell.module.css";
import { signOut } from "./logic";
import { MoveTwoStepDialog } from "./MoveTwoStepDialog";
import { MOVE_TEXT } from "./twoStepMove";
import styles from "./Operator.module.css";

export function OperatorShell({ children }: { children: ReactNode }) {
  const { operator, session } = useOperator();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);

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
      <OfflineBanner />
      <SessionEndingNotice expiresAt={session.expires_at} />
      <header className={styles.topbar}>
        <span className={styles.brand}>Kaagaz · Platform</span>
        <span className={styles.who} title={operator.email}>
          <span className="visually-hidden">Signed in as </span>
          {operator.email}
        </span>
        <ThemeToggle compact />
        <button type="button" className="btn" onClick={() => setMoving(true)} aria-haspopup="dialog">
          {MOVE_TEXT.title}
        </button>
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
      {moving && <MoveTwoStepDialog onClose={() => setMoving(false)} />}
      <main id="main" className={styles.main}>
        {children}
      </main>
    </>
  );
}
