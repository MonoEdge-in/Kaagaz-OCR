// Zoom for the page (Components.html, 15): out, the size now, in, and Fit
// width. The size is said as it changes.

import { Icon } from "../Icon/Icon";
import styles from "./Zoom.module.css";

export interface ZoomProps {
  /** 125 for 125%. */
  percent: number;
  onZoomOut(): void;
  onZoomIn(): void;
  onFit(): void;
  canZoomOut?: boolean;
  canZoomIn?: boolean;
}

export function Zoom({ percent, onZoomOut, onZoomIn, onFit, canZoomOut = true, canZoomIn = true }: ZoomProps) {
  return (
    <div className={styles.zoom} role="group" aria-label="Zoom">
      <button type="button" aria-label="Zoom out" disabled={!canZoomOut} onClick={onZoomOut}>
        <Icon name="zoom-out" />
      </button>
      <span className={styles.percent} aria-live="polite">
        {Math.round(percent)}%
      </span>
      <button type="button" aria-label="Zoom in" disabled={!canZoomIn} onClick={onZoomIn}>
        <Icon name="zoom-in" />
      </button>
      <button type="button" className={styles.fit} onClick={onFit}>
        Fit width
      </button>
    </div>
  );
}
