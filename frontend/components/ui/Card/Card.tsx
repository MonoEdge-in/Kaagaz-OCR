// Cards (Components.html, 11): a soft surface, flush for a list inside, dashed
// for a place still to fill ("Not in a department"). StatCard is a number with
// its name, LinkCard a card that goes somewhere (a department).

import Link from "next/link";
import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Card.module.css";

export type CardVariant = "default" | "flush" | "dashed";

const VARIANTS: Record<CardVariant, string | undefined> = {
  default: undefined,
  flush: styles.flush,
  dashed: styles.dashed,
};

export function Card({
  variant = "default",
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { variant?: CardVariant }) {
  return <div {...rest} className={cx(styles.card, VARIANTS[variant], className)} />;
}

/** A card's heading row; `bar` gives it its own band with a rule under it (a flush card). */
export function CardHead({ bar, children }: { bar?: boolean; children: ReactNode }) {
  return <div className={cx(styles.head, bar && styles.bar)}>{children}</div>;
}

export interface StatCardProps {
  label: string;
  value: ReactNode;
  /** A change, in words: "+148 this week". */
  delta?: ReactNode;
}

export function StatCard({ label, value, delta }: StatCardProps) {
  return (
    <div className={cx(styles.card, styles.stat)}>
      <span className="k-eyebrow">{label}</span>
      <span className="k-num">{value}</span>
      {delta ? (
        <span className={styles.delta}>
          <Icon name="send" size="sm" />
          {delta}
        </span>
      ) : null}
    </div>
  );
}

export interface LinkCardProps {
  href: string;
  icon: KIconName;
  title: ReactNode;
  meta: ReactNode;
  dashed?: boolean;
}

export function LinkCard({ href, icon, title, meta, dashed }: LinkCardProps) {
  return (
    <Link href={href} className={cx(styles.card, dashed && styles.dashed)}>
      <span className={styles.linkTitle}>
        <Icon name={icon} />
        <strong>{title}</strong>
      </span>
      <span className="k-small k-muted">{meta}</span>
    </Link>
  );
}

export function Divider() {
  return <hr className={styles.divider} />;
}
