// Page turns saved for everyone (#600): the server's page numbers and degrees against the viewer's
// page indexes and quarter turns, what a save sends, which turns a file opens with, and a page
// (with its boxes) drawn turned.

import { describe, expect, it } from "vitest";
import { activitySentence } from "@/components/activity/words";
import { describeAction } from "@/components/admin/logic";
import { turnedBox, unturnedBox } from "@/components/words/spots";
import {
  fromServer,
  openingTurns,
  pageTurnChanges,
  turnOf,
  turnedStageTransform,
  unturnedView,
} from "@/components/viewer/viewer";

describe("saved turns", () => {
  it("are page index and quarter turns in the viewer, page number and degrees on the server", () => {
    const saved = fromServer([
      { page_no: 1, turn: 90 },
      { page_no: 4, turn: 270 },
    ]);
    expect([...saved]).toEqual([
      [0, 1],
      [3, 3],
    ]);
    expect(pageTurnChanges(new Map(), saved)).toEqual([
      { page_no: 1, turn: 90 },
      { page_no: 4, turn: 270 },
    ]);
  });

  it("a save sends only the pages that differ, and 0 for a page no longer turned", () => {
    const saved = new Map([
      [0, 1],
      [2, 2],
      [5, 3],
    ]);
    const shown = new Map([
      [0, 1], // the same: not sent
      [2, 3], // changed
      [7, 1], // new
    ]);
    expect(pageTurnChanges(saved, shown)).toEqual([
      { page_no: 3, turn: 270 },
      { page_no: 6, turn: 0 },
      { page_no: 8, turn: 90 },
    ]);
    expect(pageTurnChanges(saved, saved)).toEqual([]);
  });

  it("a file opens with its saved turns, dropping this browser's own; with none saved, with them", () => {
    const local = new Map([[1, 2]]);
    const saved = new Map([[0, 1]]);
    expect(openingTurns(saved, local)).toBe(saved);
    expect(openingTurns(new Map(), local)).toBe(local);
  });
});

describe("a page drawn turned", () => {
  const box = [0.1, 0.2, 0.3, 0.6] as const;

  it("moves a box with the page at each turn", () => {
    expect(turnedBox(box, 0)).toEqual(box);
    expect(turnedBox(box, 90)).toEqual([1 - 0.6, 0.1, 1 - 0.2, 0.3]);
    expect(turnedBox(box, 180)).toEqual([1 - 0.3, 1 - 0.6, 1 - 0.1, 1 - 0.2]);
    expect(turnedBox(box, 270)).toEqual([0.2, 1 - 0.3, 0.6, 1 - 0.1]);
  });

  it("four quarter turns, or a turn and its undoing, give the box back", () => {
    let turned: readonly number[] = box;
    for (let i = 0; i < 4; i += 1) turned = turnedBox(turned as typeof box, 90);
    turned.forEach((value, index) => expect(value).toBeCloseTo(box[index] ?? NaN));
    for (const turn of [90, 180, 270]) {
      unturnedBox(turnedBox(box, turn), turn).forEach((value, index) => expect(value).toBeCloseTo(box[index] ?? NaN));
    }
  });

  it("puts a 200 x 100 page into its turned box from the top left", () => {
    expect(turnedStageTransform(200, 100, 0)).toBeUndefined();
    expect(turnedStageTransform(200, 100, 90)).toBe("translate(100px, 0px) rotate(90deg)");
    expect(turnedStageTransform(200, 100, 180)).toBe("translate(200px, 100px) rotate(180deg)");
    expect(turnedStageTransform(200, 100, 270)).toBe("translate(0px, 200px) rotate(270deg)");
  });

  it("the transform lands each corner of the page where turnedBox says", () => {
    // A point (x, y) of the w x h page: rotate about the top left, then translate (CSS order).
    const place = (transform: string, x: number, y: number) => {
      const [, tx, ty, deg] = /translate\((\S+)px, (\S+)px\) rotate\((\d+)deg\)/.exec(transform) ?? [];
      const r = (Number(deg) * Math.PI) / 180;
      return [x * Math.cos(r) - y * Math.sin(r) + Number(tx), x * Math.sin(r) + y * Math.cos(r) + Number(ty)];
    };
    const [w, h] = [200, 100];
    for (const turn of [90, 180, 270]) {
      const transform = turnedStageTransform(w, h, turn) ?? "";
      const [sw, sh] = turn === 180 ? [w, h] : [h, w];
      const [x0, y0, x1, y1] = turnedBox(box, turn);
      const corners = [place(transform, box[0] * w, box[1] * h), place(transform, box[2] * w, box[3] * h)];
      const xs = corners.map((c) => (c[0] ?? NaN) / sw).sort((a, b) => a - b);
      const ys = corners.map((c) => (c[1] ?? NaN) / sh).sort((a, b) => a - b);
      expect(xs[0]).toBeCloseTo(x0);
      expect(xs[1]).toBeCloseTo(x1);
      expect(ys[0]).toBeCloseTo(y0);
      expect(ys[1]).toBeCloseTo(y1);
    }
  });

  it("maps a part of the turned page (a crop, or what shows zoomed in) back to the page before its turn", () => {
    // A 200 x 100 page shown turned 90: 100 wide, 200 tall. Its top 50 px as shown are the page's
    // left quarter: x 0 to 50 of 200, all of its height.
    expect(unturnedView({ x: 0, y: 0, width: 100, height: 50 }, { width: 100, height: 200 }, 90)).toEqual({
      x: 0,
      y: 0,
      width: 50,
      height: 100,
    });
    const rect = { x: 10, y: 20, width: 30, height: 40 };
    expect(unturnedView(rect, { width: 100, height: 200 }, 0)).toBe(rect);
    for (const turn of [90, 180, 270]) {
      const shown = turn === 180 ? { width: 200, height: 100 } : { width: 100, height: 200 };
      const back = unturnedView(rect, shown, turn);
      const page = { width: 200, height: 100 };
      const box = [back.x / page.width, back.y / page.height, (back.x + back.width) / page.width, (back.y + back.height) / page.height] as const;
      const [x0, y0, x1, y1] = turnedBox(box, turn);
      expect([x0 * shown.width, y0 * shown.height, (x1 - x0) * shown.width, (y1 - y0) * shown.height].map(Math.round)).toEqual([10, 20, 30, 40]);
    }
  });

  it("reads a page's turn in degrees from the turns by page index", () => {
    const turns = new Map([[2, 3]]);
    expect(turnOf(turns, 3)).toBe(270);
    expect(turnOf(turns, 2)).toBe(0);
  });
});

describe("the activity", () => {
  const entry = (details: Record<string, number>) => ({ action: "document.page_turns", actor_name: "Priya", details });

  it("says who changed the turn of how many pages for everyone, and who put them back", () => {
    expect(activitySentence(entry({ pages: 1, turned: 3 }))).toBe("Priya changed the turn of 1 page for everyone");
    expect(activitySentence(entry({ pages: 3, turned: 3 }))).toBe("Priya changed the turn of 3 pages for everyone");
    expect(activitySentence(entry({}))).toBe("Priya changed the turn of the pages for everyone");
    expect(activitySentence(entry({ pages: 2, turned: 0 }))).toBe("Priya put the pages back as scanned");
  });

  it("matches in the admin's log", () => {
    expect(describeAction("document.page_turns", { pages: 2, turned: 1 })).toBe("Changed the turn of a document's pages");
    expect(describeAction("document.page_turns", { pages: 2, turned: 0 })).toBe("Put a document's pages back as scanned");
  });
});
