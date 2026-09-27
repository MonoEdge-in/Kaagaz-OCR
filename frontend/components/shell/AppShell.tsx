"use client";

// The frame around every signed-in screen: the product name, the links this
// person's role can use, who is signed in and for which customer, and Sign out.

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";
import { ApiError, logout } from "@/lib/api";
import { useSession } from "@/lib/session";
import { isCurrent, navLinks, roleName } from "./nav";
import styles from "./Shell.module.css";

export function AppShell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signOut() {
    setSigningOut(true);
    setError(null);
    try {
      await logout();
      router.replace("/login");
    } catch (caught) {
      // Already signed out on the server: go to the sign-in page anyway.
      if (caught instanceof ApiError && caught.status === 401) {
        router.replace("/login");
        return;
      }
      setError(caught instanceof ApiError ? caught.message : "Could not sign out.");
      setSigningOut(false);
    }
  }

  return (
    <>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.header}>
        <div className={styles.bar}>
          <Link href="/documents" className={styles.brand}>
            Kaagaz
          </Link>
          <nav aria-label="Main">
            <ul className={styles.links}>
              {navLinks(me.user.role).map((link) => {
                const current = isCurrent(link.href, pathname);
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      className={current ? `${styles.link} ${styles.current}` : styles.link}
                      aria-current={current ? "page" : undefined}
                    >
                      {link.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className={styles.who}>
            <div className={styles.whoText}>
              <span>
                {me.user.display_name}{" "}
                <span className="muted">({roleName(me.user.role)})</span>
              </span>
              <span className="muted">{me.customer.name}</span>
            </div>
            <button type="button" className="btn" onClick={signOut} disabled={signingOut}>
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
            {error && (
              <span className="error-text" role="alert">
                {error}
              </span>
            )}
          </div>
        </div>
      </header>
      <main id="main" className={styles.main}>
        {children}
      </main>
    </>
  );
}
