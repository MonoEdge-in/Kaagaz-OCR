"use client";

// One value that is ready to verify: the spot on its page, zoomed in, whether the
// reader was not sure of it, a link to its page's scanned text, the two
// readings side by side, and three ways to settle it. The chosen value becomes
// the stored truth for this customer; the original file never changes.
// UI audit (16 Oct 2026): the badge says why it needs review (R2); Remember starts
// off (R4, no pre-ticked box); no second reading is one muted line (R5). In focus
// mode (R3, `onSkip` given) the card takes the keys 1, 2, E, S and Enter.

import Link from "next/link";
import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "@/components/ui/Icon";
import { Tip } from "@/components/ui/Tip";
import { PageViewer } from "@/components/viewer/PageViewer";
import { confidenceTip, fieldText, reasonText, voteText } from "@/components/viewer/labels";
import { CONFIDENCE_NOTE, confidenceText, notSure, routeOrigin } from "@/components/viewer/scanned";
import { ApiError, resolveReview } from "@/lib/api";
import { pageLink } from "@/lib/regions";
import type { ReviewChoice, ReviewItem } from "@/lib/types";
import { checkChoice, checkReasonsText, focusKeyAction, MAX_TYPED_LENGTH, reasonTag } from "./review";
import styles from "./Review.module.css";

/** The id of a card's heading; the queue moves focus there after a resolve. */
export function reviewTitleId(valueId: string): string {
  return `review-${valueId}-title`;
}

/** Where a key was pressed: in a text field, on a button, link or box, or elsewhere on the card. */
function keyTarget(target: EventTarget): "typing" | "control" | "other" {
  if (!(target instanceof HTMLElement)) return "other";
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return "typing";
  if (target instanceof HTMLInputElement) return target.type === "checkbox" ? "control" : "typing";
  return target.closest("button, a") ? "control" : "other";
}

export function ReviewCard({
  item,
  onResolved,
  onSkip,
}: {
  item: ReviewItem;
  onResolved: (before: ReviewItem, after: ReviewItem) => void;
  /** Focus mode (R3): Skip, and the keys. */
  onSkip?: () => void;
}) {
  const { value } = item;
  const [typed, setTyped] = useState("");
  const [remember, setRemember] = useState(false);
  const typedRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idBase = `review-${value.id}`;
  const errorId = `${idBase}-error`;
  const link = pageLink(item.document_id, value.region.page, value.region);
  const checks = checkReasonsText(item);
  const confidence = confidenceText(value.confidence, routeOrigin(item.page.route));

  const resolve = async (choice: ReviewChoice) => {
    if (busy) return; // Enter in the box while saving: never a second save
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

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const modifier = event.ctrlKey || event.metaKey || event.altKey;
    const action = focusKeyAction(event.key, keyTarget(event.target), modifier);
    if (action === null) return;
    event.preventDefault(); // E never types an "e" into the box it focuses
    if (action === "edit") typedRef.current?.focus();
    else if (action === "skip") onSkip?.();
    else void resolve(action === "save" ? "typed" : action);
  };

  return (
    <article
      className={onSkip ? `card motion-next ${styles.item}` : `card ${styles.item}`}
      aria-labelledby={reviewTitleId(value.id)}
      onKeyDown={onSkip ? onKeyDown : undefined}
    >
      <header className={styles.head}>
        <div className={styles.titleRow}>
          <h2 id={reviewTitleId(value.id)} className={styles.title} tabIndex={-1}>
            {fieldText(value.field)}
          </h2>
          {/* Every value here is ready to verify: the badge says why (R2), with its icon and
              dashed edge; its tooltip gives the confidence (Tushar, 16 Oct 2026). */}
          <Tip className="badge badge-review" text={confidenceTip(value.confidence ?? null)}>
            <Icon name="eye-alert" size={16} />
            <span className="visually-hidden">Ready to verify: </span>
            {reasonTag(value.label_reason)}
          </Tip>
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
        {value.votes && <p className={styles.reason}>{voteText(value.votes)}</p>}
        {checks && <p className={styles.reason}>{checks}</p>}
        {(confidence || item.page.unit !== "cells") && (
          <p className={styles.reason}>
            {confidence}
            {item.page.unit !== "cells" && (
              <>
                {confidence && " · "}
                <Link href={pageLink(item.document_id, value.region.page, value.region, { scanned: true })}>
                  See page {value.region.page}&apos;s scanned text
                </Link>
              </>
            )}
          </p>
        )}
        {notSure(value.confidence) && <p className={`muted ${styles.reason}`}>{CONFIDENCE_NOTE}</p>}
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
            aria-keyshortcuts={onSkip ? "1" : undefined}
            onClick={() => void resolve("first")}
          >
            Use first reading
          </button>
        </section>
        {value.second_reading !== null ? (
          <section className={styles.reading} aria-labelledby={`${idBase}-second`}>
            <h3 id={`${idBase}-second`} className={styles.readingHeading}>
              Second reading
            </h3>
            <p className={styles.readingText}>{value.second_reading}</p>
            <button
              type="button"
              className="btn"
              disabled={busy}
              aria-keyshortcuts={onSkip ? "2" : undefined}
              onClick={() => void resolve("second")}
            >
              Use second reading
            </button>
          </section>
        ) : null}
      </div>
      {value.second_reading === null ? (
        <p className={`muted ${styles.noReading}`}>No second reading for this value.</p>
      ) : null}

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
            ref={typedRef}
            id={`${idBase}-typed`}
            className={`input ${styles.typed}`}
            type="text"
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_TYPED_LENGTH}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            aria-describedby={error ? errorId : undefined}
            aria-keyshortcuts={onSkip ? "E" : undefined}
          />
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save
          </button>
        </div>
      </form>

      <label className={styles.remember}>
        <input
          id={`${idBase}-remember`}
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
        />
        <span>Remember this correction for future documents</span>
      </label>

      {onSkip ? (
        <div className={styles.focusBar}>
          <p className={`muted ${styles.keys}`}>
            Keys: <kbd>1</kbd> first reading · <kbd>2</kbd> second reading · <kbd>E</kbd> type it ·{" "}
            <kbd>S</kbd> skip · <kbd>Enter</kbd> save
          </p>
          <button type="button" className="btn" disabled={busy} aria-keyshortcuts="S" onClick={onSkip}>
            Skip
          </button>
        </div>
      ) : null}

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
