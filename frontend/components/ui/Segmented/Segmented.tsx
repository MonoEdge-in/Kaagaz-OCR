// Two small switches between options (Components.html, 09). Segmented is a
// group of pressed buttons, for a view choice (List / Grid, the theme).
// RadioPill is a radio group, for a choice in a form (Inside documents / Names).

import type { ReactNode } from "react";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Segmented.module.css";

export interface Option<T extends string> {
  value: T;
  label: ReactNode;
  icon?: KIconName;
}

interface ChoiceProps<T extends string> {
  /** Names the group: "View", "Theme", "Search in". */
  label: string;
  options: Option<T>[];
  value: T;
  onChange(value: T): void;
}

export function Segmented<T extends string>({ label, options, value, onChange }: ChoiceProps<T>) {
  return (
    <div className={styles.seg} role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.icon ? <Icon name={option.icon} size="sm" /> : null}
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function RadioPill<T extends string>({ label, options, value, onChange, name }: ChoiceProps<T> & { name: string }) {
  return (
    <div className={styles.radio} role="radiogroup" aria-label={label}>
      {options.map((option) => {
        const on = option.value === value;
        return (
          <label key={option.value} className={on ? styles.on : undefined}>
            <input type="radio" name={name} value={option.value} checked={on} onChange={() => onChange(option.value)} />
            {option.icon ? <Icon name={option.icon} size="sm" /> : null}
            {option.label}
          </label>
        );
      })}
    </div>
  );
}
