"use client";

// The headline numbers for this customer: documents by status, questions and
// how they were answered, feedback, and values waiting for review.

import { useEffect, useState } from "react";
import { ApiError, getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { statTiles } from "./logic";
import styles from "./Admin.module.css";

export function NumbersPanel() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStats().then(
      (result) => {
        if (!cancelled) setStats(result);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load the numbers.");
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="card stack" aria-labelledby="numbers-heading">
      <h2 id="numbers-heading">Numbers</h2>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!stats && !error && (
        <p className="muted" role="status">
          Loading…
        </p>
      )}
      {stats && (
        <dl className={styles.tiles}>
          {statTiles(stats).map((tile) => (
            <div key={tile.label} className={styles.tile}>
              <dt className="muted">{tile.label}</dt>
              <dd className={styles.tileValue}>{tile.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
