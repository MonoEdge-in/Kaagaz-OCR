"use client";

// "Ask documents | Search my stories" beside the scope chip (council 3 Oct 2026,
// "story search mode"): a segmented group of native radio buttons, as the theme
// toggle is, so the arrow keys move between them and screen readers say the
// choice and "1 of 2". It shares the theme toggle's styles: 48 px segments,
// the chosen one raised and outlined, never shown by colour alone. While a
// document answer is on its way the radios are disabled and the reason is
// shown. The texts are in lib/chatMode.ts.

import { useId } from "react";
import { CHAT_MODES, MODE_TEXTS } from "@/lib/chatMode";
import type { ChatMode } from "@/lib/chatMode";
import styles from "@/components/ui/ThemeToggle.module.css";

export function ModeSwitch({
  mode,
  locked,
  onChange,
}: {
  mode: ChatMode;
  /** Why it can't be switched now, or null. */
  locked: string | null;
  onChange: (mode: ChatMode) => void;
}) {
  const id = useId();
  return (
    <fieldset
      className={`${styles.group} ${styles.full}`}
      disabled={locked !== null}
      aria-describedby={locked !== null ? `${id}-locked` : undefined}
    >
      <legend className="visually-hidden">What to search</legend>
      <div className={styles.options}>
        {CHAT_MODES.map((option) => (
          <label key={option} className={styles.option}>
            <input
              type="radio"
              className={styles.radio}
              name={`${id}-mode`}
              value={option}
              checked={mode === option}
              onChange={() => onChange(option)}
            />
            <span>{MODE_TEXTS[option].option}</span>
          </label>
        ))}
      </div>
      {locked !== null ? (
        <p id={`${id}-locked`} className={styles.hint}>
          {locked}
        </p>
      ) : null}
    </fieldset>
  );
}
