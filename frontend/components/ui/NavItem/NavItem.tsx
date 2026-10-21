// The app's main places (desktop/Home.html sidebar, phone/PhoneHome.html tab
// bar). NavItem is one sidebar link: icon, name, and a count in review colours
// when there is work waiting (Review). NavLabel heads a group. PhoneTabBar is
// the phone's five tabs at the bottom. The current place is marked
// aria-current, and a count is said with its words ("23 to review").

import Link from "next/link";
import type { ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./NavItem.module.css";

export interface NavPlace {
  href: string;
  label: string;
  icon: KIconName;
  active?: boolean;
  /** Work waiting there: the Review count. Left out at 0. */
  count?: number;
  /** How the count is said: "to review". */
  countWords?: string;
}

function Count({ count, words, className }: { count: number; words?: string; className?: string }) {
  return (
    <span className={className}>
      {formatNumber(count)}
      {words ? <span className="visually-hidden"> {words}</span> : null}
    </span>
  );
}

export function NavItem({ href, label, icon, active, count, countWords }: NavPlace) {
  return (
    <Link href={href} className={cx(styles.item, active && styles.active)} aria-current={active ? "page" : undefined}>
      <Icon name={icon} />
      <span>{label}</span>
      {count ? <Count count={count} words={countWords} className={styles.count} /> : null}
    </Link>
  );
}

export function NavLabel({ children }: { children: ReactNode }) {
  return <span className={styles.label}>{children}</span>;
}

export function PhoneTabBar({ places }: { places: NavPlace[] }) {
  return (
    <nav className={styles.tabbar} aria-label="Main">
      {places.map((place) => (
        <Link
          key={place.href}
          href={place.href}
          className={cx(styles.tab, place.active && styles.tabActive)}
          aria-current={place.active ? "page" : undefined}
        >
          <span className={styles.tabIcon}>
            <Icon name={place.icon} />
            {place.count ? <Count count={place.count} words={place.countWords} className={styles.tabCount} /> : null}
          </span>
          {place.label}
        </Link>
      ))}
    </nav>
  );
}
