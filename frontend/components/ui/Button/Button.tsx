// A button that says exactly what happens (Components.html, 06). One primary per
// view. An icon-only button takes `label`, which becomes its accessible name.
// While loading it shows a spinner, keeps its words ("Saving…") and ignores
// presses, but stays focusable, so focus never jumps away mid-action.

import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger" | "danger-outline";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string | undefined> = {
  primary: styles.primary,
  secondary: undefined,
  quiet: styles.quiet,
  danger: styles.danger,
  "danger-outline": styles.dangerOutline,
};

interface Look {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: KIconName;
  /** Icon only: the button's name, said by screen readers. */
  label?: string;
  block?: boolean;
}

function classes({ variant = "secondary", size = "md", label, block }: Look, extra?: string, loading?: boolean) {
  return cx(
    styles.btn,
    VARIANTS[variant],
    size === "sm" && styles.sm,
    size === "lg" && styles.lg,
    label !== undefined && styles.icon,
    block && styles.block,
    loading && styles.loading,
    extra,
  );
}

function inside(icon: KIconName | undefined, size: ButtonSize, loading: boolean, children: ReactNode) {
  return (
    <>
      {icon && !loading ? <Icon name={icon} size={size === "sm" ? "sm" : "md"} /> : null}
      {children}
    </>
  );
}

export interface ButtonProps extends Look, Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  loading?: boolean;
}

export function Button({
  variant,
  size = "md",
  icon,
  label,
  block,
  loading = false,
  className,
  type = "button",
  onClick,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={classes({ variant, size, label, block }, className, loading)}
      aria-label={label}
      aria-busy={loading || undefined}
      onClick={loading ? (event) => event.preventDefault() : onClick}
    >
      {inside(icon, size, loading, children)}
    </button>
  );
}

export interface ButtonLinkProps extends Look, Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "aria-label" | "href"> {
  href: string;
}

/** A link that looks like a button: it goes somewhere ("Open page 2"). */
export function ButtonLink({ variant, size = "md", icon, label, block, className, href, children, ...rest }: ButtonLinkProps) {
  return (
    <Link {...rest} href={href} className={classes({ variant, size, label, block }, className)} aria-label={label}>
      {inside(icon, size, false, children)}
    </Link>
  );
}

/** A row of buttons that wraps on a narrow screen. */
export function ButtonGroup({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(styles.group, className)}>{children}</div>;
}
