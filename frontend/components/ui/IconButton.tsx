// A button that shows an icon, with its words either visible beside it
// (showLabel) or given to screen readers and the tooltip. It is a .btn, at
// least --control-h square (40 px, 44 px on touch), and never submits a form
// unless the caller says type="submit".

import type { ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import styles from "./IconButton.module.css";

export type IconButtonProps = {
  icon: IconName;
  label: string;
  showLabel?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label">;

export function IconButton({
  icon,
  label,
  showLabel = false,
  className,
  type = "button",
  title,
  ...rest
}: IconButtonProps) {
  const classes = ["btn", showLabel ? styles.withLabel : styles.iconOnly, className]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      aria-label={showLabel ? undefined : label}
      title={title ?? (showLabel ? undefined : label)}
    >
      <Icon name={icon} />
      {showLabel && <span>{label}</span>}
    </button>
  );
}
