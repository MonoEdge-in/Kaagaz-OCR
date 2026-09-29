// /admin/login: the separate sign-in for customer admins (PRD A1). It sits
// outside the (app) group on purpose: nobody is signed in yet, so there is no
// session to load and no shell. One centred column at every width, with the
// same frame and theme switch as /login. The form gives an admin session,
// which alone opens /admin.

import { Wordmark } from "@/components/shell/Wordmark";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import login from "../../login/login.module.css";
import { AdminSignInForm } from "./AdminSignInForm";
import styles from "./adminLogin.module.css";

export default function AdminLoginPage() {
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
          <AdminSignInForm />
        </div>
      </main>
    </div>
  );
}
