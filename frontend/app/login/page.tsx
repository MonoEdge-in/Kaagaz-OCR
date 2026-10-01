// The sign-in page. From 900 px up it is two columns: a ledger-paper brand
// panel (static text only: no API call, no customer names or counts) and the
// form. Below 900 px it is the wordmark and the form. The backend gives the
// same short message for an unknown e-mail, a wrong password or a disabled
// account, and the form shows it as is. A small link under the form goes to
// the separate admin sign-in (PRD A1). After signing out, or a session
// ending, a note above the form says so (SignedOutNotice).

import Link from "next/link";
import { signedOutMessage } from "@/components/shell/gate";
import { Wordmark } from "@/components/shell/Wordmark";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { SignedOutNotice } from "./SignedOutNotice";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const note = signedOutMessage(await searchParams);
  return (
    <div className={styles.page}>
      <div className={styles.theme}>
        <ThemeToggle compact />
      </div>

      <aside className={styles.brand} aria-label="About Kaagaz">
        <div className={styles.brandInner}>
          <div className={styles.brandVisual} aria-hidden="true">
            <span className={styles.pageRain}>
              <span />
              <span />
              <span />
              <span />
              <span />
            </span>
            <span className={`${styles.paperBird} ${styles.paperBirdOne}`} />
            <span className={`${styles.paperBird} ${styles.paperBirdTwo}`} />
            <span className={styles.sourceFiles}>
              <span className={`${styles.fileTile} ${styles.filePdf}`} />
              <span className={`${styles.fileTile} ${styles.fileImg}`} />
              <span className={`${styles.fileTile} ${styles.fileJpg}`} />
              <span className={`${styles.fileTile} ${styles.fileXlsx}`} />
              <span className={`${styles.fileTile} ${styles.fileCsv}`} />
            </span>
            <span className={styles.flowArrow} />
            <div className={styles.robot}>
              <span className={styles.robotAntenna} />
              <span className={styles.robotHead}>
                <span className={styles.robotEye} />
                <span className={styles.robotEye} />
              </span>
              <span className={styles.robotBody}>
                <span className={styles.robotScreen} />
              </span>
              <span className={styles.robotFoot} />
              <span className={`${styles.robotFoot} ${styles.robotFootRight}`} />
              <span className={styles.robotBubble}>
                <span />
                <span />
                <span />
              </span>
            </div>
            <span className={styles.outputCard}>
              <span />
              <span />
              <span />
            </span>
          </div>
        </div>
      </aside>

      <main className={styles.main}>
        <div className={styles.formColumn}>
          <div className={styles.smallBrand}>
            <Wordmark reader={false} />
          </div>
          <SignedOutNotice message={note} />
          <SignInForm />
          <p className={styles.adminLink}>
            <Link href="/admin/login">Admin sign-in</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
