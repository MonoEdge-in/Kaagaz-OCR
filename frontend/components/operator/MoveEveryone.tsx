"use client";

// Company sign-in for one organisation (#737), opened from its Manage menu. It says how many
// people are on company sign-in and how many still sign in the old way. "Move 12 people" (a named
// button: many people change at once) makes each of them an account, links it and emails them a
// link to set their password and authenticator. An email the company sign-in already has is never
// linked: it is listed for attention. The result line says what happened and what to do next; a
// setup email that did not go has its own Resend button. Safe to press again: people already
// moved are skipped.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { getSignInSummary, moveEveryone, resendOrgSetup } from "@/lib/api";
import type { MoveResult, Organisation, SignInSummary } from "@/lib/types";
import { moveButtonText, moveSummaryText, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

export function MoveEveryoneDialog({
  org,
  onClose,
  onDone,
}: {
  org: Organisation;
  onClose(): void;
  onDone(message: string): void;
}) {
  const [summary, setSummary] = useState<SignInSummary | null>(null);
  const [result, setResult] = useState<MoveResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSignInSummary(org.id).then(
      (reply) => !cancelled && setSummary(reply),
      (caught: unknown) => !cancelled && setFailure(operatorErrorMessage(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id]);

  async function move() {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const reply = await moveEveryone(org.id);
      setResult(reply);
      onDone(reply.message);
    } catch (caught) {
      setFailure(operatorErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function resend(userId: string) {
    setNotice("");
    try {
      setNotice((await resendOrgSetup(org.id, userId)).message);
    } catch (caught) {
      setNotice(operatorErrorMessage(caught));
    }
  }

  const waiting = summary?.to_move ?? 0;
  const canMove = summary !== null && summary.available && summary.status === "active" && waiting > 0 && !result;
  return (
    <ActionDialog title={`Company sign-in for ${org.name}`} onClose={onClose} canClose={() => !busy}>
      <div className={styles.dialogForm}>
        {summary && <p>{moveSummaryText(summary)}</p>}
        {!summary && !failure && <p className="muted">Loading…</p>}
        {summary && !summary.available && <p className="muted">Company sign-in is not set up on this server.</p>}
        {summary && summary.available && summary.status !== "active" && (
          <p className="muted">Enable the organisation first. Only an active organisation can be moved.</p>
        )}
        {canMove && (
          <p className="muted">
            Each person gets an email with a link to set their password and authenticator. It works for 12 hours.
            Anyone whose email already has a company account is left alone and listed for you.
          </p>
        )}
        {failure && (
          <p className="error-text" role="alert">
            {failure}
          </p>
        )}
        {result && (
          <div className="stack" role="status">
            <p>{result.message}</p>
            {result.attention.length > 0 && (
              <div>
                <p>Need attention:</p>
                <ul>
                  {result.attention.map((entry) => (
                    <li key={entry.email}>
                      <span className={styles.email}>{entry.email}</span> — {entry.reason}
                    </li>
                  ))}
                </ul>
                <p className="muted">Ask them to use another email, or remove the existing company account first.</p>
              </div>
            )}
            {result.email_not_sent.length > 0 && (
              <div>
                <p>Setup email not sent:</p>
                <ul>
                  {result.email_not_sent.map((entry) => (
                    <li key={entry.user_id}>
                      <span className={styles.email}>{entry.email}</span>{" "}
                      <button type="button" className="btn" onClick={() => void resend(entry.user_id)}>
                        Resend setup email
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {notice && <p>{notice}</p>}
          </div>
        )}
        <div className={styles.buttons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy} data-autofocus={!canMove || undefined}>
            Close
          </button>
          {canMove && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void move()}
              disabled={busy}
              aria-busy={busy || undefined}
              data-autofocus
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              {moveButtonText(waiting)}
            </button>
          )}
        </div>
      </div>
    </ActionDialog>
  );
}
