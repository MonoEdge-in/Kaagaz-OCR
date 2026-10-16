// The signed-in admin's own two-step sign-in (2FA), in Admin, Settings. Every admin
// must have it (16 Oct 2026), so it is always on here: the panel says so and what to
// do about a lost phone. There is no "Turn off" (the server refuses it, 403).

import styles from "@/app/login/twoStep.module.css";
import admin from "@/components/admin/Admin.module.css";

export const TWO_STEP_ON =
  "Sign-in asks for your password and a 6-digit code from your authenticator app. Admins keep it on.";
export const LOST_PHONE =
  "New phone, or lost it? Another admin of your organisation can reset your two-step sign-in. " +
  "You then set it up again the next time you sign in.";

export function TwoStepPanel() {
  return (
    <section className="card stack" aria-labelledby="two-step-heading">
      <h2 id="two-step-heading" className={admin.sectionHeading}>
        Two-step sign-in
      </h2>
      <p className={styles.twoStepText}>
        Your two-step sign-in: <strong>On</strong>. {TWO_STEP_ON}
      </p>
      <p className={styles.note}>{LOST_PHONE}</p>
    </section>
  );
}
