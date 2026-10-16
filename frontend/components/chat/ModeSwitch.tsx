"use client";

// "Ask documents" or "Search my stories" (council 3 Oct 2026, "story search mode"):
// a quiet button in the composer's toolbar naming the mode, which opens a small
// panel (ComposerMenu.tsx) with the two as native radio buttons, so the arrow keys
// move between them and screen readers say the choice and "1 of 2". A click on one
// closes the panel. While a document answer is on its way the radios are disabled
// and the reason is shown. The texts are in lib/chatMode.ts.

import { useId } from "react";
import { CHAT_MODES, MODE_TEXTS } from "@/lib/chatMode";
import type { ChatMode } from "@/lib/chatMode";
import { closeMenu, ComposerMenu } from "./ComposerMenu";
import styles from "./Chat.module.css";

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
    <ComposerMenu label={MODE_TEXTS[mode].option} name="What to search" title="What to search">
      <fieldset
        className={styles.menuGroup}
        disabled={locked !== null}
        aria-describedby={locked !== null ? `${id}-locked` : undefined}
      >
        <legend className="visually-hidden">What to search</legend>
        {CHAT_MODES.map((option) => (
          <label key={option} className={styles.menuOption}>
            <input
              type="radio"
              name={`${id}-mode`}
              value={option}
              checked={mode === option}
              onChange={() => onChange(option)}
              // A pointer click picks and closes; the arrow keys only move the choice.
              onClick={(event) => {
                if (event.detail > 0) closeMenu(event.currentTarget);
              }}
            />
            {MODE_TEXTS[option].option}
          </label>
        ))}
        {locked !== null ? (
          <p id={`${id}-locked`} className={styles.menuHint}>
            {locked}
          </p>
        ) : null}
      </fieldset>
    </ComposerMenu>
  );
}
