"use client";

// Timeout setup, a tab of /operator (Tushar, 18 Oct 2026, 0040): how long organisation admins
// and members stay signed in with no activity, 1 to 24 hours, for every organisation; one
// organisation's own value opens from its Manage menu (OrgSessionTimeoutDialog), with "Follow
// the platform". With hours set, a sign-in lasts 24 hours and this ends it; nothing saved is the
// default: the session's own length (SESSION_HOURS, ADMIN_SESSION_MINUTES). Every
// save asks first in ConfirmDialog with a fresh code (step-up). Super admin only.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Toast } from "@/components/ui/Toast";
import { getSessionTimeout, setSessionTimeout } from "@/lib/api";
import type { Organisation, SessionTimeout } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { LoadingRows } from "./LoadingRows";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

export const HOUR_CHOICES = Array.from({ length: 24 }, (_, index) => index + 1);
export const hoursText = (hours: number) => `${hours} ${hours === 1 ? "hour" : "hours"}`;
const EXPLAIN = "How long organisation admins and members stay signed in with no activity (up to 24 hours).";
const SAVE_ID = "session-timeout-save";

/** The null choice: the platform has no idle limit (sign-ins last their usual length, SESSION_HOURS
 * for members); an organisation follows the platform, whose value is said. */
export const noneLabel = (value: SessionTimeout, org: boolean) =>
  !org
    ? `No idle limit (sign-in lasts ${hoursText(value.default_hours)})`
    : `Follow the platform (${value.platform_hours ? hoursText(value.platform_hours) : "no idle limit"})`;

/** Saving a value: asked first, with a fresh code. ``none`` is the null choice's label. */
export function timeoutConfirm(hours: number | null, none: string, org?: Organisation): Confirm {
  const who = org ? `${org.name}'s admins and members` : "Organisation admins and members";
  const limit = hours === null ? none : hoursText(hours);
  const effect =
    hours !== null
      ? `${who} sign in again after ${limit} with no activity. Anyone already idle that long is signed out at their next step.`
      : org
        ? `${who} follow the platform's setting from now on.`
        : `${who} are not signed out for being inactive. Each sign-in lasts its usual length.`;
  return {
    title: `Set the sign-in timeout${org ? ` for ${org.name}` : ""} to ${limit}?`,
    effect,
    action: "Save",
    done: `The sign-in timeout${org ? ` for ${org.name}` : ""} is ${limit}.`,
    danger: false,
    stepUp: true,
    focusId: SAVE_ID,
    run: (code) => setSessionTimeout(hours, code, org?.id),
  };
}

/** The select (24 hours and the null choice) and Save, which opens the confirm. */
export function TimeoutForm(props: { saved: number | null; none: string; onSave(hours: number | null): void }) {
  const { saved, none, onSave } = props;
  const [choice, setChoice] = useState(saved === null ? "" : String(saved));
  const hours = choice === "" ? null : Number(choice);
  return (
    <>
      <div className="field">
        <label htmlFor="session-timeout">Sign-in timeout</label>
        <select id="session-timeout" className="input" value={choice} onChange={(event) => setChoice(event.target.value)}>
          <option value="">{none}</option>
          {HOUR_CHOICES.map((each) => (
            <option key={each} value={each}>
              {hoursText(each)}
            </option>
          ))}
        </select>
      </div>
      <div>
        <button id={SAVE_ID} type="button" className="btn btn-primary" disabled={hours === saved} onClick={() => onSave(hours)}>
          Save
        </button>
      </div>
    </>
  );
}

/** Loads the value of the platform (no org) or of one organisation, again after each save. */
function useTimeout(orgId?: string) {
  const [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<SessionTimeout | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    getSessionTimeout(orgId).then(
      (result) => !cancelled && setValue(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [orgId, attempt]);
  const failed = error?.attempt === attempt ? error.message : null;
  return { value, failed, reload: () => setAttempt((n) => n + 1) };
}

export function SessionTimeoutView() {
  const { value, failed, reload } = useTimeout();
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const none = value ? noneLabel(value, false) : "No idle limit";
  return (
    <section className={admin.section} aria-labelledby="session-timeout-heading">
      <h2 id="session-timeout-heading" className={admin.sectionHeading}>
        Timeout setup
      </h2>
      <p className={styles.summary}>{EXPLAIN}</p>
      {failed && !value && <LoadFailed what="the sign-in timeout" message={failed} onRetry={reload} />}
      {!value && !failed && <LoadingRows what="the sign-in timeout" />}
      {value && (
        <TimeoutForm key={String(value.hours)} saved={value.hours} none={none} onSave={(hours) => setChanging(timeoutConfirm(hours, none))} />
      )}
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setToast(message);
            reload();
          }}
        />
      )}
      <Toast message={toast} onClose={() => setToast(null)} />
    </section>
  );
}

/** The Manage menu's "Session timeout" for one organisation: super admin only. */
export function OrgSessionTimeoutDialog({ org, onClose }: { org: Organisation; onClose(): void }) {
  const { value, failed, reload } = useTimeout(org.id);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const none = value ? noneLabel(value, true) : "Follow the platform";
  return (
    <>
      <ActionDialog title={`Session timeout for ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className={styles.summary}>{EXPLAIN} This organisation only.</p>
          <p className="visually-hidden" role="status">
            {!value && !failed ? "Loading the sign-in timeout…" : ""}
          </p>
          {failed && !value && <LoadFailed what="the sign-in timeout" message={failed} onRetry={reload} />}
          {value && (
            <TimeoutForm
              key={String(value.hours)}
              saved={value.hours}
              none={none}
              onSave={(hours) => setChanging(timeoutConfirm(hours, none, org))}
            />
          )}
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
            reload();
          }}
        />
      )}
    </>
  );
}
