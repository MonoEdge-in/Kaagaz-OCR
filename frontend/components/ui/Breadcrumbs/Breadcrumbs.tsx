// Where a page sits (Components.html, 09): each level links up, the last is the
// current page, in ink, marked aria-current.

import Link from "next/link";
import { Fragment } from "react";
import { Icon } from "../Icon/Icon";
import styles from "./Breadcrumbs.module.css";

export interface Crumb {
  label: string;
  /** Leave out on the last one: the page you are on. */
  href?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav className={styles.crumbs} aria-label="Breadcrumb">
      {items.map((item, index) => {
        const last = index === items.length - 1;
        return (
          <Fragment key={`${index}-${item.label}`}>
            {last || item.href === undefined ? (
              <span className={last ? styles.current : undefined} aria-current={last ? "page" : undefined}>
                {item.label}
              </span>
            ) : (
              <Link href={item.href}>{item.label}</Link>
            )}
            {last ? null : <Icon name="chev-right" />}
          </Fragment>
        );
      })}
    </nav>
  );
}
