"use client";

// "What is this?" at the top of the document panel (document panel UX, 25 Oct 2026): the
// card's AI summary, moved here from Details, under its caution, at most about five lines
// until "Show the full summary"; then up to five of the card's own facts, each with its
// label (Verified or Ready to verify) as a quiet mark, and under them one line with how many
// are not checked yet, linking a reviewer to Review (owner, 29 Oct 2026). Nothing is added or
// worked out. Devanagari runs carry lang="mr". No card yet: one quiet line while it is read; nothing when none will come.
// It rises in once, when it first appears (the CSS; never on a tab change: it sits above them).
// #453 (owner, 29 Oct 2026): a reviewer's value not checked yet has one ⋯ button at the row's end,
// which says so (a small dot, its name and tooltip) and opens Verify, Edit value… and Show on page.
// Each is one POST /review/{value_id}, as Review sends it (sendResolve); the reply's value replaces
// the old one here. A fact with a value behind it shows that value's words, the ones Verify confirms.

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { flushSync } from "react-dom";
import { RowMenu } from "@/components/admin/RowMenu";
import { scriptParts } from "@/components/chat/answer";
import { MAX_TYPED_LENGTH, sendResolve } from "@/components/review/review";
import { Icon as LabelIcon } from "@/components/ui/Icon";
import { Icon } from "@/components/ui/Icon/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { labelMarkIcon, labelTip } from "@/components/viewer/labels";
import { cardSections } from "@/components/viewer/panels";
import type { CardItem, Spot } from "@/components/viewer/panels";
import { beingRead } from "@/components/viewer/reprocess";
import { plural, valueAt } from "@/components/viewer/scanned";
import { sameRegion } from "@/components/viewer/selection";
import type { DocumentDetail, ExtractedValue, Region } from "@/lib/types";
import styles from "./DocumentView.module.css";

export const SUMMARY_TEXT = {
  heading: "Summary",
  caution: "Written by AI. Check it against the pages.",
  waiting: "The summary appears when reading finishes.",
  more: "Show the full summary",
  less: "Show less",
  /** Under the facts: a reviewer's count, with Review after it, and everyone else's. */
  toVerify: (count: number) => `${plural(count, "value")} to verify`,
  notChecked: (count: number) => `${plural(count, "value")} not checked yet`,
  review: "Review",
};

/** A value's ⋯ menu and its editor (#453), each menu item with its plain line. */
export const FACT_TEXT = {
  menu: (name: string) => `Not checked yet: actions for ${name}`,
  verify: "Verify",
  verifyHelp: "Confirms it reads right, as shown here.",
  edit: "Edit value…",
  editHelp: "Type the right value here and save it as verified.",
  show: "Show on page",
  showHelp: "Highlights where it is on the page.",
  field: (name: string) => `Correct value for ${name}`,
  cancel: "Cancel",
  save: "Save",
  verified: (text: string) => `Verified "${text}".`,
  saved: (text: string) => `Saved "${text}".`,
};

/**
 * The detail once a value is resolved, without asking for it again: the reply's value in its
 * place, and one more reviewer fix, as the resolve wrote one correction for it.
 */
export function withResolved(detail: DocumentDetail, value: ExtractedValue): DocumentDetail {
  return {
    ...detail,
    values: detail.values.map((each) => (each.id === value.id ? value : each)),
    reviewer_fixes: detail.reviewer_fixes + 1,
  };
}

export const MAX_FACTS = 5;
/** About five lines at the panel's width: a longer summary is cut there until opened. */
export const LONG_SUMMARY = 320;

/** Text with each Devanagari run marked lang="mr". Plain text only. */
export function ScriptText({ text }: { text: string }) {
  return (
    <>
      {scriptParts(text).map((part, index) =>
        part.lang ? (
          <span key={index} lang={part.lang}>
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

export function SummaryCard({
  detail,
  active,
  onShow,
  reviewer,
  onResolved,
  onStale,
}: {
  detail: DocumentDetail;
  active: Region | null;
  onShow: (spot: Spot) => void;
  reviewer: boolean; // may verify values in Review (canReview), so the count links there and each has its menu
  /** A value verified or saved here: the server's value, and what to say aloud. */
  onResolved: (value: ExtractedValue, message: string) => void;
  /** The server refused as the value had changed (404, 409): ask for the document again. */
  onStale: () => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const card = detail.card;
  if (card === null) {
    const coming = beingRead(detail.status) || detail.understanding === true;
    return coming ? <p className={styles.summaryWaiting}>{SUMMARY_TEXT.waiting}</p> : null;
  }
  const facts = cardSections(card, detail.values)
    .flatMap((section) => section.items)
    .slice(0, MAX_FACTS)
    .map((item) => ({ ...item, value: valueAt(detail.values, item.region) }));
  // A fact with no value behind it is Ready to verify too, as its mark says.
  const waiting = facts.filter((item) => item.value?.label !== "verified").length;
  if (!card.summary && facts.length === 0) return null;
  const long = (card.summary?.length ?? 0) > LONG_SUMMARY;

  return (
    <section className={styles.summaryCard} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className={styles.summaryHeading}>
        {SUMMARY_TEXT.heading}
      </h2>
      <p className={styles.caution}>{SUMMARY_TEXT.caution}</p>
      {card.summary && (
        <p id={`${id}-text`} className={styles.summaryText} data-clamped={long && !open ? "true" : undefined} data-open={open || undefined}>
          <ScriptText text={card.summary} />
        </p>
      )}
      {long && (
        <button
          type="button"
          className={`btn btn-quiet ${styles.expand}`}
          aria-expanded={open}
          aria-controls={`${id}-text`}
          onClick={() => setOpen(!open)}
        >
          {open ? SUMMARY_TEXT.less : SUMMARY_TEXT.more}
          <Icon name="chev-down" size="sm" className={styles.chevron} />
        </button>
      )}
      {facts.length > 0 && (
        <dl className={styles.keyFacts}>
          {facts.map((item, index) => (
            <KeyFact
              key={item.key}
              item={item}
              index={index}
              pressed={active !== null && sameRegion(active, item.region)}
              reviewer={reviewer}
              onShow={onShow}
              onResolved={onResolved}
              onStale={onStale}
            />
          ))}
        </dl>
      )}
      {waiting > 0 && (
        <p className={styles.toVerify}>
          <LabelIcon name={labelMarkIcon("needs_review")} size={16} />
          <span>{reviewer ? SUMMARY_TEXT.toVerify(waiting) : SUMMARY_TEXT.notChecked(waiting)}</span>
          {reviewer && (
            <>
              <span aria-hidden="true">·</span>
              <Link href={`/review?document_id=${encodeURIComponent(detail.id)}`}>{SUMMARY_TEXT.review}</Link>
            </>
          )}
        </p>
      )}
    </section>
  );
}

/**
 * One fact: its name (wrapping when long), its value on one line (cut short with "…", whole in
 * its title), and at the row's end the same slot on every row: a reviewer's ⋯ menu while the
 * value is not checked yet, else its mark (members, verified values, and a fact with no value
 * behind it). Edit value… puts a field with Cancel and Save in the value's place; Esc cancels.
 */
function KeyFact({
  item,
  index,
  pressed,
  reviewer,
  onShow,
  onResolved,
  onStale,
}: {
  item: CardItem & { value: ExtractedValue | undefined };
  index: number;
  pressed: boolean;
  reviewer: boolean;
  onShow: (spot: Spot) => void;
  onResolved: (value: ExtractedValue, message: string) => void;
  onStale: () => void;
}) {
  const { value } = item;
  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const row = useRef<HTMLDivElement>(null);
  const shown = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const errorId = useId();
  // The stored value's words, the ones Verify confirms and Edit starts from (a saved one as
  // saved); the card's own words only for a fact with no value behind it, which has no menu.
  const text = value ? value.value : item.text;
  const name = `${item.name} ${text}`;

  useEffect(() => {
    if (editing) field.current?.focus();
  }, [editing]);

  /** The row as it shows again, with `update` in the same render; focus on the value. */
  const settle = (update: () => void) => {
    flushSync(() => {
      setBusy(false);
      setEditing(false);
      update();
    });
    shown.current?.focus();
  };

  const cancel = () => {
    if (busy) return;
    flushSync(() => {
      setEditing(false);
      setError(null);
    });
    row.current?.querySelector<HTMLElement>('[aria-haspopup="menu"]')?.focus();
  };

  const send = async (entered: string, said: (text: string) => string) => {
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    const done = await sendResolve(value.id, "typed", entered, null, false);
    if (done.ok) {
      settle(() => onResolved(done.item.value, said(done.item.value.value)));
      return;
    }
    setError(done.message);
    if (done.stale) settle(onStale);
    else setBusy(false);
  };

  return (
    <div ref={row} className={styles.keyFact} style={{ "--i": index } as CSSProperties}>
      <dt>{item.name}</dt>
      <dd>
        {editing ? (
          <form
            className={styles.factEdit}
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void send(typed, FACT_TEXT.saved);
            }}
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              cancel();
            }}
          >
            <input
              ref={field}
              className={`input ${styles.factField}`}
              type="text"
              autoComplete="off"
              spellCheck={false}
              maxLength={MAX_TYPED_LENGTH}
              aria-label={FACT_TEXT.field(item.name)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
            />
            <button type="button" className="btn" disabled={busy} onClick={cancel}>
              {FACT_TEXT.cancel}
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {FACT_TEXT.save}
            </button>
          </form>
        ) : (
          <>
            <button
              ref={shown}
              type="button"
              className={`quiet-link ${styles.valueButton} ${styles.factValue}`}
              title={text}
              aria-pressed={pressed}
              onClick={() => onShow(item)}
            >
              <ScriptText text={text} />
              <span className="visually-hidden">{`, show on page ${item.region.page}`}</span>
            </button>
            <span className={styles.factEnd}>
              {reviewer && value?.label === "needs_review" ? (
                <RowMenu
                  compact
                  name={name}
                  status={{ label: FACT_TEXT.menu(name), tip: labelTip(value.label, value.label_reason), className: `${styles.factMenu}` }}
                  items={[
                    { label: FACT_TEXT.verify, description: FACT_TEXT.verifyHelp, onSelect: () => void send(text, FACT_TEXT.verified) },
                    {
                      label: FACT_TEXT.edit,
                      description: FACT_TEXT.editHelp,
                      onSelect: () => {
                        setTyped(text);
                        setError(null);
                        setEditing(true);
                      },
                    },
                    { label: FACT_TEXT.show, description: FACT_TEXT.showHelp, onSelect: () => onShow(item) },
                  ]}
                />
              ) : (
                <LabelBadge label={value?.label ?? "needs_review"} reason={value?.label_reason} />
              )}
            </span>
          </>
        )}
        {error && (
          <p id={errorId} className={`error-text ${styles.factError}`} role="alert">
            <LabelIcon name="alert" size={16} />
            {error}
          </p>
        )}
      </dd>
    </div>
  );
}
