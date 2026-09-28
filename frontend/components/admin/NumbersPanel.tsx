"use client";

// The headline numbers for this customer: documents by status, questions and
// how they were answered, feedback, and values waiting for review. Tiles in a
// grid that fits the width; 0 shows as 0.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { statTiles } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_TILES = [0, 1, 2, 3, 4, 5];

export function NumbersPanel() {
  const [attempt, setAttempt] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStats().then(
      (result) => {
        if (!cancelled) setStats(result);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError({
            attempt,
            message:
              caught instanceof ApiError
                ? caught.message
                : "Check your internet connection, then try again.",
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error !== null && error.attempt === attempt && !stats ? error.message : null;

  return (
    <section className={styles.section} aria-labelledby="numbers-heading">
      <h2 id="numbers-heading" className={styles.sectionHeading}>
        Numbers
      </h2>
      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the numbers.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!stats && !failed && (
        <>
          <p className="visually-hidden" role="status">
            Loading the numbers…
          </p>
          <div className={`delayed ${styles.tiles}`} aria-hidden="true">
            {SKELETON_TILES.map((tile) => (
              <span key={tile} className={`skeleton ${styles.skeletonTile}`} />
            ))}
          </div>
        </>
      )}
      {stats && (
        <dl className={styles.tiles}>
          {statTiles(stats).map((tile) => (
            <div key={tile.label} className={styles.tile}>
              <dt className={styles.tileLabel}>{tile.label}</dt>
              <dd className={`${styles.tileValue} tabular`}>{tile.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
