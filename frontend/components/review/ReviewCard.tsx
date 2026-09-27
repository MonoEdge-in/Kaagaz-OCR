"use client";

// One value that needs review: the spot on its page, zoomed in, the two
// readings side by side, and three ways to settle it. The chosen value becomes
// the stored truth for this customer; the original file never changes.

import Link from "next/link";
import { useState } from "react";
import { PageViewer } from "@/components/viewer/PageViewer";
import { fieldText, reasonText } from "@/components/viewer/labels";
import { ApiError, resolveReview } from "@/lib/api";
import { pageLink } from "@/lib/regions";
import type { ReviewChoice, ReviewItem } from "@/lib/types";
import { checkChoice, MAX_TYPED_LENGTH } from "./review";
import styles from "./Review.module.css";

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
      setError(caught instanceof ApiError ? caught.message : "Could not save this correction.");
      setBusy(false);
    }
  };

  return (
    <article className={`card stack ${styles.item}`} aria-labelledby={`${idBase}-title`}>
      <header className="stack">
        <h2 id={`${idBase}-title`}>{fieldText(value.field)}</h2>
        <p className="row muted">
          <span>{item.document_name}</span>
          <span>Page {value.region.page}</span>
          <span className="badge badge-review">Needs review</span>
          <span>{reasonText(value.label_reason)}</span>
        </p>
      </header>

      <PageViewer
        document={{ id: item.document_id, display_name: item.document_name, detected_type: item.detected_type }}
        page={item.page}
        highlights={[value.region]}
        crop={value.region}
      />
      <p>
        <Link href={pageLink(item.document_id, value.region.page, value.region)}>
          Open the full page
        </Link>
      </p>

      <div className={styles.readings}>
        <section className={`stack ${styles.reading}`} aria-labelledby={`${idBase}-first`}>
          <h3 id={`${idBase}-first`}>First reading</h3>
          <p className={styles.readingText}>{value.raw}</p>
          <div>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => resolve("first")}>
              Use first reading
            </button>
          </div>
        </section>
        <section className={`stack ${styles.reading}`} aria-labelledby={`${idBase}-second`}>
          <h3 id={`${idBase}-second`}>Second reading</h3>
          {value.second_reading !== null ? (
            <>
              <p className={styles.readingText}>{value.second_reading}</p>
              <div>
                <button type="button" className="btn btn-primary" disabled={busy} onClick={() => resolve("second")}>
                  Use second reading
                </button>
              </div>
            </>
          ) : (
            <p className="muted">No second reading for this value.</p>
          )}
        </section>
      </div>

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void resolve("typed");
        }}
      >
        <label htmlFor={`${idBase}-typed`}>Type the correct value</label>
        <div className="row">
          <input
            id={`${idBase}-typed`}
            className={`input ${styles.typed}`}
            type="text"
            maxLength={MAX_TYPED_LENGTH}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
          <button type="submit" className="btn" disabled={busy}>
            Use typed value
          </button>
        </div>
      </form>

      <div className="row">
        <input
          id={`${idBase}-remember`}
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
        />
        <label htmlFor={`${idBase}-remember`}>Remember this correction for future documents</label>
      </div>

      {busy ? <p className="muted">Saving…</p> : null}
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}
