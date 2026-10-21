// A loading shape (Components.html, 16): a soft bar with a sweep, sized by the
// screen to what is coming. It is only drawn; the screen marks the area it
// fills with aria-busy and names it ("Loading documents").

import type { CSSProperties } from "react";
import styles from "./Skeleton.module.css";

export interface SkeletonProps {
  width?: CSSProperties["width"];
  height: CSSProperties["height"];
  /** A file icon or a tile is less round than a line of text. */
  radius?: CSSProperties["borderRadius"];
  style?: CSSProperties;
}

export function Skeleton({ width, height, radius, style }: SkeletonProps) {
  return <span className={styles.skel} style={{ width, height, borderRadius: radius, ...style }} aria-hidden="true" />;
}
