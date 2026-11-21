"use client";

// One organisation's admins, for the operator: name, e-mail, locked,
// disabled and the last sign-in. Never its members or reviewers
// (the list shows them as counts). Create admin (a fresh code; they are emailed a link to set
// their password and authenticator), and per admin Disable or Enable, Unlock, Resend setup email
// and Reset authenticator (a fresh code). No delete: admins are disabled instead. While the
// organisation is being deleted the list is read only, as the server. After a
// change the focus goes to that admin's name, as their buttons may change.

import { useEffect, useState } from "react";
import { useMinuteNow } from "@/components/chat/hooks";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { listAdmins } from "@/lib/api";
import type { OrgAdmin, Organisation } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { adminConfirm, adminFocusId } from "./confirms";
import { CreateDialog } from "./CreateDialog";
import { adminActions, adminFacts, afterDone, lastSignInText, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

const ACTION_LABELS = {
  disable: "Disable",
  enable: "Enable",
  unlock: "Unlock",
  "resend-setup": "Resend setup email",
  "reset-2fa": "Reset authenticator",
};

export function AdminsDialog({ org, onClose }: { org: Organisation; onClose(): void }) {
  const [attempt, setAttempt] = useState(0);
  const [admins, setAdmins] = useState<OrgAdmin[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState("");
  const now = useMinuteNow();

  useEffect(() => {
    let cancelled = false;
    listAdmins(org.id).then(
      (result) => !cancelled && setAdmins(result.items),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  const failed = error !== null && error.attempt === attempt ? error.message : null;
  const finished = (message: string, confirm?: Confirm) => {
    if (confirm) setChanging((open) => afterDone(open, confirm));
    setDone(message);
    setAttempt((n) => n + 1);
  };

  return (
    <>
      <ActionDialog title={`Admins of ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className="muted">Only this organisation&apos;s admins. Members and reviewers are counts on the list.</p>
          {org.status !== "deleting" && (
            <div>
              <button type="button" className="btn btn-primary" data-autofocus onClick={() => setCreating(true)}>
                Create admin
              </button>
            </div>
          )}
          <p className="visually-hidden" role="status">
            {done || (!admins && !failed ? "Loading the admins…" : "")}
          </p>
          {failed && (
            <div className="stack">
              <p className="error-text" role="alert">
                {failed}
              </p>
              <div>
                <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
                  Try again
                </button>
              </div>
            </div>
          )}
          {admins && (
            <ul className={styles.adminList} aria-label="Admins">
              {admins.map((admin) => (
                <li key={admin.id} className={styles.adminItem}>
                  <span className={styles.orgName} id={adminFocusId(admin.id)} tabIndex={-1}>
                    {admin.name}
                  </span>
                  <span className={styles.email} title={admin.email}>
                    {admin.email}
                  </span>
                  <span className={styles.counts}>
                    {adminFacts(admin).map((fact) => (
                      <span key={fact}>{fact}</span>
                    ))}
                  </span>
                  <span className="muted">{lastSignInText(admin.last_sign_in_at, now)}</span>
                  {/* 16px between buttons: Disable and Reset two-step sit beside Enable and Unlock. */}
                  <span className={`${styles.rowActions} ${styles.spaced}`}>
                    {adminActions(admin, org.status).map((action) => (
                      <button
                        key={action}
                        type="button"
                        className={action === "disable" || action === "reset-2fa" ? "btn btn-danger" : "btn"}
                        onClick={() => setChanging(adminConfirm(org.id, admin, action))}
                        aria-label={`${ACTION_LABELS[action]} ${admin.name}`}
                      >
                        {ACTION_LABELS[action]}
                      </button>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </ActionDialog>
      {creating && <CreateDialog org={org} onClose={() => setCreating(false)} onCreated={finished} />}
      {changing && <ConfirmDialog confirm={changing} onClose={() => setChanging(null)} onDone={(message) => finished(message, changing)} />}
    </>
  );
}
