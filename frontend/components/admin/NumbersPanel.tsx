"use client";

// The headline numbers for this customer (UI audit AD4, 16 Oct 2026): four large
// ones (Total documents, Ready, Ready to verify, Failed), then small lists in plain
// words: the pipeline, questions and how they were answered, the pages read this
// month by how they were read (PRD 2, R6), and what is kept. A row that is 0 is
// left out; a list with nothing says so. Which reader or model read them is the
// operator's only (UI quality council, 15 Oct 2026); AI use is on the Usage tab.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Tip } from "@/components/ui/Tip";
import { ApiError, getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { breakdowns, kpiTiles } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_TILES = [0, 1, 2, 3];

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
        <>
          <dl className={styles.tiles}>
            {kpiTiles(stats).map((tile) => (
              <div key={tile.label} className={`${styles.tile} ${styles.kpi}`}>
                <dt className={styles.tileLabel}>{tile.tip ? <Tip text={tile.tip}>{tile.label}</Tip> : tile.label}</dt>
                <dd className={`${styles.tileValue} tabular`}>{tile.value}</dd>
                {tile.note && <dd className={styles.tileNote}>{tile.note}</dd>}
              </div>
            ))}
          </dl>
          <div className={styles.breakdowns}>
            {breakdowns(stats).map((group, index) => (
              <section key={group.title} className={styles.breakdown} aria-labelledby={`numbers-group-${index}`}>
                <h3 id={`numbers-group-${index}`} className={styles.tilesHeading}>
                  {group.title}
                </h3>
                {group.rows.length > 0 ? (
                  <dl className={styles.breakdownList}>
                    {group.rows.map((row) => (
                      <div key={row.label} className={styles.breakdownRow}>
                        <dt>{row.label}</dt>
                        <dd className="tabular">{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="muted">{group.none}</p>
                )}
              </section>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
