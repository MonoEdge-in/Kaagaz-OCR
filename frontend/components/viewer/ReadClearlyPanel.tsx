"use client";

// Read clearly (Tushar, 19 Oct 2026): a clean, typed copy of the whole document, every page
// in order, beside the page image (under it on a phone). One request (GET clear-copy); the
// text is exactly as read, plain React text only (visibleText), grouped by clearCopy.ts.
// Words not read for sure have a dotted underline and a tooltip. Pressing a line highlights
// its box on the page. Copy all and Print (the copy alone, through a portal and globals.css).
// Document panel UX (25 Oct 2026): Copy all (it says "Copied") and Print in one quiet row; then
// the text. Vrushit UAT (25 Oct 2026): no translation for now, so no language switch.
// One heading a page ("Page 1"), a page jump when there are several. The line opened is lit
// like the spot on the page, with "The line you opened". A label line keeps its label and
// value in two columns. A table keeps whole words, scrolls sideways in its own frame (an edge
// and a note say so) and opens wide in a dialog.

import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Toast } from "@/components/ui/Toast";
import { getClearCopy } from "@/lib/api";
import type { ClearCopy, ReadLine, Region } from "@/lib/types";
import {
  CLEAN_COPY,
  COPIED,
  COPY_BLOCKED,
  LEFT_OUT,
  NOT_SURE_TIP,
  OPENED_NOTE,
  WIDE_NOTE,
  blocks,
  copyText,
  pageTitle,
  pairParts,
  parts,
} from "./clearCopy";
import type { Part } from "./clearCopy";
import type { Spot } from "./panels";
import { sameRegion } from "./selection";
import styles from "./ReadClearly.module.css";

export interface ReadClearlyPanelProps {
  documentId: string;
  active: Region | null; // the spot highlighted now
  onShow: (spot: Spot) => void;
}

type Loaded = { key: string; copy: ClearCopy } | { key: string; failed: true };

export function ReadClearlyPanel({ documentId, active, onShow }: ReadClearlyPanelProps) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const reader = useRef<HTMLDivElement>(null);
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
      </section>
    );
  });

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
      <div ref={reader} className={styles.reader}>
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
