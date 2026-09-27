"use client";

// Light, Dark or System, as a segmented group of native radio buttons (so the
// arrow keys move between them and screen readers say "1 of 3"). The choice is
// applied at once and saved in the theme cookie (lib/theme.ts).

import { useId } from "react";
import { THEME_CHOICES, THEME_LABELS } from "@/lib/theme";
import type { ThemeChoice } from "@/lib/theme";
import { Icon } from "./Icon";
import type { IconName } from "./icons";
import { useTheme } from "./ThemeProvider";
import styles from "./ThemeToggle.module.css";

const THEME_ICONS: Record<ThemeChoice, IconName> = {
  light: "sun",
  dark: "moon",
  system: "monitor",
};

const THEME_DESCRIPTIONS: Record<ThemeChoice, string> = {
  light: "Light mode is fixed.",
  dark: "Dark mode is fixed.",
  system: "System mode follows your device setting.",
};

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { choice, setChoice } = useTheme();
  // Each toggle on the page is its own radio group.
  const group = useId();
  return (
    <fieldset className={`${styles.group} ${compact ? styles.compact : styles.full}`}>
      <legend className={compact ? "visually-hidden" : styles.legend}>Theme</legend>
      <div className={styles.options}>
        {THEME_CHOICES.map((option) => (
          <label
            key={option}
            className={styles.option}
            title={compact ? THEME_LABELS[option] : undefined}
          >
            <input
              type="radio"
              className={styles.radio}
              name={group}
              value={option}
              checked={choice === option}
              onChange={() => setChoice(option)}
            />
            <Icon name={THEME_ICONS[option]} size={compact ? 18 : 16} />
            <span className={compact ? "visually-hidden" : undefined}>{THEME_LABELS[option]}</span>
          </label>
        ))}
      </div>
      {!compact && <p className={styles.hint}>{THEME_DESCRIPTIONS[choice]}</p>}
    </fieldset>
  );
}
