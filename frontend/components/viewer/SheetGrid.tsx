"use client";

// A spreadsheet page (one sheet) as a grid of cells from getSheetCells, with the
// highlighted cells marked in colour and in words, and the first one scrolled
// into view inside the grid (the page itself does not jump). My words in a cell
// get a dashed outline and their number (ViewerWords.tsx).

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, getSheetCells } from "@/lib/api";
import { cellKey, cellName, columnName, highlightedCells } from "@/lib/regions";
import type { CellRegion } from "@/lib/regions";
import type { PageSummary, Region, SheetCells } from "@/lib/types";
import { markerId } from "@/components/words/ViewerWords";
import { markedCells } from "@/components/words/spots";
import type { Marker } from "@/components/words/spots";
import { buildGrid, cropRowWindow, sheetRowWindow, SHEET_ROWS_PER_VIEW } from "./viewer";
import styles from "./PageViewer.module.css";

export interface SheetGridProps {
  documentId: string;
  page: PageSummary;
  highlights: Region[];
  crop?: CellRegion;
  marks?: Marker[];
  activeMark?: string | null;
}

interface Loaded {
  key: string;
  sheet: SheetCells | null;
  error: string | null;
}

export function SheetGrid({ documentId, page, highlights, crop, marks = [], activeMark = null }: SheetGridProps) {
  const marked = highlightedCells(highlights, page.page_no);
  const mine = markedCells(marks);
  const focus = crop ?? highlights.find(
    (r): r is CellRegion => r.kind === "cell" && r.page === page.page_no,
  );
  const initial = crop ? cropRowWindow(crop.row) : sheetRowWindow(focus ? focus.row : null);
  const [rowFrom, setRowFrom] = useState<{ base: string; value: number } | null>(null);
  const base = `${documentId}:${page.page_no}:${initial.rowFrom}`;
  const from = rowFrom && rowFrom.base === base ? rowFrom.value : initial.rowFrom;
  const to = crop ? initial.rowTo : from + SHEET_ROWS_PER_VIEW - 1;
  const [tries, setTries] = useState(0); // Try again asks once more
  const key = `${base}:${from}:${to}:${tries}`;

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    let cancelled = false;
    getSheetCells(documentId, page.page_no, from, to)
      .then((sheet) => {
        if (!cancelled) setLoaded({ key, sheet, error: null });
      })
      .catch((error: unknown) => {
        const message = error instanceof ApiError ? error.message : "Could not load this sheet.";
        if (!cancelled) setLoaded({ key, sheet: null, error: message });
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, page.page_no, from, to, key]);

  const frameRef = useRef<HTMLDivElement>(null);
  const current = loaded && loaded.key === key ? loaded : null;
  const focusKey = focus ? cellKey(focus.row, focus.column) : null;

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!current?.sheet || !frame || !focusKey) return;
    const cell = frame.querySelector<HTMLElement>(`[data-cell="${focusKey}"]`);
    if (!cell) return;
    const f = frame.getBoundingClientRect();
    const c = cell.getBoundingClientRect();
    frame.scrollTop += c.top - f.top - f.height / 2 + c.height / 2;
    frame.scrollLeft += c.left - f.left - f.width / 2 + c.width / 2;
  }, [current, focusKey]);

  if (!current) {
    return crop ? (
      <span className={`skeleton ${styles.sheetSkeleton}`} role="status" aria-label="Loading the sheet" />
    ) : (
      <p className="muted" aria-live="polite">
        Loading the sheet…
      </p>
    );
  }
  if (current.error || !current.sheet) {
    return (
      <div role="alert">
        <p className="error-text">{current.error ?? "Could not load this sheet."}</p>
        <button type="button" className="btn" onClick={() => setTries((n) => n + 1)}>
          Try again
        </button>
      </div>
    );
  }

  const sheet = current.sheet;
  const columnWindow = crop ? { from: crop.column - 3, to: crop.column + 3 } : null;
  const grid = buildGrid(sheet, columnWindow);
  const hasEarlier = !crop && from > 1;
  const hasLater = !crop && to < sheet.total_rows;
  const move = (value: number) => setRowFrom({ base, value: Math.max(1, value) });

  return (
    <div className={styles.sheetStack}>
      {focus ? (
        <p className={`muted ${styles.note}`}>
          Highlighted cell: {cellName(focus)}
          {marked.size > 1 ? ` and ${marked.size - 1} more` : ""}
        </p>
      ) : null}
      <div
        ref={frameRef}
        className={crop ? `${styles.sheetFrame} ${styles.sheetCrop}` : styles.sheetFrame}
        tabIndex={0}
        role="region"
        aria-label={`Sheet ${page.page_no}, rows ${from} to ${Math.min(to, sheet.total_rows)}`}
      >
        {grid.rows.length === 0 ? (
          <p className="muted">No cells in these rows.</p>
        ) : (
          <table className={`table ${styles.sheet}`}>
            <thead>
              <tr>
                <th scope="col">
                  <span className="visually-hidden">Row</span>
                </th>
                {grid.columns.map((column) => (
                  <th key={column} scope="col">
                    {columnName(column)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((row) => (
                <tr key={row.row}>
                  <th scope="row">{row.row}</th>
                  {row.cells.map((cell) => {
                    const k = cellKey(row.row, cell.column);
                    const hit = marked.has(k);
                    const here = mine.get(k);
                    const numbers = here?.map((mark) => mark.n);
                    const active = here?.some((mark) => mark.id === activeMark);
                    const classes = [hit && styles.cellHit, here && styles.cellMine, active && styles.cellMineActive];
                    return (
                      <td key={k} data-cell={k} id={here ? markerId(here[0]?.id ?? "") : undefined}
                        className={classes.filter(Boolean).join(" ") || undefined}>
                        {cell.text}
                        {hit ? <span className="visually-hidden"> (highlighted)</span> : null}
                        {numbers ? (
                          <span className={styles.cellMineNumber}>
                            <span className="visually-hidden"> (my word </span>
                            {numbers.join(", ")}
                            <span className="visually-hidden">)</span>
                          </span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {hasEarlier || hasLater ? (
        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!hasEarlier}
            onClick={() => move(from - SHEET_ROWS_PER_VIEW)}
          >
            Earlier rows
          </button>
          <button
            type="button"
            className="btn"
            disabled={!hasLater}
            onClick={() => move(from + SHEET_ROWS_PER_VIEW)}
          >
            Later rows
          </button>
          <span className={`muted tabular ${styles.note}`}>
            Rows {from} to {Math.min(to, sheet.total_rows)} of {sheet.total_rows}
          </span>
        </div>
      ) : null}
    </div>
  );
}
