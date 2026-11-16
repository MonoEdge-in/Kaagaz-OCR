// Page turns saved for everyone (#600, the lead's review decision 3): a save reloads the document's
// detail, and the review panel's crop is drawn with the turn the reloaded detail carries. The crop
// is measured (useObservedBox) as if on screen, so its stage is drawn.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "@/app/(app)/documents/[id]/ReviewPanel";
import type { WordReview } from "@/app/(app)/documents/[id]/WordReview";
import type { DocumentDetail, PageTurnSaved, ReviewStatus, WordFlag } from "@/lib/types";

vi.mock("@/components/viewer/useObservedBox", () => ({
  useObservedBox: () => ({ ref: () => undefined, box: { width: 400, height: 300 }, near: true }),
}));

const KEY = "a".repeat(64);
const flag = {
  word_key: KEY,
  kind: "value",
  page_no: 3,
  region: { kind: "box", page: 3, box: [0.1, 0.1, 0.2, 0.12] },
  as_read: "Rs 4,850",
  text: "Rs 4,850",
  band: "review",
  reason: "smudged_print",
  reason_text: "smudged print",
  score: 0.62,
  alternatives: [],
  value_id: "v1",
  state: "open",
  ensemble: null,
  change: null,
} as unknown as WordFlag;
const status = {
  pages: 3,
  words: 100,
  flagged: 1,
  by_band: { check: 0, review: 1 },
  reviewed: 0,
  resolved: 0,
  remaining: 1,
  last_change_at: null,
  status: "ready",
  summary: "",
  export: { ready: false, message: "" },
  can_review: true,
  ensemble: { allowed: false, reason: null, run: null },
} as unknown as ReviewStatus;
const review: WordReview = {
  status,
  flags: [flag],
  failed: false,
  reload: () => undefined,
  reloadWords: () => undefined,
  replace: () => undefined,
  setRun: () => undefined,
};

const crop = (page_turns: PageTurnSaved[] | undefined) =>
  renderToStaticMarkup(
    createElement(ReviewPanel, {
      detail: {
        id: "d1",
        display_name: "letter.jpg",
        detected_type: "jpeg",
        pages: [{ page_no: 3, width: 3000, height: 4000, unit: "px", has_image: true, route: "ocr", read_quality: "good" }],
        page_turns,
      } as unknown as DocumentDetail,
      review,
      current: KEY,
      onCurrent: () => undefined,
      onExit: () => undefined,
      ensembleAllowed: false,
      onEnsemble: () => undefined,
      say: () => undefined,
      headingRef: { current: null },
      onReadAgain: () => undefined,
    }),
  );

describe("the review panel's crop after a save (#600)", () => {
  it("is drawn with the turn the reloaded detail carries", () => {
    expect(crop([])).not.toContain("rotate(");
    expect(crop([{ page_no: 3, turn: 90 }])).toContain("rotate(90deg)");
    expect(crop([{ page_no: 1, turn: 90 }])).not.toContain("rotate("); // another page's turn
    expect(crop(undefined)).not.toContain("rotate("); // a detail from a server before #600
  });

  it("gets a reloaded detail: the screen reloads after a save, from the PDF viewer and the photo menu", () => {
    const view = readFileSync(join(process.cwd(), "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("const reloadAfterTurns = () => setReloads((n) => n + 1);");
    expect(view.match(/onTurnsSaved=\{reloadAfterTurns\}/g)).toHaveLength(2);
  });
});
