// A switch that says its state in words beside the track ("On", "Off"), so the
// colour and the knob are never the only signal. It is a checkbox with
// role="switch", so it works with the keyboard and in a form.

import type { InputHTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Switch.module.css";

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "role" | "checked"> {
  label: ReactNode;
  checked: boolean;
}

export function Switch({ label, checked, className, ...rest }: SwitchProps) {
  return (
    <label className={cx(styles.switch, className)}>
      <input {...rest} type="checkbox" role="switch" checked={checked} />
      <span>{label}</span>
      <span className={styles.state} aria-hidden="true">
        {checked ? "On" : "Off"}
      </span>
    </label>
  );
}
