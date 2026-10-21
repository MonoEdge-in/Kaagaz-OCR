// A banner says what happened and what to do (Components.html, 11): the
// headline in bold, then the detail, then the actions. Each tone has its own
// icon. A failure always carries its reason and its fix. `inline` puts one
// short action on the same line ("Linked as version 3 … Undo"); `compact` is
// the slim one inside a card ("4 files look like new versions. Review").

import type { ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Banner.module.css";

export type BannerTone = "info" | "warning" | "danger" | "version" | "quiet";

const ICONS: Record<BannerTone, KIconName> = {
  info: "info",
  warning: "alert",
  danger: "alert",
  version: "versions",
  quiet: "info",
};

export interface BannerProps {
  tone: BannerTone;
  /** The headline, in bold: "Couldn’t read Invoice_12.pdf page 3." */
  title?: ReactNode;
  /** The detail after it: what it means and what to do. */
  children?: ReactNode;
  /** The buttons, in a ButtonGroup or one Button. */
  actions?: ReactNode;
  /** Another icon than the tone's (a link for "Linked as version 3"). */
  icon?: KIconName;
  inline?: boolean;
  compact?: boolean;
  /** "alert" for a failure that just happened, "status" for news; left out for a banner already on the page. */
  role?: "alert" | "status";
}

export function Banner({ tone, title, children, actions, icon, inline, compact, role }: BannerProps) {
  return (
    <div className={cx(styles.banner, styles[tone], compact && styles.compact)} role={role}>
      <Icon name={icon ?? ICONS[tone]} />
      <div className={cx(styles.body, (inline || compact) && styles.inline)}>
        <span>
          {title ? <strong>{title}</strong> : null}
          {title && children ? " " : null}
          {children}
        </span>
        {actions}
      </div>
    </div>
  );
}
