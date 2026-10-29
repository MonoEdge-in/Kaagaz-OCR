"use client";

// The Scanned text panel: each reader's lines on this page, as read, in order.
// Mounted only while open (no fetch before); a late reply for another page is
// dropped (key + cancelled). Plain React text, never sent to the model. Each
// line can also get a meaning in My words ("Add meaning", ViewerWords.tsx).
// Ensemble (feature 27): under a main reader's line that holds a voted value, the
// readers' vote in plain words ("2 of 3 readers read …", voteText). A main reader's line
// flagged for review carries its band (FlagText.tsx, scan review UX rules, section 2).
// Vrushit UAT (25 Oct 2026): a main reader's line with a flagged word offers Fix text (the
// review's Edit), and shows the words people fixed (withFixes).
// Vrushit UAT 2 (27 Oct 2026, #417): Fix text, or a press on such a line, opens a box under the
// line to type the word in its place (fixBox says what to type); Save sends it as the review's
// Edit, Cancel or Esc closes it, and the focus goes back to the line.
// #431: Fix text works on the line's word mark before the line's own flag (wordToFix), and while
// the box is open it is the row's one action.
// #443: the box also offers Confirm, which settles the word as it reads (the review's Confirm), so
// a stray mark such as a lone "." can be left as it is.

import { useEffect, useId, useRef, useState } from "react";
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
  FIX_MEMBER_NOTE,
  FIX_NOTE,
  fixBox,
  fixesFor,
  lineHolding,
  lineMarked,
  MAIN_READER,
  notSure,
  plural,
  readerHeading,
  secondReaderNote,
  truncatedText,
  visibleText,
  withFixes,
  wordToFix,
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
  // Vrushit UAT (25 Oct 2026): "how to replace the missing text?" A main reader's line holding a
  // flagged word ([?] too) offers Fix text (a reviewer); #417: the word typed is saved through this.
  onFix?: (flag: WordFlag, text: string) => Promise<{ ok: boolean; message: string }>;
  member?: boolean; // a member: told that only a reviewer or an admin can fix a word (FIX_MEMBER_NOTE)
  fixed?: WordFlag[]; // the page's words a person fixed: shown in their lines as fixed (withFixes)
}

type Loaded = { key: string; readings: PageReadings } | { key: string; httpStatus: number | null };

export function ScannedTextPanel(props: ScannedTextPanelProps) {
  const { documentId, page, voted = [], active, cited, onShow, onClose, onAddMeaning, headingRef, flags = [], onFix, member = false, fixed = [] } =
    props;
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [role, setRole] = useState<"first" | "second">("first");
  const [allFor, setAllFor] = useState<string | null>(null);
  // The fix box open (#417): the word's key, the text typed, saving, and what went wrong.
  const [fixing, setFixing] = useState<{ key: string; text: string; busy: boolean; error: string | null } | null>(null);
  const fixInput = useRef<HTMLInputElement>(null);
  const fixId = useId();
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
  // The checker is offered only when it read text (24 Oct 2026): on a scan it never has any
  // ("This page is a scan, so it carries no text of its own"), so only the main reader shows.
  const hasSecond = readers.some((r) => r.role === "second" && r.lines.length > 0);
  const reader = readers.find((r) => r.role === (hasSecond ? role : "first")) ?? readers[0];
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
  // A box pressed on the page (active): its line is shown, lit and scrolled to (24 Oct 2026).
  const holding = lineHolding(lines, active);
  const shown = allFor === listKey || holding >= FIRST_LINES ? lines : lines.slice(0, FIRST_LINES);
  const rows = useRef<(HTMLLIElement | null)[]>([]);
  useEffect(() => {
    if (holding < 0) return;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    rows.current[holding]?.scrollIntoView({ block: "nearest", behavior: calm ? "auto" : "smooth" });
  }, [holding, active]);
  const fixKey = fixing?.key ?? null;
  useEffect(() => fixInput.current?.focus(), [fixKey]); // the box opened: its field takes the keys

  const openFix = (flag: WordFlag, draft: string) =>
    setFixing((now) => (now?.key === flag.word_key ? now : { key: flag.word_key, text: draft, busy: false, error: null }));
  const closeFix = (index: number) => {
    setFixing(null);
    rows.current[index]?.querySelector("button")?.focus();
  };
  // Save sends the text typed; Confirm (asRead) sends each word's own text, so it is confirmed.
  const saveFix = async (flag: WordFlag, index: number, asRead = false) => {
    if (!onFix || !fixing || fixing.busy) return;
    const typed = fixing.text.trim();
    if (typed === "" && !asRead) return setFixing({ ...fixing, error: "Type the word you see on the page." });
    setFixing({ ...fixing, busy: true, error: null });
    let done = { ok: true, message: "" };
    for (const each of fixesFor(flag, flags)) if (done.ok) done = await onFix(each, asRead ? each.text : typed);
    if (done.ok) closeFix(index);
    else setFixing((now) => now && { ...now, busy: false, error: done.message });
  };

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
          {reader?.role === "first" && flags.length > 0 && (onFix || member) && (
            <p className={styles.muted}>{onFix ? FIX_NOTE : FIX_MEMBER_NOTE}</p>
          )}
          {truncated && <p className={styles.note}>{truncated}</p>}
          <div
            className={styles.linesBox}
            role="region"
            aria-label={`Lines read on page ${page.page_no}`}
          >
            {/* role: Safari reads a list drawn without markers as no list; the numbers are CSS's. */}
            <ol className={styles.lines} role="list">
              {shown.map((line, index) => {
                const marked = lineMarked(line, cited);
                const meta = confidenceText(line.confidence, line.origin);
                const first = reader?.role === "first";
                const flag = first ? flags.find((each) => sameRegion(each.region, line.region)) : undefined;
                const toFix = first && onFix ? wordToFix(flags, line) : undefined;
                const read = first ? withFixes(line, fixed) : line.text;
                const text = visibleText(read);
                // The line's own flag is asked for as the line shows now (#431: its words fixed).
                const ask = toFix ? fixBox(toFix.kind === "line" ? { text: read } : toFix) : null;
                const box = toFix && fixing?.key === toFix.word_key ? fixing : null;
                const boxId = `${fixId}-${index}`;
                return (
                  <li
                    key={index}
                    ref={(element) => {
                      rows.current[index] = element;
                    }}
                    className={[styles.line, marked ? styles.cited : "", index === holding ? styles.held : ""].join(" ")}
                    aria-current={index === holding ? "true" : undefined}
                  >
                    <div className={styles.lineRow}>
                      <button
                        type="button"
                        className={styles.lineButton}
                        aria-pressed={active !== null && sameRegion(active, line.region)}
                        onClick={() => {
                          onShow({ name: `line ${index + 1}`, region: line.region, label: null });
                          if (toFix && ask) openFix(toFix, ask.draft);
                        }}
                      >
                        {flag ? (
                          <FlagText flag={flag} text={text} />
                        ) : (
                          <bdi dir="auto" className="readText">
                            {text}
                          </bdi>
                        )}
                        {(marked || meta) && (
                          <span className={styles.lineMeta}>
                            {marked && <strong>{meta ? "Cited · " : "Cited"}</strong>}
                            {meta}
                          </span>
                        )}
                      </button>
                      {/* #431: one action. While the box is open it is the action (Save, Cancel or
                          Esc), so the row's Fix text steps aside rather than sit beside it. */}
                      {toFix && ask && !box && (
                        <button
                          type="button"
                          className={`btn btn-quiet ${styles.addMeaning}`}
                          aria-expanded={false}
                          onClick={() => openFix(toFix, ask.draft)}
                        >
                          Fix text<span className="visually-hidden">{` in line ${index + 1}`}</span>
                        </button>
                      )}
                      {/* While the box is open its row offers only the box: Add meaning steps aside too,
                          so it never slides under the pointer that pressed Fix text (owner, 29 Oct 2026). */}
                      {onAddMeaning && !box && (
                        <button
                          type="button"
                          className={`btn btn-quiet ${styles.addMeaning}`}
                          onClick={(event) => onAddMeaning(text, line.region, event.currentTarget)}
                        >
                          Add meaning<span className="visually-hidden">{` for line ${index + 1}`}</span>
                        </button>
                      )}
                    </div>
                    {toFix && ask && box && (
                      <form
                        id={boxId}
                        className={styles.fix}
                        onSubmit={(event) => {
                          event.preventDefault();
                          void saveFix(toFix, index);
                        }}
                      >
                        <label className={styles.fixLabel} htmlFor={`${boxId}-text`}>
                          {ask.hint}
                        </label>
                        <input
                          ref={fixInput}
                          id={`${boxId}-text`}
                          className={`input ${styles.fixInput}`}
                          type="text"
                          dir="auto"
                          autoComplete="off"
                          spellCheck={false}
                          value={box.text}
                          onChange={(event) => setFixing({ ...box, text: event.target.value, error: null })}
                          onKeyDown={(event) => {
                            if (event.key !== "Escape") return;
                            event.preventDefault();
                            event.stopPropagation(); // Esc here closes the box, not the panel
                            closeFix(index);
                          }}
                        />
                        {/* [Cancel] [Confirm] [Save] under the field; a narrow panel wraps them. */}
                        <div className={styles.fixActions}>
                          <button type="button" className="btn btn-quiet" onClick={() => closeFix(index)}>
                            Cancel
                          </button>
                          <div className={styles.fixSettle}>
                            <button type="button" className="btn" disabled={box.busy} onClick={() => void saveFix(toFix, index, true)}>
                              Confirm
                            </button>
                            <button type="submit" className="btn btn-primary" disabled={box.busy} aria-busy={box.busy || undefined}>
                              Save
                            </button>
                          </div>
                        </div>
                        {box.error && (
                          <p className="error-text" role="alert">
                            {box.error}
                          </p>
                        )}
                      </form>
                    )}
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
