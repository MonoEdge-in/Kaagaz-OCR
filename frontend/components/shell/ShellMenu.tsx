"use client";

// What the sidebar (1024 px and up) and the menu drawer (below) both hold: the
// links for this person's role and session. One component, so the two places
// always show the same links. Who is signed in, the theme switch and the two
// sign-outs are in the header's account menu (AccountMenu, UI audit G6). The Review
// link carries no count (Tushar, 16 Oct 2026: a big number there makes people uneasy;
// the Review page itself has the list).

import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { isCurrent } from "./nav";
import type { NavLink } from "./nav";
import styles from "./Shell.module.css";

export interface ShellMenuProps {
  links: readonly NavLink[];
  pathname: string;
  /** Called when a link is followed, so the drawer also closes on its own page's link. */
  onLinkClick?(): void;
}

export function ShellMenu({ links, pathname, onLinkClick }: ShellMenuProps) {
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
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
