"use client";

// The Scanned text panel: each reader's lines on this page, as read, in order.
// Mounted only while open (no fetch before); a late reply for another page is
// dropped (key + cancelled). Plain React text, never sent to the model. Each
// line can also get a meaning in My words ("Add meaning", ViewerWords.tsx).
// Ensemble (feature 27): under a main reader's line that holds a voted value, the
// readers' vote in plain words ("2 of 3 readers read …", voteText). A main reader's line
// flagged for review carries its band (FlagText.tsx, scan review UX rules, section 2).

import { useEffect, useState } from "react";
import { FlagText } from "@/components/review/FlagText";
import { ApiError, getPageReadings } from "@/lib/api";
import type { ExtractedValue, PageReadings, PageSummary, Region, WordFlag } from "@/lib/types";
import { voteText } from "./labels";
import type { Spot } from "./panels";
import {
  CHECKER,
  CONFIDENCE_NOTE,
  FIRST_LINES,
  confidenceText,
  emptyState,
  lineMarked,
  MAIN_READER,
  notSure,
  plural,
  readerHeading,
  secondReaderNote,
  truncatedText,
  visibleText,
} from "./scanned";
import type { PanelLoad } from "./scanned";
import { sameRegion } from "./selection";
import styles from "./ScannedText.module.css";

export const SCANNED_PANEL_ID = "scanned-text";

export interface ScannedTextPanelProps {
  documentId: string;
  page: PageSummary;
  voted?: ExtractedValue[]; // this page's values the readers voted on (Ensemble)
  active: Region | null; // the spot highlighted now
  cited: Region | null; // the cited spot a "See scanned text" link carried
  onShow: (spot: Spot) => void;
  onClose: () => void;
  onAddMeaning?: (text: string, region: Region, opener: HTMLElement) => void;
  headingRef?: (heading: HTMLHeadingElement | null) => void; // to move focus to the heading
  flags?: WordFlag[]; // the page's words still flagged for review: their lines carry the band
}

type Loaded = { key: string; readings: PageReadings } | { key: string; httpStatus: number | null };

export function ScannedTextPanel(props: ScannedTextPanelProps) {
  const { documentId, page, voted = [], active, cited, onShow, onClose, onAddMeaning, headingRef, flags = [] } = props;
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [role, setRole] = useState<"first" | "second">("first");
  const [allFor, setAllFor] = useState<string | null>(null);
  const key = `${documentId}:${page.page_no}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    getPageReadings(documentId, page.page_no)
      .then((readings) => {
        if (!cancelled) setLoaded({ key, readings });
      })
      .catch((error: unknown) => {
        const httpStatus = error instanceof ApiError ? error.status : null;
        if (!cancelled) setLoaded({ key, httpStatus });
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, page.page_no, key]);

  const current = loaded && loaded.key === key ? loaded : null;
  const readers = current && "readings" in current ? current.readings.readers : [];
  const reader = readers.find((r) => r.role === role) ?? readers[0];
  const hasSecond = readers.some((r) => r.role === "second");
  let load: PanelLoad = { status: "loading" };
  if (current) {
    load = "httpStatus" in current ? { status: "error", httpStatus: current.httpStatus } : { status: "ready", reader };
  }
  const empty = emptyState(load, page.route);
  const heading = reader ? readerHeading(reader, page) : null;
  const note = reader?.role === "second" ? secondReaderNote(page.route) : null;
  const truncated = reader ? truncatedText(reader) : null;
  const lines = reader?.lines ?? [];
  const listKey = `${key}:${reader?.role ?? ""}`;
  const shown = allFor === listKey ? lines : lines.slice(0, FIRST_LINES);

  return (
    <section
      id={SCANNED_PANEL_ID}
      className={styles.panel}
      aria-labelledby="scanned-heading"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <h2 id="scanned-heading" ref={headingRef} tabIndex={-1} className={styles.heading}>
        Scanned text, page {page.page_no}
      </h2>
      {hasSecond && (
        <fieldset className={styles.readers}>
          <legend className="visually-hidden">Which reader&apos;s text to show</legend>
          <label>
            <input
              type="radio"
              name="scanned-reader"
              checked={reader?.role === "first"}
              onChange={() => setRole("first")}
            />
            {MAIN_READER}
          </label>
          <label>
            <input
              type="radio"
              name="scanned-reader"
              checked={reader?.role === "second"}
              onChange={() => setRole("second")}
            />
            {CHECKER}
          </label>
        </fieldset>
      )}
      {heading && (
        <div>
          <p className={styles.title}>{heading.title}</p>
          {heading.notSureCount && <p className={styles.muted}>{heading.notSureCount}</p>}
        </div>
      )}
      {note && <p className={styles.note}>{note}</p>}
      <p className="visually-hidden" role="status">
        {empty ? empty.text : plural(lines.length, "line")}
      </p>
      {load.status === "loading" ? (
        <span className={`skeleton ${styles.skeleton}`} aria-hidden="true" />
      ) : empty ? (
        <div className={styles.empty}>
          <p>{empty.text}</p>
          {empty.retry && (
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          )}
        </div>
      ) : (
        <>
          {lines.some((line) => notSure(line.confidence)) && <p className={styles.muted}>{CONFIDENCE_NOTE}</p>}
          {truncated && <p className={styles.note}>{truncated}</p>}
          <div
            className={styles.linesBox}
            role="region"
            aria-label={`Lines read on page ${page.page_no}`}
          >
            <ol className={styles.lines}>
              {shown.map((line, index) => {
                const marked = lineMarked(line, cited);
                const meta = confidenceText(line.confidence, line.origin);
                const flag =
                  reader?.role === "first" ? flags.find((each) => sameRegion(each.region, line.region)) : undefined;
                return (
                  <li key={index} className={marked ? `${styles.line} ${styles.cited}` : styles.line}>
                    <div className={styles.lineRow}>
                      <button
                        type="button"
                        className={styles.lineButton}
                        aria-pressed={active !== null && sameRegion(active, line.region)}
                        onClick={() => onShow({ name: `line ${index + 1}`, region: line.region, label: null })}
                      >
                        {flag ? (
                          <FlagText flag={flag} text={visibleText(line.text)} />
                        ) : (
                          <bdi dir="auto" className="readText">
                            {visibleText(line.text)}
                          </bdi>
                        )}
                        {(marked || meta) && (
                          <span className={styles.lineMeta}>
                            {marked && <strong>{meta ? "Cited · " : "Cited"}</strong>}
                            {meta}
                          </span>
                        )}
                      </button>
                      {onAddMeaning && (
                        <button
                          type="button"
                          className={`btn btn-quiet ${styles.addMeaning}`}
                          onClick={(event) => onAddMeaning(visibleText(line.text), line.region, event.currentTarget)}
                        >
                          Add meaning<span className="visually-hidden">{` for line ${index + 1}`}</span>
                        </button>
                      )}
                    </div>
                    {reader?.role === "first" &&
                      voted.map((value) =>
                        value.votes && sameRegion(value.region, line.region) ? (
                          <p key={value.id} className={styles.note}>
                            <bdi dir="auto" className="readText">
                              {visibleText(voteText(value.votes))}
                            </bdi>
                          </p>
                        ) : null,
                      )}
                  </li>
                );
              })}
            </ol>
          </div>
          {shown.length < lines.length && (
            <button type="button" className="btn" onClick={() => setAllFor(listKey)}>
              Show all {lines.length.toLocaleString("en-IN")} lines
            </button>
          )}
        </>
      )}
    </section>
  );
}
