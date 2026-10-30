// UAT 3 (29 Oct 2026, #446): after the user turned a page, scrolling the PDF viewer to a newly
// chosen spot could land off screen. The engine's scrollToPageArea aims with the file's turn
// (/Rotate) only, never the user's, which the viewer applies in its own layout. The viewer now
// centres the spot's mark as drawn, which carries every turn and the zoom.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { centredScroll } from "@/components/viewer/viewer";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

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

  it("aims at the drawn mark, never the engine's scrollToPageArea, and draws the spot even with Highlights off", () => {
    const wrapper = read("components/viewer/PdfDocumentViewer.tsx");
    expect(wrapper).toContain("centredScroll(mark.getBoundingClientRect(), area.getBoundingClientRect(), area)");
    expect(wrapper).toContain('`[data-pdf-viewer-page="${spot.page}"] [data-spot]`');
    expect(wrapper).toContain('visibility: showHighlights ? undefined : "hidden"');
    expect(wrapper).not.toContain(".scrollToPageArea(");
  });
});
