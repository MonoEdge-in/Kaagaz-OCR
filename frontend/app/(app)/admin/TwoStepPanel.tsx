"use client";

// The signed-in admin's own two-step sign-in (2FA), in Admin, Settings: On or Off, and
// where to change it. Every admin must have it (16 Oct 2026) unless they turned their own
// off from their Account menu (Tushar, 30 Oct 2026; components/shell/TwoStepDialog.tsx),
// and what to do about a lost phone.

import styles from "@/app/login/twoStep.module.css";
import admin from "@/components/admin/Admin.module.css";
import { mayTurnOff, requiredText, twoStepView } from "@/components/shell/twoStepAccount";
import { useSession } from "@/lib/session";

export const TWO_STEP_ON =
  "Sign-in asks for your password and a 6-digit code from your authenticator app.";
export const TWO_STEP_OFF = "You sign in with your email and password only.";
export const CHANGE_IN_ACCOUNT = "Turn it off or on from your Account menu.";
export const LOST_PHONE =
  "New phone, or lost it? Another admin of your organisation can reset your two-step sign-in. " +
  "You then set it up again the next time you sign in.";

export function TwoStepPanel() {
  const { me } = useSession();
  if (me.user.auth_authority === "keycloak") {
    return (
      <section className="card stack" aria-labelledby="two-step-heading">
        <h2 id="two-step-heading" className={admin.sectionHeading}>Company sign-in</h2>
        <p className={styles.twoStepText}>Your organization manages sign-in and multi-factor authentication.</p>
      </section>
    );
  }
  const on = twoStepView(me) === "on";
  return (
    <section className="card stack" aria-labelledby="two-step-heading">
      <h2 id="two-step-heading" className={admin.sectionHeading}>
        Two-step sign-in
      </h2>
      <p className={styles.twoStepText}>
        Your two-step sign-in: <strong>{on ? "On" : "Off"}</strong>. {on ? TWO_STEP_ON : TWO_STEP_OFF}{" "}
        {/* QA U5: while the organisation requires it (#481) there is nothing to turn off. */}
        {on && !mayTurnOff(me) ? requiredText(me) : CHANGE_IN_ACCOUNT}
      </p>
      {on && <p className={styles.note}>{LOST_PHONE}</p>}
    </section>
  );
}
