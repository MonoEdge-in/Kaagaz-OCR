"use client";

// The platform's Google sign-in default (#507, 0074), super admin only, on the Timeout setup tab.
// Each organisation's own switch is gone (#737): company sign-in (Keycloak) is the way in. Every
// change asks first in ConfirmDialog with a fresh code (step-up).

import { useEffect, useState } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import { Toast } from "@/components/ui/Toast";
import { getGoogleSignIn, setGoogleSignIn } from "@/lib/api";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { LoadingRows } from "./LoadingRows";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

export const OFF_HINT =
  "Off: everyone signs in with their password, even where admins chose Google only. Connected accounts are kept. People signed in with Google are signed out.";
const PLATFORM_LINE = "For every organisation. Turn it off to stop Google sign-in everywhere.";

const PLATFORM_SWITCH_ID = "google-signin-platform";
const OFF_HINT_ID = "google-signin-off-hint";

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
