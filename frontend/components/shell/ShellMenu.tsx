"use client";

// What the sidebar (1024 px and up) and the menu drawer (below) both hold: the
// links for this person's role and session. One component, so the two places
// always show the same links. Who is signed in, the theme switch and the two
// sign-outs are in the header's account menu (AccountMenu, UI audit G6).

import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { isCurrent } from "./nav";
import type { NavLink } from "./nav";
import styles from "./Shell.module.css";

export interface ShellMenuProps {
  links: readonly NavLink[];
  pathname: string;
  /** Values waiting to be checked, on the Review link; 0 shows nothing. */
  toCheck: number;
  /** Called when a link is followed, so the drawer also closes on its own page's link. */
  onLinkClick?(): void;
}

export function ShellMenu({ links, pathname, toCheck, onLinkClick }: ShellMenuProps) {
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
                  <Icon name={link.icon} />
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
    </div>
  );
}
