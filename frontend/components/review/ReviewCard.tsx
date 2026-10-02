"use client";

// One value that needs review: the spot on its page, zoomed in, the two
// readings side by side, and three ways to settle it. The chosen value becomes
// the stored truth for this customer; the original file never changes.

import Link from "next/link";
import { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { PageViewer } from "@/components/viewer/PageViewer";
import { fieldText, reasonText } from "@/components/viewer/labels";
import { ApiError, resolveReview } from "@/lib/api";
import { pageLink } from "@/lib/regions";
import type { ReviewChoice, ReviewItem } from "@/lib/types";
import { checkChoice, MAX_TYPED_LENGTH } from "./review";
import styles from "./Review.module.css";

/** The id of a card's heading; the queue moves focus there after a resolve. */
export function reviewTitleId(valueId: string): string {
  return `review-${valueId}-title`;
}

export function ReviewCard({
  item,
  onResolved,
}: {
  item: ReviewItem;
  onResolved: (before: ReviewItem, after: ReviewItem) => void;
}) {
  const { value } = item;
  const [typed, setTyped] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idBase = `review-${value.id}`;
  const errorId = `${idBase}-error`;
  const link = pageLink(item.document_id, value.region.page, value.region);

  const resolve = async (choice: ReviewChoice) => {
    const check = checkChoice(choice, typed, value.second_reading);
    if (!check.ok) {
      setError(check.message);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const after = await resolveReview(value.id, check.choice, remember, check.value);
      onResolved(item, after);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save this correction. Check your connection, then try again.",
      );
      setBusy(false);
    }
  };

  return (
    <article className={`card ${styles.item}`} aria-labelledby={reviewTitleId(value.id)}>
      <header className={styles.head}>
        <div className={styles.titleRow}>
          <h2 id={reviewTitleId(value.id)} className={styles.title} tabIndex={-1}>
            {fieldText(value.field)}
          </h2>
          <LabelBadge label="needs_review" />
        </div>
        <p className={styles.meta}>
          <Link href={link} className={styles.docLink}>
            {item.document_name}
            <span className="visually-hidden">{`, open page ${value.region.page}`}</span>
          </Link>
          <span aria-hidden="true">·</span>
          <span className="tabular">Page {value.region.page}</span>
        </p>
        <p className={styles.reason}>{reasonText(value.label_reason)}</p>
      </header>

      <PageViewer
        document={{
          id: item.document_id,
          display_name: item.document_name,
          detected_type: item.detected_type,
        }}
        page={item.page}
        highlights={[value.region]}
        crop={value.region}
      />

      <div className={styles.readings}>
        <section className={styles.reading} aria-labelledby={`${idBase}-first`}>
          <h3 id={`${idBase}-first`} className={styles.readingHeading}>
            First reading
          </h3>
          <p className={styles.readingText}>{value.raw}</p>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => void resolve("first")}
          >
            Use first reading
          </button>
        </section>
        <section className={styles.reading} aria-labelledby={`${idBase}-second`}>
          <h3 id={`${idBase}-second`} className={styles.readingHeading}>
            Second reading
          </h3>
          {value.second_reading !== null ? (
            <>
              <p className={styles.readingText}>{value.second_reading}</p>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void resolve("second")}
              >
                Use second reading
              </button>
            </>
          ) : (
            <p className={styles.noReading}>No second reading for this value.</p>
          )}
        </section>
      </div>

      <form
        className={styles.typedForm}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void resolve("typed");
        }}
      >
        <label htmlFor={`${idBase}-typed`} className="label">
          Type the correct value
        </label>
        <div className={styles.typedRow}>
          <input
            id={`${idBase}-typed`}
            className={`input ${styles.typed}`}
            type="text"
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_TYPED_LENGTH}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            aria-describedby={error ? errorId : undefined}
          />
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save
          </button>
        </div>
      </form>

      <div className={styles.remember}>
        <input
          id={`${idBase}-remember`}
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
        />
        <label htmlFor={`${idBase}-remember`}>Remember this correction for future documents</label>
      </div>

      {busy ? (
        <p className={styles.saving} role="status">
          <span className="spinner" aria-hidden="true" />
          Saving…
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className={`error-text ${styles.error}`} role="alert">
          <Icon name="alert" size={16} />
          {error}
        </p>
      ) : null}
    </article>
  );
}
