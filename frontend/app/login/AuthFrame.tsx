// The frame of the three sign-in pages (/login, /admin/login, /operator/login).
// From 900 px up it is two columns: a ledger-paper panel that says what
// Kaagaz does (static text only: no API call, no customer names or counts)
// and the form. Below 900 px the panel is a short band with the wordmark and
// the value line, above the form. The one motion: the highlight, the chip and
// the dotted link between them fade in once.

import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import styles from "./login.module.css";

/** A small line icon for the facts at the bottom of the panel. */
function PointIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function AuthFrame({
  variant = "member",
  children,
}: {
  variant?: "member" | "admin" | "platform";
  children: ReactNode;
}) {
  const mark = (
    <p className={styles.mark}>
      Kaagaz
      {variant === "platform" && <span className={styles.markSub}> · Platform</span>}
    </p>
  );
  const value = (
    <p className={styles.value}>
      Ask your documents.
      <br />
      See the page behind every answer.
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
              <PointIcon>
                <rect x="5" y="10.5" width="14" height="10" rx="2" />
                <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
              </PointIcon>
              Each company&apos;s documents are kept apart.
            </li>
            <li>
              <PointIcon>
                <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5" />
              </PointIcon>
              Every answer shows the page it came from.
            </li>
            <li>
              <PointIcon>
                <circle cx="12" cy="12" r="8.5" />
                <path d="M3.5 12h17M12 3.5c2.5 2.5 2.5 14.5 0 17M12 3.5c-2.5 2.5-2.5 14.5 0 17" />
              </PointIcon>
              <span>
                Reads English, <span lang="hi">हिंदी</span> and <span lang="mr">मराठी</span> documents.
              </span>
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
        <p className={styles.footer}>&copy; 2026 MonoEdge</p>
      </main>
    </div>
  );
}
