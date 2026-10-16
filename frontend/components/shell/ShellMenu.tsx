"use client";

// What the sidebar (1024 px and up) and the menu drawer (below) both hold, in
// order: the links for this person's role and session, a spacer, the theme
// switch, who is signed in and for which customer, then the two ways to sign
// out: this browser only, or every browser and phone at once (PRD A4). One
// component, so the two places always show the same things.

import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import type { Me } from "@/lib/types";
import { isCurrent, roleName } from "./nav";
import type { NavLink } from "./nav";
import styles from "./Shell.module.css";

/** Which sign-out is running: this browser ("here") or all of them. */
export type SignOutKind = "here" | "everywhere";

export interface ShellMenuProps {
  links: readonly NavLink[];
  pathname: string;
  me: Me;
  /** Values waiting to be checked, on the Review link; 0 shows nothing. */
  toCheck: number;
  signingOut: SignOutKind | null;
  signOutError: string | null;
  onSignOut(): void;
  onSignOutEverywhere(): void;
  /** Called when a link is followed, so the drawer also closes on its own page's link. */
  onLinkClick?(): void;
}

export function ShellMenu({
  links,
  pathname,
  me,
  toCheck,
  signingOut,
  signOutError,
  onSignOut,
  onSignOutEverywhere,
  onLinkClick,
}: ShellMenuProps) {
  return (
    <div className={styles.menu}>
      <nav aria-label="Main">
        <ul className={styles.links}>
          {links.map((link) => {
            const current = isCurrent(link.href, pathname);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className={current ? `${styles.link} ${styles.current}` : styles.link}
                  aria-current={current ? "page" : undefined}
                  onClick={onLinkClick}
                >
                  <Icon name={link.icon} size={20} />
                  <span>{link.label}</span>
                  {link.toCheck && toCheck > 0 && (
                    <span className={styles.badge}>
                      <span className="visually-hidden">, </span>
                      {toCheck}
                      <span className="visually-hidden"> to check</span>
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className={styles.spacer} />

      <ThemeToggle />

      <div className={styles.user}>
        <p className={styles.userName}>{me.user.display_name}</p>
        <p className={styles.userMeta}>
          {roleName(me.user.role)} · {me.customer.name}
        </p>
      </div>

      <div className={styles.signOuts}>
        <button
          type="button"
          className={`btn btn-quiet btn-block ${styles.signOut}`}
          onClick={onSignOut}
          disabled={signingOut !== null}
          aria-busy={signingOut === "here" || undefined}
        >
          <Icon name="logout" />
          <span>{signingOut === "here" ? "Signing out…" : "Sign out"}</span>
        </button>
        <button
          type="button"
          className={`btn btn-quiet btn-block ${styles.signOut}`}
          onClick={onSignOutEverywhere}
          disabled={signingOut !== null}
          aria-busy={signingOut === "everywhere" || undefined}
        >
          <Icon name="logout" />
          <span>
            {signingOut === "everywhere" ? "Signing out everywhere…" : "Sign out everywhere"}
          </span>
        </button>
      </div>
      {signOutError && (
        <p className="error-text" role="alert">
          {signOutError}
        </p>
      )}
    </div>
  );
}
