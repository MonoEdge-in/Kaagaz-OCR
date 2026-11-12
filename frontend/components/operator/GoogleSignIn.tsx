"use client";

// Google sign-in (#507, 0074), super admin only. One organisation's, opened from its Manage menu
// (OrgGoogleSignInDialog): follow the platform, On or Off, with what Off does; its admin's own
// rules (the "Google only" roles and the company domains) shown read-only beside it. The
// platform's default sits on the Timeout setup tab (PlatformGoogleSignIn). Every change asks
// first in ConfirmDialog with a fresh code (step-up); it says the people who use Google there are
// signed out only when the value in force changes, as the server does (#507 review). The dialog
// shows the value in force, the server's switch included. Off is the way back in for an
// organisation whose admins locked themselves out.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import libraryStyles from "@/components/documents/Library.module.css";
import { ROLE_CHOICES } from "@/components/admin/googleRules";
import { Toast } from "@/components/ui/Toast";
import { getGoogleSignIn, getOrgGoogleSignIn, setGoogleSignIn, setOrgGoogleSignIn } from "@/lib/api";
import type { Organisation, OrgGoogleSignIn, Role } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { LoadingRows } from "./LoadingRows";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

/** The Manage menu's line under "Google sign-in", and the dialog's. */
export const GOOGLE_SIGNIN_LINE = "Let this organisation's people sign in with Google, or turn it off.";
export const OFF_HINT =
  "Off: everyone signs in with their password, even where admins chose Google only. Connected accounts are kept. People signed in with Google are signed out.";
const PLATFORM_LINE =
  "For every organisation that follows the platform. An organisation's own setting is in its Manage menu.";

const SAVE_ID = "google-signin-save";
const PLATFORM_SWITCH_ID = "google-signin-platform";
const OFF_HINT_ID = "google-signin-off-hint";

type Choice = "follow" | "on" | "off";
const CHOICES: readonly Choice[] = ["follow", "on", "off"];
const onOff = (on: boolean) => (on ? "On" : "Off");
const toOn = (choice: Choice): boolean | null => (choice === "follow" ? null : choice === "on");
const fromOn = (on: boolean | null): Choice => (on === null ? "follow" : on ? "on" : "off");

/** The three choices' labels, in CHOICES' order; following names the platform's value. */
export function choiceLabels(platformOn: boolean): string[] {
  return [`Follow the platform (${onOff(platformOn)})`, "On", "Off"];
}

/** "Admins, Members", or "None". */
export function rolesText(roles: readonly Role[]): string {
  const names = ROLE_CHOICES.filter((choice) => roles.includes(choice.role)).map((choice) => choice.label);
  return names.length > 0 ? names.join(", ") : "None";
}

/** "In force: On.", or Off, the server's switch included: its own words when that is what turns it off. */
export function inForceText(value: OrgGoogleSignIn): string {
  const chosen = value.on ?? value.platform_on;
  if (chosen && !value.effective) return "In force: Off. Google sign-in is off on this server.";
  return `In force: ${onOff(value.effective)}.`;
}

/** One organisation's change: always asked first, always with a fresh code. Its people are signed
 * out only when the organisation's value in force changes (the server's rule, its switch aside). */
export function orgGoogleConfirm(org: Organisation, on: boolean | null, value: OrgGoogleSignIn): Confirm {
  const base = { stepUp: true, focusId: SAVE_ID, run: (code: string) => setOrgGoogleSignIn(org.id, on, code) };
  const platformOn = value.platform_on;
  const changes = (on ?? platformOn) !== (value.on ?? platformOn);
  const signedOut = (words: string) => (changes ? ` ${words}` : "");
  if (on === null) {
    return {
      ...base,
      title: `Make ${org.name} follow the platform for Google sign-in?`,
      effect: `${org.name} follows the platform's setting, now ${onOff(platformOn)}.${signedOut("Anyone with a connected Google account is signed out.")}`,
      action: "Save",
      done: `${org.name} follows the platform for Google sign-in (${onOff(platformOn)}).`,
      danger: !platformOn,
    };
  }
  return on
    ? {
        ...base,
        title: `Turn Google sign-in on for ${org.name}?`,
        effect: `${org.name}'s people can sign in with Google, as its admin chose.${signedOut("Anyone with a connected Google account is signed out, and signs in again.")}`,
        action: "Turn on",
        done: `Google sign-in is on for ${org.name}.`,
        danger: false,
      }
    : {
        ...base,
        title: `Turn Google sign-in off for ${org.name}?`,
        effect: `Everyone at ${org.name} signs in with their password, even where admins chose Google only. Connected accounts are kept.${signedOut("People signed in with Google are signed out.")}`,
        action: "Turn off",
        done: `Google sign-in is off for ${org.name}.`,
        danger: true,
      };
}

/** The platform's default, for every organisation that follows it: asked first, with a fresh code. */
export function platformGoogleConfirm(on: boolean): Confirm {
  return {
    title: `Turn Google sign-in ${on ? "on" : "off"} for the platform?`,
    effect: on
      ? "Organisations that follow the platform can sign in with Google, as their admins chose. Anyone there with a connected Google account is signed out, and signs in again."
      : "Organisations that follow the platform sign in with their password only. Connected accounts are kept. People signed in with Google are signed out.",
    action: on ? "Turn on" : "Turn off",
    done: `Google sign-in is ${on ? "on" : "off"} for the platform.`,
    danger: !on,
    stepUp: true,
    focusId: PLATFORM_SWITCH_ID,
    run: (code) => setGoogleSignIn(on, code),
  };
}

/** The three choices and Save, which opens the confirm. */
function ChoiceForm({ value, onSave }: { value: OrgGoogleSignIn; onSave(on: boolean | null): void }) {
  const saved = fromOn(value.on);
  const [choice, setChoice] = useState<Choice>(saved);
  const labels = choiceLabels(value.platform_on);
  return (
    <>
      <fieldset className={admin.settingsGroup}>
        <legend className={admin.settingsLegend}>Google sign-in</legend>
        {CHOICES.map((each, index) => (
          <label key={each} className={libraryStyles.check}>
            <input
              type="radio"
              name="google-signin"
              value={each}
              checked={choice === each}
              aria-describedby={each === "off" ? OFF_HINT_ID : undefined}
              onChange={() => setChoice(each)}
            />
            {labels[index]}
          </label>
        ))}
        <p id={OFF_HINT_ID} className="hint">
          {OFF_HINT}
        </p>
      </fieldset>
      <p className="hint">{inForceText(value)}</p>
      <p className="hint">Google only, set by its admin: {rolesText(value.google_only_roles)}.</p>
      <p className="hint">Company domains: {value.domains.length > 0 ? value.domains.join(", ") : "any"}.</p>
      <div>
        <button id={SAVE_ID} type="button" className="btn btn-primary" disabled={choice === saved} onClick={() => onSave(toOn(choice))}>
          Save
        </button>
      </div>
    </>
  );
}

/** The Manage menu's "Google sign-in" for one organisation. `onDone`: each change's words, for the
 * page's Toast too, so they outlast the dialog (as OrgSessionTimeoutDialog). */
export function OrgGoogleSignInDialog({ org, onClose, onDone }: { org: Organisation; onClose(): void; onDone(message: string): void }) {
  const [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<OrgGoogleSignIn | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOrgGoogleSignIn(org.id).then(
      (result) => !cancelled && setValue(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <>
      <ActionDialog title={`Google sign-in for ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className={styles.summary}>{GOOGLE_SIGNIN_LINE}</p>
          <p className="visually-hidden" role="status">
            {value === null && !failed ? "Loading the Google sign-in setting…" : ""}
          </p>
          {failed && value === null && (
            <LoadFailed what="the Google sign-in setting" message={failed} onRetry={() => setAttempt((n) => n + 1)} />
          )}
          {value && (
            <ChoiceForm
              key={String(value.on)}
              value={value}
              onSave={(on) => setChanging(orgGoogleConfirm(org, on, value))}
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
            onDone(message);
            setAttempt((n) => n + 1);
          }}
        />
      )}
    </>
  );
}

/** The platform's default, on the Timeout setup tab: a switch, every flip asked first. */
export function PlatformGoogleSignIn() {
  const [attempt, setAttempt] = useState(0);
  const [on, setOn] = useState<boolean | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGoogleSignIn().then(
      (result) => !cancelled && setOn(result.on),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  const reload = () => setAttempt((n) => n + 1);
  return (
    <section className={admin.section} aria-labelledby="google-signin-heading">
      <h2 id="google-signin-heading" className={admin.sectionHeading}>
        Google sign-in
      </h2>
      <p className={styles.summary}>{PLATFORM_LINE}</p>
      {failed && on === null && <LoadFailed what="the Google sign-in setting" message={failed} onRetry={reload} />}
      {on === null && !failed && <LoadingRows what="the Google sign-in setting" />}
      {on !== null && (
        <label className={libraryStyles.check}>
          <input
            id={PLATFORM_SWITCH_ID}
            type="checkbox"
            role="switch"
            checked={on}
            aria-describedby={OFF_HINT_ID}
            // Controlled: nothing changes until the question is answered (and the code is right).
            onChange={() => setChanging(platformGoogleConfirm(!on))}
          />
          Let people sign in with Google
        </label>
      )}
      <p id={OFF_HINT_ID} className="hint">
        {OFF_HINT}
      </p>
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
