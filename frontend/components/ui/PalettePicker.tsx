"use client";

// Colours: the six palettes as native radio buttons (arrow keys move between
// them, screen readers say "1 of 6"), each a small circle of its accent and its
// name. The choice is applied at once and saved in the palette cookie
// (lib/palette.ts); it changes the look for this person only.

import { useId } from "react";
import { PALETTE_CHOICES, PALETTE_LABELS } from "@/lib/palette";
import { usePalette } from "./PaletteProvider";
import styles from "./PalettePicker.module.css";

export function PalettePicker() {
  const { palette, setPalette } = usePalette();
  // Each picker on the page is its own radio group.
  const group = useId();
  return (
    <fieldset className={styles.group}>
      <legend className={styles.legend}>Colours</legend>
      <div className={styles.options}>
        {PALETTE_CHOICES.map((option) => (
          <label key={option} className={styles.option}>
            <input
              type="radio"
              className={styles.radio}
              name={group}
              value={option}
              checked={palette === option}
              onChange={() => setPalette(option)}
            />
            {/* The palette's own accent (tokens.css, --k-swatch-*), whatever palette is on. */}
            <span className={styles.swatch} style={{ background: `var(--k-swatch-${option})` }} aria-hidden="true" />
            <span>{PALETTE_LABELS[option]}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
