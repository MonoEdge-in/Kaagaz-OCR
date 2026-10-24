"use client";

// Read clearly (Tushar, 19 Oct 2026): a clean, typed copy of the whole document, every page
// in order, beside the page image (under it on a phone). One request (GET clear-copy); the
// text is exactly as read, plain React text only (visibleText), grouped by clearCopy.ts.
// Words not read for sure have a dotted underline and a tooltip. Pressing a line highlights
// its box on the page. Copy all and Print (the copy alone, through a portal and globals.css).
// Translate follows Ask: with `canTranslate` off (Ask switched off or at None) it is not
// shown; a translation sits under its page with its own label, never mixed into the text.
// Document panel UX (25 Oct 2026): Copy all (it says "Copied") and Print in one quiet row; then
// the text with its own control above it, "Show the text in", which keeps the page in place.
// One heading a page ("Page 1"), a page jump when there are several. The line opened is lit
// like the spot on the page, with "The line you opened". A label line keeps its label and
// value in two columns. A table keeps whole words, scrolls sideways in its own frame (an edge
// and a note say so) and opens wide in a dialog.

import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Segmented } from "@/components/ui/Segmented/Segmented";
import { Toast } from "@/components/ui/Toast";
import { ApiError, getClearCopy, translatePage } from "@/lib/api";
import type { ClearCopy, PageTranslation, ReadLine, Region, TranslateLanguage } from "@/lib/types";
import {
  CLEAN_COPY,
  COPIED,
  COPY_BLOCKED,
  LANGUAGES,
  LEFT_OUT,
  MAX_TRANSLATE_PAGES,
  NOT_SURE_TIP,
  OPENED_NOTE,
  SHOW_IN,
  TRANSLATE_AT_ONCE,
  TRANSLATING,
  TRANSLATION_LABEL,
  WIDE_NOTE,
  blocks,
  copyText,
  pageOf,
  pageTitle,
  pairParts,
  parts,
  translateFailure,
} from "./clearCopy";
import type { Part } from "./clearCopy";
import type { Spot } from "./panels";
import { visibleText } from "./scanned";
import { sameRegion } from "./selection";
import styles from "./ReadClearly.module.css";

export interface ReadClearlyPanelProps {
  documentId: string;
  pageNo: number | null; // the page shown beside it: Translate starts there
  active: Region | null; // the spot highlighted now
  canTranslate: boolean;
  onShow: (spot: Spot) => void;
}

type Loaded = { key: string; copy: ClearCopy } | { key: string; failed: true };
type Done =
  | { status: "loading" }
  | { status: "failed"; text: string; retry: boolean }
  | { status: "done"; result: PageTranslation };

export function ReadClearlyPanel({ documentId, pageNo, active, canTranslate, onShow }: ReadClearlyPanelProps) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [language, setLanguage] = useState<TranslateLanguage | null>(null);
  const [done, setDone] = useState<Record<string, Done>>({});
  const [switchedOff, setSwitchedOff] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const reader = useRef<HTMLDivElement>(null);
  // A language picked: where the page shown was, so it stays there (keepPlace below).
  const keep = useRef<{ page: HTMLElement; top: number } | null>(null);
  const key = `${documentId}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    getClearCopy(documentId).then(
      (copy) => !cancelled && setLoaded({ key, copy }),
      () => !cancelled && setLoaded({ key, failed: true }),
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, key]);

  const current = loaded && loaded.key === key ? loaded : null;
  const pages = current && "copy" in current ? current.copy.pages : [];
  const doneKey = (lang: TranslateLanguage, page: number) => `${documentId}:${lang}:${page}`;
  const inFlight = useRef(new Set<string>());

  async function translate(lang: TranslateLanguage, wanted: number[]) {
    // In flight is tracked at once (a ref, not state), so a double press never sends a page twice.
    const queue = wanted.filter(
      (page) => !inFlight.current.has(doneKey(lang, page)) && done[doneKey(lang, page)]?.status !== "done",
    );
    const set = (page: number, value: Done) => setDone((now) => ({ ...now, [doneKey(lang, page)]: value }));
    queue.forEach((page) => {
      inFlight.current.add(doneKey(lang, page));
      set(page, { status: "loading" });
    });
    let next = 0;
    const worker = async () => {
      for (let page = queue[next++]; page !== undefined; page = queue[next++]) {
        try {
          set(page, { status: "done", result: await translatePage(documentId, page, lang) });
        } catch (error: unknown) {
          if (error instanceof ApiError && error.code === "feature_off") setSwitchedOff(true);
          set(page, { status: "failed", ...translateFailure(error instanceof ApiError ? error.code : undefined) });
        } finally {
          inFlight.current.delete(doneKey(lang, page));
        }
      }
    };
    await Promise.all(Array.from({ length: TRANSLATE_AT_ONCE }, worker));
  }

  const pick = (lang: TranslateLanguage | null) => {
    const first = pageNo ?? pages[0]?.page_no;
    const page = reader.current?.querySelector<HTMLElement>(`[data-page="${first}"]`);
    keep.current = page ? { page, top: page.getBoundingClientRect().top } : null;
    setLanguage(lang);
    if (lang && first !== undefined) void translate(lang, [first]);
  };

  // keepPlace: after a language change, <main> scrolls by what the page shown moved.
  useLayoutEffect(() => {
    const held = keep.current;
    keep.current = null;
    const moved = held ? held.page.getBoundingClientRect().top - held.top : 0;
    if (held && moved !== 0) held.page.closest("main")?.scrollBy({ top: moved });
  }, [language]);

  const text = (shown: Part[]) =>
    shown.map((part, index) =>
      part.unsure ? (
        <span key={index} className={styles.unsure} title={NOT_SURE_TIP}>
          {part.text}
          <span className="visually-hidden"> ({NOT_SURE_TIP})</span>
        </span>
      ) : (
        <Fragment key={index}>{part.text}</Fragment>
      ),
    );

  // A "Label: value" line keeps its label and value in two columns.
  const line = (each: ReadLine, n: number, shown: Part[]) => {
    const pressed = active !== null && sameRegion(active, each.region);
    const label = shown.filter((part) => part.label);
    return (
      <>
        <button
          type="button"
          className={label.length > 0 ? `${styles.line} ${styles.pair}` : styles.line}
          aria-pressed={pressed}
          onClick={() => onShow({ name: `line ${n}`, region: each.region, label: null })}
        >
          {label.length > 0 ? (
            <>
              <bdi dir="auto" className={styles.pairLabel}>
                {text(label)}
              </bdi>
              <bdi dir="auto">{text(shown.filter((part) => !part.label))}</bdi>
            </>
          ) : (
            <bdi dir="auto">{text(shown)}</bdi>
          )}
        </button>
        {pressed && <span className={styles.opened}>{OPENED_NOTE}</span>}
      </>
    );
  };

  const copy = pages.map((page) => {
    const marks = page.marks;
    let n = 0;
    const plain = (each: ReadLine) => line(each, ++n, parts(each, marks));
    const tr = language ? done[doneKey(language, page.page_no)] : undefined;
    const meta = LANGUAGES.find((each) => each.id === language);
    return (
      <section key={page.page_no} className={styles.page} data-page={page.page_no}>
        <h3 className={styles.pageTitle}>{pageTitle(page.page_no, pages.length)}</h3>
        {page.lines.length === 0 && <p className={styles.note}>No text was found on this page.</p>}
        {blocks(page.lines).map((block, index) => {
          if (block.type === "heading") return <h4 key={index}>{plain(block.line)}</h4>;
          if (block.type === "paragraph") return <p key={index}>{plain(block.line)}</p>;
          if (block.type === "pair") {
            return <p key={index}>{line(block.line, ++n, pairParts(block.line, marks, block.label))}</p>;
          }
          if (block.type === "list") {
            return (
              <ul key={index}>
                {block.items.map((item, at) => (
                  <li key={at}>{plain(item)}</li>
                ))}
              </ul>
            );
          }
          return (
            <TableFrame key={index} title={`Table, ${pageTitle(page.page_no, pages.length).toLowerCase()}`}>
              <table className={styles.table}>
                <tbody>
                  {block.rows.map((row, r) => (
                    <tr key={r}>
                      {row.map((cell, c) => {
                        const Cell = cell.some((each) => each.table?.header) ? "th" : "td";
                        return <Cell key={c}>{cell.map((each, at) => <Fragment key={at}>{plain(each)}</Fragment>)}</Cell>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableFrame>
          );
        })}
        {language && canTranslate && !switchedOff && !tr && page.lines.length > 0 && (
          <button type="button" className="btn btn-quiet" onClick={() => void translate(language, [page.page_no])}>
            Translate {pageOf(page.page_no, pages.length).toLowerCase()}
          </button>
        )}
        {language && canTranslate && !switchedOff && tr && (
          <div className={styles.translation} lang={meta?.lang}>
            <p className={styles.label}>{TRANSLATION_LABEL}</p>
            {tr.status === "loading" && <p className={styles.note}>{TRANSLATING}</p>}
            {tr.status === "failed" && (
              <p className={styles.note}>
                {tr.text}
                {tr.retry && " "}
                {tr.retry && (
                  <button type="button" className="btn btn-quiet" onClick={() => language && void translate(language, [page.page_no])}>
                    Try again
                  </button>
                )}
              </p>
            )}
            {tr.status === "done" &&
              tr.result.lines.map((text, at) => (
                <p key={at}>
                  <bdi dir="auto">{visibleText(text)}</bdi>
                </p>
              ))}
          </div>
        )}
      </section>
    );
  });

  const showTranslate = canTranslate && !switchedOff && pages.length > 0;
  const allPages = pages.slice(0, MAX_TRANSLATE_PAGES).map((page) => page.page_no);
  return (
    <section className={styles.panel} aria-labelledby="read-clearly-heading">
      <h2 id="read-clearly-heading" className={styles.heading} tabIndex={-1}>
        {CLEAN_COPY}
      </h2>
      <p className={styles.note}>The text exactly as read, page by page. Press a line to see it on the page.</p>
      <div className={styles.tools}>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={pages.length === 0}
          onClick={() =>
            void navigator.clipboard.writeText(copyText(pages)).then(
              () => setToast(COPIED),
              () => setToast(COPY_BLOCKED),
            )
          }
        >
          Copy all
        </button>
        <button type="button" className="btn btn-quiet" disabled={pages.length === 0} onClick={() => window.print()}>
          Print
        </button>
        {pages.length > 1 && (
          <label className={styles.jump}>
            Go to page
            <select
              className="input"
              onChange={(event) =>
                reader.current?.querySelector(`[data-page="${event.target.value}"]`)?.scrollIntoView({ block: "start" })
              }
            >
              {pages.map((page) => (
                <option key={page.page_no} value={page.page_no}>
                  {page.page_no}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
      {/* The text, with its own control above it. */}
      <div ref={reader} className={styles.reader}>
        {showTranslate && (
          <div className={styles.languages}>
            <span className={styles.label}>{SHOW_IN}</span>
            <Segmented<TranslateLanguage | "original">
              label={SHOW_IN}
              options={LANGUAGES.map((each) => ({ value: each.id ?? "original", label: <span lang={each.lang}>{each.name}</span> }))}
              value={language ?? "original"}
              onChange={(value) => pick(value === "original" ? null : value)}
            />
            {language && pages.length > 1 && (
              <button type="button" className="btn btn-quiet" onClick={() => void translate(language, allPages)}>
                {pages.length > MAX_TRANSLATE_PAGES ? `Translate the first ${MAX_TRANSLATE_PAGES} pages` : "Translate all pages"}
              </button>
            )}
          </div>
        )}
        {current === null ? (
          <span className={`skeleton ${styles.skeleton}`} aria-hidden="true" />
        ) : "failed" in current ? (
          <p className={styles.note}>
            The clean copy could not be loaded. Check your connection, then{" "}
            <button type="button" className="btn btn-quiet" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </p>
        ) : (
          <div className={styles.copy}>{copy}</div>
        )}
      </div>
      {current && "copy" in current && current.copy.truncated && <p className={styles.note}>{LEFT_OUT}</p>}
      {current && "copy" in current && typeof document !== "undefined" &&
        createPortal(<div className={`print-copy ${styles.copy}`}>{copy}</div>, document.body)}
    </section>
  );
}

/**
 * A table in its own frame, which alone scrolls sideways. Wider than the panel, it shows an
 * edge, says "Scroll sideways to see all columns", takes the keyboard's arrows (a named,
 * focusable region) and offers Open wide: the same table in a wide dialog.
 */
function TableFrame({ title, children }: { title: string; children: ReactNode }) {
  const frame = useRef<HTMLDivElement>(null);
  const [wide, setWide] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWide(element.scrollWidth > element.clientWidth + 1));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return (
    <div className={styles.tableWrap}>
      <div
        ref={frame}
        className={styles.tableBox}
        data-wide={wide || undefined}
        role={wide ? "region" : undefined}
        aria-label={wide ? title : undefined}
        tabIndex={wide ? 0 : undefined}
      >
        {children}
      </div>
      {wide && (
        <p className={styles.wideNote}>
          {WIDE_NOTE}
          <button type="button" className="btn btn-quiet" onClick={() => setOpen(true)}>
            Open wide
          </button>
        </p>
      )}
      {open && (
        <ActionDialog title={title} onClose={() => setOpen(false)}>
          <div className={`${styles.tableBox} ${styles.wideTable}`}>{children}</div>
        </ActionDialog>
      )}
    </div>
  );
}
