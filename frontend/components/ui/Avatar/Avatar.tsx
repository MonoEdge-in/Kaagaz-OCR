// A person's initials in a circle. Decorative: the name is always written next
// to it or in the button it sits in.

import { cx } from "../cx";
import styles from "./Avatar.module.css";

/** "Priya Kulkarni" → "PK", "Rahul" → "RA", "" → "?", in capitals. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const letters = words.length === 1 ? Array.from(words[0]!).slice(0, 2) : [Array.from(words[0]!)[0], Array.from(words.at(-1)!)[0]];
  return letters.join("").toLocaleUpperCase("en-IN");
}

export function Avatar({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  return (
    <span className={cx(styles.avatar, size === "lg" && styles.lg)} aria-hidden="true">
      {initials(name)}
    </span>
  );
}
