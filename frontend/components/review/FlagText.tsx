// A flagged word in running text (scan review UX rules, section 2): Check is a dotted
// underline and a "?", Review a solid underline and a "!", each with words for screen
// readers, so colour is never the only cue. Text decoration and a fixed-width glyph
// only: the text keeps its size, line height and spacing.

import type { WordFlag } from "@/lib/types";
import { flagLabel } from "./wordReview";
import styles from "./Review.module.css";

export function FlagText({ flag, text = flag.text }: { flag: Pick<WordFlag, "text" | "band" | "reason_text">; text?: string }) {
  return (
    <span className={styles.flagText} data-band={flag.band}>
      <bdi dir="auto" className="readText" aria-hidden="true">
        {text}
      </bdi>
      <span className="visually-hidden">{flagLabel(flag)}</span>
    </span>
  );
}
