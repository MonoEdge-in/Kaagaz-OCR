"use client";

// What the sidebar (1024 px and up) and the menu drawer (below) both hold, in
// order: the links for this person's role, a spacer, the theme switch, who is
// signed in and for which customer, and Sign out. One component, so the two
// places always show the same things.

import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import type { Me } from "@/lib/types";
import { isCurrent, roleName } from "./nav";
import type { NavLink } from "./nav";
import styles from "./Shell.module.css";

export interface ShellMenuProps {
  links: readonly NavLink[];
  pathname: string;
  me: Me;
  signingOut: boolean;
  signOutError: string | null;
  onSignOut(): void;
  /** Called when a link is followed, so the drawer also closes on its own page's link. */
  onLinkClick?(): void;
}

export function ShellMenu({
  links,
  pathname,
  me,
  signingOut,
  signOutError,
  onSignOut,
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

      <button
        type="button"
        className={`btn btn-quiet btn-block ${styles.signOut}`}
        onClick={onSignOut}
        disabled={signingOut}
        aria-busy={signingOut || undefined}
      >
        <Icon name="logout" />
        <span>{signingOut ? "Signing out…" : "Sign out"}</span>
      </button>
      {signOutError && (
        <p className="error-text" role="alert">
          {signOutError}
        </p>
      )}
    </div>
  );
}
