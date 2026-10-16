// The frame of the sign-in pages (/login, /operator/login) and
// /signup, in the MonoEdge look. From 900 px up it is two columns: a panel
// that says what Kaagaz does (static text only: no API call, no customer names
// or counts) and the form. Below 900 px the panel is a short band with the
// lockup and the value line, above the form. The one motion: the highlight,
// the chip and the dotted link between them fade in once.

import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import styles from "./login.module.css";

export function AuthFrame({
  variant = "member",
  children,
}: {
  variant?: "member" | "platform";
  children: ReactNode;
}) {
  const mark = (
    <p className={styles.mark}>
      Kaagaz
      {variant === "platform" && <span className={styles.markSub}> · Platform</span>}{" "}
      <span className={styles.by}>by MonoEdge</span>
    </p>
  );
  const value = (
    <p className={styles.value}>
      Ask your documents.
      <br />
      Kaagaz shows you the page.
    </p>
  );

  return (
    <div className={styles.page}>
      <div className={styles.theme}>
        <ThemeToggle compact />
      </div>

      <aside className={styles.brand} aria-label="About Kaagaz">
        <div className={styles.brandInner}>
          {mark}
          <div className={styles.mid}>
            {value}
            {/* A page with the cited line highlighted, and its answer marked Verified. */}
            <div className={styles.figure} aria-hidden="true">
              <div className={styles.sheet}>
                <i />
                <i />
                <b className={styles.sheetRule} />
                <i />
                <i />
                <i />
                <i />
                <i className={styles.hl} />
                <i />
                <i />
              </div>
              <div className={styles.card}>
                <span className={`badge badge-verified ${styles.pill}`}>
                  <Icon name="check-seal" size={14} />
                  Verified
                </span>
                <i />
                <i />
                <i />
                <span className={styles.chip}>1</span>
              </div>
              <svg className={styles.link} viewBox="0 0 360 214">
                <path d="M335 142 C 335 114, 250 114, 158 114" />
                <circle cx="158" cy="114" r="3" />
              </svg>
            </div>
          </div>
          <ul className={styles.points}>
            <li>
              <Icon name="file" />
              Every answer shows its source page.
            </li>
            <li>
              <Icon name="check-seal" />
              Every value is Verified or Needs review.
            </li>
            <li>
              <Icon name="search-off" />
              Not in your documents? It says so. It never guesses.
            </li>
          </ul>
        </div>
      </aside>

      <div className={styles.band}>
        {mark}
        {value}
      </div>

      <main className={styles.main}>
        <div className={styles.formColumn}>{children}</div>
      </main>
    </div>
  );
}
