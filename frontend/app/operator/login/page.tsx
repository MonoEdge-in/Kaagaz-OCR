// /operator/login: the platform operator's (super admin's) sign-in. Outside
// app/(app) on purpose: that group's SessionProvider is for customer users,
// and an operator belongs to no organisation. The same frame as /admin/login.

import { signedOutMessage } from "@/components/shell/gate";
import { Wordmark } from "@/components/shell/Wordmark";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import login from "../../login/login.module.css";
import styles from "../../admin/login/adminLogin.module.css";
import { OperatorSignInForm } from "./OperatorSignInForm";

export default async function OperatorLoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const note = signedOutMessage(await searchParams);
  return (
    <div className={styles.page}>
      <div className={login.theme}>
        <ThemeToggle compact />
      </div>
      <main className={login.main}>
        <div className={login.formColumn}>
          <div className={styles.brand}>
            <Wordmark reader={false} />
          </div>
          <OperatorSignInForm note={note} />
        </div>
      </main>
    </div>
  );
}
