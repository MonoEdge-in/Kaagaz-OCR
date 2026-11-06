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
// Correct a line (#527, Tushar, 7 Nov 2026): a reviewer or an admin sees Edit beside a line on
// hover or focus; it opens a text box (Enter saves, Esc cancels). A corrected line says
// "Corrected by {name}, {date}" with Undo, and "Read as: ..." on hover or focus. Focus goes
// back to the line after Save, Cancel or Undo. The printed copy has neither Edit nor Undo.

import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { PencilIcon } from "lucide-react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Toast } from "@/components/ui/Toast";
import { editLine, getClearCopy, undoChange } from "@/lib/api";
import type { ClearCopy, LineCorrection, ReadLine, Region } from "@/lib/types";
import {
  CLEAN_COPY,
  COPIED,
  COPY_BLOCKED,
  LEFT_OUT,
  LINE_SAVED,
  LINE_UNDONE,
  MAX_LINE,
  NOT_SURE_TIP,
  OPENED_NOTE,
  WIDE_NOTE,
  blocks,
  copyText,
  correctedText,
  lineFailure,
  pageTitle,
  pairParts,
  parts,
  readAsText,
  replaceLine,
} from "./clearCopy";
import type { Part } from "./clearCopy";
import type { Spot } from "./panels";
import { sameRegion } from "./selection";
import styles from "./ReadClearly.module.css";

export interface ReadClearlyPanelProps {
  documentId: string;
  active: Region | null; // the spot highlighted now
  onShow: (spot: Spot) => void;
  canEdit: boolean; // a reviewer or an admin, the document not in the trash (#527)
}

type Loaded = { key: string; copy: ClearCopy } | { key: string; failed: true };
/** The one line being corrected: its key, the text typed, and a refusal to show. */
type Editing = { key: string; draft: string; busy: boolean; error: string | null };

export function ReadClearlyPanel({ documentId, active, onShow, canEdit }: ReadClearlyPanelProps) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [undoing, setUndoing] = useState<string | null>(null); // the change being undone
  const reader = useRef<HTMLDivElement>(null);
  const focusLine = useRef<string | null>(null); // the line to focus once it is drawn again
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

  // After Save, Cancel or Undo: focus back on the line (the reader's own, not the printed copy's).
  useEffect(() => {
    const line = focusLine.current;
    if (line === null) return;
    focusLine.current = null;
    reader.current?.querySelector<HTMLElement>(`[data-line="${line}"]`)?.focus();
  });

  const current = loaded && loaded.key === key ? loaded : null;
  const pages = current && "copy" in current ? current.copy.pages : [];

  const close = (line: string) => {
    focusLine.current = line;
    setEditing(null);
  };

  async function save(each: ReadLine, line: string, draft: string) {
    const text = draft.trim();
    if (text === each.text.trim()) return close(line); // nothing changed: nothing to send
    setEditing({ key: line, draft, busy: true, error: null });
    try {
      const saved = await editLine(documentId, line, text, each.text);
      setLoaded((now) => (now && "copy" in now ? { key: now.key, copy: replaceLine(now.copy, saved.line) } : now));
      setToast(LINE_SAVED);
      close(line);
    } catch (caught) {
      setEditing({ key: line, draft, busy: false, error: lineFailure(caught) });
    }
  }

  // An earlier correction may be in force again after Undo, so the copy is asked for again.
  async function undo(line: string, correction: LineCorrection) {
    setUndoing(correction.change_id);
    try {
      await undoChange(documentId, correction.change_id);
      focusLine.current = line;
      setToast(LINE_UNDONE);
      await getClearCopy(documentId).then(
        (copy) => setLoaded({ key, copy }),
        () => setLoaded({ key, failed: true }),
      );
    } catch (caught) {
      setToast(lineFailure(caught));
    } finally {
      setUndoing(null);
    }
  }

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

  // A "Label: value" line keeps its label and value in two columns. `tools`: Edit and Undo,
  // left out of the printed copy.
  const line = (each: ReadLine, n: number, shown: Part[], tools: boolean) => {
    const pressed = active !== null && sameRegion(active, each.region);
    const label = shown.filter((part) => part.label);
    const lineKey = tools && canEdit ? (each.key ?? null) : null; // only a line with a key can be corrected
    const correction = each.correction ?? null;
    const readAsId = tools && each.key && correction ? `read-as-${each.key}` : undefined;
    const open = lineKey !== null && editing?.key === lineKey ? editing : null;
    if (open) {
      const { key: at, draft, busy, error } = open;
      return (
        <span className={styles.editor}>
          <input
            className="input"
            aria-label={`Correct line ${n}`}
            aria-invalid={error !== null || undefined}
            aria-describedby={error ? "line-edit-error" : undefined}
            maxLength={MAX_LINE}
            value={draft}
            readOnly={busy}
            autoFocus
            onChange={(event) => setEditing({ ...open, draft: event.target.value })}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault(); // Enter saves; never mid-word in a Hindi or Marathi keyboard
                if (!busy && draft.trim()) void save(each, at, draft);
              } else if (event.key === "Escape" && !busy) {
                event.preventDefault();
                close(at);
              }
            }}
          />
          <span className={styles.editActions}>
            <button type="button" className="btn" disabled={busy} onClick={() => close(at)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              aria-busy={busy || undefined}
              disabled={busy || !draft.trim()}
              onClick={() => void save(each, at, draft)}
            >
              Save
            </button>
          </span>
          {error && (
            <span id="line-edit-error" className="error-text" role="alert">
              {error}
            </span>
          )}
        </span>
      );
    }
    return (
      <span className={styles.row}>
        <button
          type="button"
          className={label.length > 0 ? `${styles.line} ${styles.pair}` : styles.line}
          aria-pressed={pressed}
          aria-describedby={readAsId}
          data-line={tools ? each.key : undefined}
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
        {lineKey !== null && (
          <button
            type="button"
            className={`btn btn-quiet ${styles.edit}`}
            aria-label={`Edit line ${n}`}
            title="Edit"
            disabled={editing?.busy}
            onClick={() => setEditing({ key: lineKey, draft: each.text, busy: false, error: null })}
          >
            <PencilIcon size={16} aria-hidden="true" />
          </button>
        )}
        {correction && (
          <Corrected
            correction={correction}
            id={readAsId}
            n={n}
            busy={undoing === correction.change_id}
            onUndo={lineKey !== null ? () => void undo(lineKey, correction) : undefined}
          />
        )}
      </span>
    );
  };

  const copyOf = (tools: boolean) => pages.map((page) => {
    const marks = page.marks;
    let n = 0;
    const plain = (each: ReadLine) => line(each, ++n, parts(each, marks), tools);
    return (
      <section key={page.page_no} className={styles.page} data-page={page.page_no}>
        <h3 className={styles.pageTitle}>{pageTitle(page.page_no, pages.length)}</h3>
        {page.lines.length === 0 && <p className={styles.note}>No text was found on this page.</p>}
        {blocks(page.lines).map((block, index) => {
          if (block.type === "heading") return <h4 key={index}>{plain(block.line)}</h4>;
          if (block.type === "paragraph") return <p key={index}>{plain(block.line)}</p>;
          if (block.type === "pair") {
            return <p key={index}>{line(block.line, ++n, pairParts(block.line, marks, block.label), tools)}</p>;
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
          <div className={styles.copy}>{copyOf(true)}</div>
        )}
      </div>
      {current && "copy" in current && current.copy.truncated && <p className={styles.note}>{LEFT_OUT}</p>}
      {current && "copy" in current && typeof document !== "undefined" &&
        createPortal(<div className={`print-copy ${styles.copy}`}>{copyOf(false)}</div>, document.body)}
    </section>
  );
}

/**
 * A corrected line's small note: "Corrected by Ravi Sharma, 7 Nov 2026", Undo for a reviewer
 * (`onUndo`), and "Read as: ..." (`id`: the line's description) on hover or focus.
 */
export function Corrected({
  correction,
  id,
  n,
  busy,
  onUndo,
}: {
  correction: LineCorrection;
  id: string | undefined;
  n: number;
  busy: boolean;
  onUndo?: () => void;
}) {
  return (
    <span className={styles.corrected}>
      {correctedText(correction)}
      {onUndo && (
        <button
          type="button"
          className={`btn btn-quiet ${styles.undo}`}
          aria-label={`Undo the correction to line ${n}`}
          aria-busy={busy || undefined}
          disabled={busy}
          onClick={onUndo}
        >
          Undo
        </button>
      )}
      {id && (
        <span id={id} className={styles.readAs}>
          {readAsText(correction)}
        </span>
      )}
    </span>
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
