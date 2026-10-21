// A checkbox with its words beside it; the whole line is the click target.

import type { InputHTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Checkbox.module.css";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: ReactNode;
}

export function Checkbox({ label, className, disabled, ...rest }: CheckboxProps) {
  return (
    <label className={cx(styles.check, disabled && styles.disabled, className)}>
      <input {...rest} type="checkbox" disabled={disabled} />
      {label}
    </label>
  );
}
