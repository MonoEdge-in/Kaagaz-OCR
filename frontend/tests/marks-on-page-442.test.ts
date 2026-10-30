// UAT 3 (28 Oct 2026, #442): marks on a scanned PDF page were not on their word. The PDF viewer
// drew them at its zoom twice over, and in the page as shown although it draws them before the
// file's own turn (/Rotate). Here: a made-up A4 receipt, its scan 500 pt wide at the top-left
// with white below and to the right, the scan's word "red" at 25..175 x 33.3..58.3 pt.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { markerRect, unturnedBox } from "@/components/words/spots";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

type Box = [number, number, number, number];
const A4 = { width: 595, height: 842 }; // points, before the file's turn
const RED: Box = [25 / 595, 100 / 3 / 842, 175 / 595, 175 / 3 / 842]; // fractions, before the turn

/** The box as a reader stores it on the page as shown, the file's turn applied (clockwise). */
function shown([u0, v0, u1, v1]: Box, turn: number): Box {
  if (turn === 90) return [1 - v1, u0, 1 - v0, u1];
  if (turn === 180) return [1 - u1, 1 - v1, 1 - u0, 1 - v0];
  if (turn === 270) return [v0, 1 - u1, v1, 1 - u0];
  return [u0, v0, u1, v1];
}

describe("a mark on a scanned PDF page (#442)", () => {
  it("lands on its word in the offset scan at every zoom and file turn", () => {
    for (const zoom of [0.5, 1, 2]) {
      for (const turn of [0, 90, 180, 270]) {
        expect(markerRect(unturnedBox(shown(RED, turn), turn), A4.width * zoom, A4.height * zoom)).toEqual({
          left: expect.closeTo(25 * zoom, 6),
          top: expect.closeTo((100 / 3) * zoom, 6),
          width: expect.closeTo(150 * zoom, 6),
          height: expect.closeTo(25 * zoom, 6),
        });
      }
    }
  });

  it("takes the boxes the backend stored for the made-up PDF to the same word", () => {
    // backend: render_pdf_page + ocr_spans, and pdfplumber, on pages with /Rotate 90 and with an
    // offset CropBox and /Rotate 270 (scratch check for #442); fractions of the page as shown.
    const stored: [Box, number][] = [
      [[0.043478, 0.041026, 0.294686, 0.070085], 0],
      [[0.929915, 0.043478, 0.958974, 0.294686], 90],
      [[0.041026, 0.705314, 0.070085, 0.956522], 270],
    ];
    for (const [box, turn] of stored) {
      const back = unturnedBox(box, turn);
      back.forEach((value, i) => expect(Math.abs(value - stored[0]![0][i]!)).toBeLessThan(1e-5));
    }
  });

  it("is drawn by the PDF viewer at its own zoomed size and file turn, never zoomed again", () => {
    const wrapper = read("components/viewer/PdfDocumentViewer.tsx");
    const viewer = read("components/extend/pdf-viewer.tsx");
    expect(viewer).toContain("pageWidth: page.width"); // the scroller's size, already zoomed
    expect(viewer).toContain("fileRotation: rotationToDegrees(basePageRotation)");
    expect(wrapper).toContain("markerRect(unturnedBox(box, fileRotation), pageWidth, pageHeight)");
    // Changed on purpose (#446): scrolling to the spot centres its mark as drawn
    // (tests/spot-scroll-446.test.ts), so it no longer turns the spot back itself.
    expect(wrapper).not.toMatch(/pageWidth \* scale|pageHeight \* scale/);
  });
});
