// The sign-in page. From 900 px up it is two columns: a ledger-paper brand
// panel (static text only: no API call, no customer names or counts) and the
// form. Below 900 px it is the wordmark and the form. The backend gives the
// same short message for an unknown e-mail, a wrong password or a disabled
// account, and the form shows it as is.

import { Wordmark } from "@/components/shell/Wordmark";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

export default function LoginPage() {
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
          <SignInForm />
        </div>
      </main>
    </div>
  );
}
