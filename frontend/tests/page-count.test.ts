import { describe, expect, it } from "vitest";
import { countTiffPages, countedKind, pageLimitProblem } from "@/components/documents/pageCount";

// A classic TIFF made by hand: the 8-byte header, then one IFD per page, each
// with `entries` 12-byte entries (left as zeros) and its next-IFD offset.
// `links` sets each IFD's next offset; by default each points at the next IFD
// and the last one at 0.
function tiff(
  pages: number,
  { little = true, entries = 1, links }: { little?: boolean; entries?: number; links?: number[] } = {},
): ArrayBuffer {
  const ifdSize = 2 + entries * 12 + 4;
  const buffer = new ArrayBuffer(8 + pages * ifdSize);
  const view = new DataView(buffer);
  view.setUint16(0, little ? 0x4949 : 0x4d4d, false);
  view.setUint16(2, 42, little);
  view.setUint32(4, pages === 0 ? 0 : 8, little);
  for (let page = 0; page < pages; page += 1) {
    const at = 8 + page * ifdSize;
    view.setUint16(at, entries, little);
    const next = links?.[page] ?? (page === pages - 1 ? 0 : at + ifdSize);
    view.setUint32(at + 2 + entries * 12, next, little);
  }
  return buffer;
}

describe("countTiffPages", () => {
  it("counts one page", () => {
    expect(countTiffPages(tiff(1), 100)).toBe(1);
  });

  it("follows the next-IFD offsets through three pages", () => {
    expect(countTiffPages(tiff(3), 100)).toBe(3);
    expect(countTiffPages(tiff(3, { entries: 0 }), 100)).toBe(3);
  });

  it("reads big-endian (MM) files too", () => {
    expect(countTiffPages(tiff(3, { little: false }), 100)).toBe(3);
  });

  it("stops at one past the limit, enough to refuse the file", () => {
    expect(countTiffPages(tiff(5), 2)).toBe(3);
    expect(countTiffPages(tiff(3), 3)).toBe(3);
  });

  it("gives up (null) on an IFD chain that loops", () => {
    // 3 IFDs of 18 bytes from offset 8; the last points back at the first.
    expect(countTiffPages(tiff(3, { links: [26, 44, 8] }), 100)).toBeNull();
    // An IFD that points at itself.
    expect(countTiffPages(tiff(1, { links: [8] }), 100)).toBeNull();
  });

  it("gives up (null) on a truncated file or an offset outside it", () => {
    const whole = tiff(2);
    expect(countTiffPages(whole.slice(0, whole.byteLength - 3), 100)).toBeNull();
    expect(countTiffPages(whole.slice(0, 6), 100)).toBeNull();
    expect(countTiffPages(tiff(2, { links: [5000, 0] }), 100)).toBeNull();
    expect(countTiffPages(tiff(1, { links: [4] }), 100)).toBeNull();
  });

  it("gives up (null) on anything that is not a classic TIFF", () => {
    const big = tiff(1);
    new DataView(big).setUint16(2, 43, true); // BigTIFF
    expect(countTiffPages(big, 100)).toBeNull();
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0]).buffer;
    expect(countTiffPages(png, 100)).toBeNull();
    expect(countTiffPages(new ArrayBuffer(0), 100)).toBeNull();
    // A header with no first IFD has no pages to count.
    expect(countTiffPages(tiff(0), 100)).toBeNull();
  });
});

describe("pageLimitProblem", () => {
  it("lets a file at or under the limit through, and one it could not count", () => {
    expect(pageLimitProblem("pdf", 100, 100)).toBeNull();
    expect(pageLimitProblem("pdf", 1, 100)).toBeNull();
    expect(pageLimitProblem("tiff", 100, 100)).toBeNull();
    expect(pageLimitProblem("pdf", null, 100)).toBeNull();
    expect(pageLimitProblem("tiff", null, 100)).toBeNull();
  });

  it("names a PDF's page count, the limit and the fix", () => {
    expect(pageLimitProblem("pdf", 240, 100)).toBe(
      "This PDF has 240 pages. The limit is 100. Split it into parts and upload each one.",
    );
  });

  it("says more than the limit for a TIFF, which is counted only to one past it", () => {
    expect(pageLimitProblem("tiff", 101, 100)).toBe(
      "This file has more than 100 pages. The limit is 100. Split it into parts and upload each one.",
    );
  });
});

describe("countedKind", () => {
  it("counts PDFs and TIFFs by name, in any letter case, and nothing else", () => {
    expect(countedKind("Statement.PDF")).toBe("pdf");
    expect(countedKind("scan.tif")).toBe("tiff");
    expect(countedKind("SCAN.TIFF")).toBe("tiff");
    expect(countedKind("photo.jpg")).toBeNull();
    expect(countedKind("ledger.xlsx")).toBeNull();
    expect(countedKind("pdf")).toBeNull();
  });
});
