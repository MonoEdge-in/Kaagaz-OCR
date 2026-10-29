// UAT 3 (29 Oct 2026, #446): after the user turned a page, scrolling the PDF viewer to a newly
// chosen spot could land off screen. The engine's scrollToPageArea aims with the file's turn
// (/Rotate) only, never the user's, which the viewer applies in its own layout. The viewer now
// centres the spot's mark as drawn, which carries every turn and the zoom.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { rotateRect } from "@embedpdf/models";
import { describe, expect, it } from "vitest";
import { centredScroll } from "@/components/viewer/viewer";
import { markerRect, unturnedBox } from "@/components/words/spots";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

type Box = [number, number, number, number];
const A4 = { width: 595, height: 842 }; // points, before the file's turn
const RED: Box = [25 / 595, 100 / 3 / 842, 175 / 595, 175 / 3 / 842]; // the word, before any turn

/** A box turned clockwise with its page (fractions): as a reader stores it, or as the user turns it. */
function turned([u0, v0, u1, v1]: Box, turn: number): Box {
  if (turn === 90) return [1 - v1, u0, 1 - v0, u1];
  if (turn === 180) return [1 - u1, 1 - v1, 1 - u0, 1 - v0];
  if (turn === 270) return [v0, 1 - u1, v1, 1 - u0];
  return [u0, v0, u1, v1];
}

describe("the PDF viewer scrolls a chosen spot to the middle of its view (#446)", () => {
  it("moves the scroll by the mark's distance from the view's middle, borders and scroll taken", () => {
    const area = { scrollLeft: 30, scrollTop: 500, clientLeft: 1, clientTop: 1, clientWidth: 800, clientHeight: 400 };
    // The view's middle is at 100 + 1 + 400 = 501 across, 50 + 1 + 200 = 251 down; the mark's at 620, 310.
    expect(centredScroll({ left: 600, top: 300, width: 40, height: 20 }, { left: 100, top: 50 }, area)).toEqual({
      left: 149,
      top: 559,
    });
    // Already in the middle: stays put.
    expect(centredScroll({ left: 481, top: 241, width: 40, height: 20 }, { left: 100, top: 50 }, area)).toEqual({
      left: 30,
      top: 500,
    });
  });

  it("puts the word's middle, as the user sees it, in the view's middle, at every file turn, user turn and zoom", () => {
    const box = { left: 7, top: 60 }; // the scroll area on screen
    const area = { scrollLeft: 0, scrollTop: 0, clientLeft: 0, clientTop: 0, clientWidth: 900, clientHeight: 520 };
    for (const zoom of [0.5, 1, 2]) {
      const size = { width: A4.width * zoom, height: A4.height * zoom }; // the page drawn, before any turn
      for (const file of [0, 90, 180, 270]) {
        for (const user of [0, 90, 180, 270]) {
          // The overlay draws the stored box before the file's turn (#442); the page is then shown
          // with both turns, as the viewer's Rotate does (the engine's own rotateRect).
          const drawn = markerRect(unturnedBox(turned(RED, file), file), size.width, size.height);
          const shown = rotateRect(
            size,
            { origin: { x: drawn.left, y: drawn.top }, size: { width: drawn.width, height: drawn.height } },
            (((file + user) / 90) % 4) as 0 | 1 | 2 | 3,
          );
          const page = { left: box.left + 40, top: box.top + 1200 }; // its top-left on screen, below the view
          const mark = { left: page.left + shown.origin.x, top: page.top + shown.origin.y, ...shown.size };
          const to = centredScroll(mark, box, area);
          // After that scroll, the word (turned by the file and then the user) sits mid-view.
          const word = turned(turned(RED, file), user);
          const upright = (file + user) % 180 === 0;
          const [across, down] = upright ? [size.width, size.height] : [size.height, size.width];
          expect(page.left - to.left + ((word[0] + word[2]) / 2) * across).toBeCloseTo(box.left + 450, 6);
          expect(page.top - to.top + ((word[1] + word[3]) / 2) * down).toBeCloseTo(box.top + 260, 6);
        }
      }
    }
  });

  it("aims at the drawn mark, never the engine's scrollToPageArea, and draws the spot even with Highlights off", () => {
    const wrapper = read("components/viewer/PdfDocumentViewer.tsx");
    expect(wrapper).toContain("centredScroll(mark.getBoundingClientRect(), area.getBoundingClientRect(), area)");
    expect(wrapper).toContain('`[data-pdf-viewer-page="${spot.page}"] [data-spot]`');
    expect(wrapper).toContain('visibility: showHighlights ? undefined : "hidden"');
    expect(wrapper).not.toContain(".scrollToPageArea(");
  });
});
