"use client";

// Two-step for admins (#481, Tushar, 3 Nov 2026; 0068): one organisation's switch, opened from
// its Manage menu. Off by default. While on, every admin of that organisation must use two-step
// sign-in: one who turned it off sets it up before anything else (every route refuses them
// until then), and none can turn it off.
// Like a feature switch: a real checkbox with the switch role, and every flip asks first in
// ConfirmDialog with a fresh code (step-up). Super admin only.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import libraryStyles from "@/components/documents/Library.module.css";
import { Toast } from "@/components/ui/Toast";
import { getAdminTwoStep, setAdminTwoStep } from "@/lib/api";
import type { Organisation } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { afterDone, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

const SWITCH_ID = "admin-two-step-switch";
const HELP_ID = "admin-two-step-help";
export const ADMIN_TWO_STEP_HELP =
  "Every admin of this organisation must sign in with a password and a 6-digit code. An admin who turned it off sets it up before they can do anything else, and no admin can turn it off.";

/** Turning it on or off for one organisation: always asked first, always with a fresh code. */
export function adminTwoStepConfirm(org: Organisation, required: boolean): Confirm {
  return {
    title: `${required ? "Require" : "Stop requiring"} two-step sign-in for admins of ${org.name}?`,
    effect: required
      ? `An admin of ${org.name} without two-step sign-in must set it up before they can use Kaagaz again. Members are not affected.`
      : `Admins of ${org.name} can turn their own two-step sign-in off again. Anyone who has it on keeps it.`,
    action: required ? "Require it" : "Stop requiring it",
    done: `Two-step sign-in is ${required ? "required" : "no longer required"} for admins of ${org.name}.`,
    danger: !required,
    stepUp: true,
    focusId: SWITCH_ID,
    run: (code) => setAdminTwoStep(org.id, required, code),
  };
}

/** The Manage menu's "Two-step for admins" for one organisation. `onDone`: each change's words,
 * for the page's Toast too, so they outlast the dialog (as OrgSessionTimeoutDialog). */
export function OrgAdminTwoStepDialog({ org, onClose, onDone }: { org: Organisation; onClose(): void; onDone(message: string): void }) {
  const [attempt, setAttempt] = useState(0);
  const [required, setRequired] = useState<boolean | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAdminTwoStep(org.id).then(
      (result) => !cancelled && setRequired(result.required),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <>
      <ActionDialog title={`Two-step sign-in for admins of ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className="visually-hidden" role="status">
            {required === null && !failed ? "Loading the two-step setting…" : ""}
          </p>
          {failed && required === null && (
            <LoadFailed what="the two-step setting" message={failed} onRetry={() => setAttempt((n) => n + 1)} />
          )}
          {required !== null && (
            <label className={libraryStyles.check}>
              <input
                id={SWITCH_ID}
                type="checkbox"
                role="switch"
                checked={required}
                aria-describedby={HELP_ID}
                // Controlled: nothing changes until the question is answered (and the code is right).
                onChange={() => setChanging(adminTwoStepConfirm(org, !required))}
              />
              Require two-step sign-in for admins
            </label>
          )}
          <p id={HELP_ID} className={styles.summary}>
            {ADMIN_TWO_STEP_HELP}
          </p>
          <Toast message={done} onClose={() => setDone(null)} />
        </div>
      </ActionDialog>
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setDone(message);
            onDone(message);
            setAttempt((n) => n + 1);
          }}
        />
      )}
    </>
  );
}
