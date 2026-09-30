// Counting pages before upload, so a file over the page limit is never sent,
// and a PDF's time estimate uses its real pages (PRD 2, X4). A
// PDF is opened by PDF.js in the browser; a TIFF's pages (its IFDs) are counted
// from its bytes here. Anything that cannot be counted here (a password, a
// damaged file, a BigTIFF) is sent, and the server decides. The counting and
// the words are pure functions, unit tested in node (tests/page-count.test.ts).

import { countPdfPages } from "@/components/viewer/pdfDocuments";

/** The files whose pages are counted before upload. */
export type CountedKind = "pdf" | "tiff";

export function countedKind(name: string): CountedKind | null {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "pdf";
  if (lower.endsWith(".tif") || lower.endsWith(".tiff")) return "tiff";
  return null;
}

/**
 * How many pages (IFDs) a classic TIFF has, following each IFD's next-IFD
 * offset until 0. It stops once it has counted maxPages + 1, which is enough to
 * refuse the file, so a result above maxPages means "more than maxPages". null
 * when unknown: not a classic TIFF (a BigTIFF included), an offset outside the
 * file, or an IFD chain that loops back on itself.
 */
export function countTiffPages(buffer: ArrayBuffer, maxPages: number): number | null {
  const view = new DataView(buffer);
  if (view.byteLength < 8) return null;
  const order = view.getUint16(0, false);
  if (order !== 0x4949 && order !== 0x4d4d) return null; // "II" or "MM"
  const little = order === 0x4949;
  if (view.getUint16(2, little) !== 42) return null;

  const seen = new Set<number>();
  let offset = view.getUint32(4, little);
  let pages = 0;
  while (offset !== 0 && pages <= maxPages) {
    if (offset < 8 || offset + 2 > view.byteLength || seen.has(offset)) return null;
    seen.add(offset);
    const next = offset + 2 + view.getUint16(offset, little) * 12;
    if (next + 4 > view.byteLength) return null;
    pages += 1;
    offset = view.getUint32(next, little);
  }
  return pages === 0 ? null : pages;
}

/**
 * The refusal for a file over the page limit, or null when it may be sent
 * (pages null: not known here). A TIFF is counted only to maxPages + 1, so it
 * says "more than", like the server's own message.
 */
export function pageLimitProblem(
  kind: CountedKind,
  pages: number | null,
  maxPages: number,
): string | null {
  if (pages === null || pages <= maxPages) return null;
  const fix = `The limit is ${maxPages}. Split it into parts and upload each one.`;
  return kind === "pdf"
    ? `This PDF has ${pages} pages. ${fix}`
    : `This file has more than ${maxPages} pages. ${fix}`;
}

/**
 * A PDF's or TIFF's pages (null when not known here), and why it will not be
 * sent for them (null when it may be). The upload queue keeps the pages for
 * its time estimate. Runs in the browser only.
 */
export async function checkPages(
  file: File,
  maxPages: number,
): Promise<{ pages: number | null; problem: string | null }> {
  const kind = countedKind(file.name);
  if (kind === null) return { pages: null, problem: null };
  try {
    const data = await file.arrayBuffer();
    const pages = kind === "pdf" ? await countPdfPages(data) : countTiffPages(data, maxPages);
    return { pages, problem: pageLimitProblem(kind, pages, maxPages) };
  } catch {
    // PDF.js could not open it (a password, damage), or the file could not be read here.
    return { pages: null, problem: null };
  }
}
