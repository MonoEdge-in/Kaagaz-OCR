// The sign-in page, in the shared sign-in frame (AuthFrame). The backend gives
// the same short message for an unknown e-mail, a wrong password or a disabled
// account, and the form shows it as is. A small link under the form goes to
// the separate admin sign-in (PRD A1). After signing out, or a session
// ending, a note under the form's heading says so (SignedOutNotice).

import Link from "next/link";
import { signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "./AuthFrame";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

export const metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const note = signedOutMessage(await searchParams);
  return (
    <AuthFrame>
      <SignInForm note={note} />
      <p className={styles.switch}>
        Account admin? <Link href="/admin/login">Admin sign-in</Link>
      </p>
    </AuthFrame>
  );
}
