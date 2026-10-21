// A document's sensitivity label (Components.html, 08): round, square, diamond
// or triangle, with the word, so it never relies on colour.

import { cx } from "../cx";
import styles from "./LabelChip.module.css";

/** The backend's sensitivity values (documents.sensitivity); internal is the default. */
export type Sensitivity = "public" | "internal" | "confidential" | "restricted";

export const SENSITIVITY_WORDS: Record<Sensitivity, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

export function LabelChip({ sensitivity }: { sensitivity: Sensitivity }) {
  return <span className={cx(styles.lab, styles[sensitivity])}>{SENSITIVITY_WORDS[sensitivity]}</span>;
}
