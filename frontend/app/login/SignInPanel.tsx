// What the sign-in page shows: the heading, why you are here, what went wrong if something did,
// and the one button. No form: the company account's own page asks for the email, the password
// and the code.

import { Icon } from "@/components/ui/Icon/Icon";
import { keycloakCallbackError, keycloakLoginHref } from "@/lib/keycloak";
import { SignedOutNotice } from "./SignedOutNotice";
import styles from "./login.module.css";

export function SignInPanel({
  note,
  next,
  keycloakError = null,
}: {
  note: string | null;
  next: string;
  keycloakError?: string | null;
}) {
  const problem = keycloakCallbackError(keycloakError);
  return (
    <div className={styles.form}>
      <div className={styles.intro}>
        <h1 id="login-title" className={styles.title}>
          Sign in to Kaagaz
        </h1>
        <p className={styles.lead}>Use your company account. You will come back here when you are done.</p>
      </div>
      <SignedOutNotice message={note} />
      {problem && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {problem}
        </div>
      )}
      <a className={`btn btn-primary btn-block ${styles.submit}`} href={keycloakLoginHref(true, next) ?? "#"}>
        Sign in
      </a>
    </div>
  );
}
