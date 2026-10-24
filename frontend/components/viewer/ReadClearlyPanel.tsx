"use client";

// Read clearly (Tushar, 19 Oct 2026): a clean, typed copy of the whole document, every page
// in order, beside the page image (under it on a phone). One request (GET clear-copy); the
// text is exactly as read, plain React text only (visibleText), grouped by clearCopy.ts.
// Words not read for sure have a dotted underline and a tooltip. Pressing a line highlights
// its box on the page. Copy all and Print (the copy alone, through a portal and globals.css).
// Translate follows Ask: with `canTranslate` off (Ask switched off or at None) it is not
// shown; a translation sits under its page with its own label, never mixed into the text.

import { Fragment, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, getClearCopy, translatePage } from "@/lib/api";
import type { ClearCopy, PageTranslation, ReadLine, Region, TranslateLanguage } from "@/lib/types";
import {
  LANGUAGES,
  LEFT_OUT,
  MAX_TRANSLATE_PAGES,
  NOT_SURE_TIP,
  TRANSLATE_AT_ONCE,
  TRANSLATING,
  TRANSLATION_LABEL,
  blocks,
  copyText,
  pageOf,
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
  const [said, setSaid] = useState("");
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
    setLanguage(lang);
    const first = pageNo ?? pages[0]?.page_no;
    if (lang && first !== undefined) void translate(lang, [first]);
  };

  const line = (each: ReadLine, n: number, shown: Part[]) => (
    <button
      type="button"
      className={styles.line}
      aria-pressed={active !== null && sameRegion(active, each.region)}
      onClick={() => onShow({ name: `line ${n}`, region: each.region, label: null })}
    >
      <bdi dir="auto">
        {shown.map((part, index) => {
          const text = part.label ? <strong>{part.text}</strong> : part.text;
          return part.unsure ? (
            <span key={index} className={styles.unsure} title={NOT_SURE_TIP}>
              {text}
              <span className="visually-hidden"> ({NOT_SURE_TIP})</span>
            </span>
          ) : (
            <Fragment key={index}>{text}</Fragment>
          );
        })}
      </bdi>
    </button>
  );

  const copy = pages.map((page) => {
    const marks = page.marks;
    let n = 0;
    const plain = (each: ReadLine) => line(each, ++n, parts(each, marks));
    const tr = language ? done[doneKey(language, page.page_no)] : undefined;
    const meta = LANGUAGES.find((each) => each.id === language);
    return (
      <section key={page.page_no} className={styles.page} aria-label={pageOf(page.page_no, pages.length)}>
        <p className={styles.pageBreak}>{pageOf(page.page_no, pages.length)}</p>
        {page.lines.length === 0 && <p className={styles.note}>No text was found on this page.</p>}
        {blocks(page.lines).map((block, index) => {
          if (block.type === "heading") return <h3 key={index}>{plain(block.line)}</h3>;
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
            <div key={index} className={styles.tableBox}>
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
            </div>
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
        Read clearly
      </h2>
      <p className={styles.note}>The text exactly as read, page by page. Press a line to see it on the page.</p>
      <div className={styles.tools}>
        <button
          type="button"
          className="btn"
          disabled={pages.length === 0}
          onClick={() =>
            void navigator.clipboard.writeText(copyText(pages)).then(
              () => setSaid("Copied."),
              () => setSaid("Copying is blocked here. Select the text instead."),
            )
          }
        >
          Copy all
        </button>
        <button type="button" className="btn" disabled={pages.length === 0} onClick={() => window.print()}>
          Print
        </button>
        <span role="status" className={styles.note}>
          {said}
        </span>
      </div>
      {showTranslate && (
        <div className={styles.tools}>
          <fieldset className={styles.languages}>
            <legend>Show in</legend>
            {LANGUAGES.map((each) => (
              <label key={each.name}>
                <input type="radio" name="read-clearly-language" checked={language === each.id} onChange={() => pick(each.id)} />
                <span lang={each.lang}>{each.name}</span>
              </label>
            ))}
          </fieldset>
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
          The clean copy could not be loaded.{" "}
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </p>
      ) : (
        <div className={styles.copy}>{copy}</div>
      )}
      {current && "copy" in current && current.copy.truncated && <p className={styles.note}>{LEFT_OUT}</p>}
      {current && "copy" in current && typeof document !== "undefined" &&
        createPortal(<div className={`print-copy ${styles.copy}`}>{copy}</div>, document.body)}
    </section>
  );
}
