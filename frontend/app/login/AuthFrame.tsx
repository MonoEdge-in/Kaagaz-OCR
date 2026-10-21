// The frame of the sign-in pages (/login, /operator/login) and /signup, in the
// handoff's layout (desktop/SignIn.html): the page on a dot field, and one card
// on it, with the brand panel in the accent on the left (the lockup, "कागज़ ·
// paper" and what Kaagaz promises; static text only: no API call, no customer
// names or counts) and the form on the right. Below 760 px the panel is a short
// band above the form. The one motion: the panel's words fade in once.

import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon/Icon";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import styles from "./login.module.css";

export function AuthFrame({
  variant = "member",
  children,
}: {
  variant?: "member" | "platform";
  children: ReactNode;
}) {
  return (
    <div className={styles.page}>
      <div className={styles.theme}>
        <ThemeToggle compact />
      </div>

      <div className={styles.shell}>
        <aside className={styles.brand} aria-label="About Kaagaz">
          <p className={styles.mark}>
            <span className={styles.markGlyph} aria-hidden="true" />
            <span className={styles.markWords}>
              <span className={styles.markName}>
                Kaagaz
                {variant === "platform" && <span className={styles.markSub}> · Platform</span>}
              </span>
              <span className={styles.by}>by MonoEdge</span>
            </span>
          </p>
          <div className={styles.brandWords}>
            <p className={styles.deva} lang="hi">
              कागज़ · paper
            </p>
            <p className={styles.value}>Your business papers, read and filed. Every answer shows its proof.</p>
          </div>
          <ul className={styles.points}>
            <li>
              <Icon name="file" size="sm" />
              Every answer shows its source page.
            </li>
            <li>
              <Icon name="check-circle" size="sm" />
              Every value is Verified or Ready to verify.
            </li>
            <li>
              <Icon name="not-found" size="sm" />
              Not in your documents? It says so. It never guesses.
            </li>
          </ul>
        </aside>

        <main className={styles.main}>
          <div className={styles.formColumn}>{children}</div>
        </main>
      </div>
    </div>
  );
}
