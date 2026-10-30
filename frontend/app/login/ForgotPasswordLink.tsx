// Under the sign-in form (#457 phase 0): "Forgot password?" to /forgot-password while the
// server sends email; else, as before, ask the admin. ``open`` comes from the server page
// (signInOptions), so the line never changes once the page shows. Not on the operator's
// sign-in page: operators reset theirs on the server.

import Link from "next/link";
import styles from "./login.module.css";

export function ForgotPasswordLink({ open }: { open: boolean }) {
  return open ? (
    <p className={styles.help}>
      <Link href="/forgot-password">Forgot password?</Link>
    </p>
  ) : (
    <p className={styles.help}>Forgot your password? Ask your account admin to reset it.</p>
  );
}
