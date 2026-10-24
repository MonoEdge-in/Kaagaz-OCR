// Word-level review and Ensemble after the scan on the document screen (scan review UX
// rules, Tushar, 16 Oct 2026): bands, keys, confirm / edit / undo, Ensemble, the export
// dialog and the wording rules of section 6.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "@/app/(app)/documents/[id]/ReviewPanel";
import { ExportDialog, SummaryBar, documentMoreItems } from "@/app/(app)/documents/[id]/WordReview";
import type { WordReview } from "@/app/(app)/documents/[id]/WordReview";
import { FlagText } from "@/components/review/FlagText";
import * as words from "@/components/review/wordReview";
import {
  confirmText,
  decide,
  flagLabel,
  keepAll,
  needsConfirm,
  nextOpen,
  pageRanges,
  readingsDifferText,
  resultText,
  retryLabel,
  reviewKey,
  runEnsemble,
  runText,
  saveWord,
  selectRange,
  stopRun,
  undoWord,
} from "@/components/review/wordReview";
import type { KeyEventLike } from "@/components/review/wordReview";
import type { DocumentDetail, EnsembleRun, ReviewStatus, WordChange, WordFlag } from "@/lib/types";

const KEY = "a".repeat(64);

const flag = (changes: Partial<WordFlag> = {}): WordFlag => ({
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
  alternatives: ["Rs 4,350"],
  value_id: "v1",
  state: "open",
  ensemble: null,
  change: null,
  ...changes,
});

const run = (changes: Partial<EnsembleRun> = {}): EnsembleRun => ({
  id: "r1",
  scope: "flagged",
  status: "running",
  pages_total: 14,
  pages_done: 4,
  page_now: 5,
  words_total: 12,
  agreed: 0,
  changed: 0,
  unresolved: 0,
  kept: 0,
  failed_pages: [],
  started_at: "2026-10-16T10:00:00+05:30",
  finished_at: null,
  error: null,
  ...changes,
});

const status = (changes: Partial<ReviewStatus> = {}): ReviewStatus => ({
  pages: 14,
  words: 2431,
  flagged: 12,
  by_band: { check: 8, review: 4 },
  reviewed: 4,
  resolved: 0,
  remaining: 8,
  last_change_at: "2026-10-16T10:42:00+05:30",
  status: "ready",
  summary: "Scan complete · 14 pages · 2,431 words · 12 flagged for review",
  export: { ready: false, message: "8 flagged words not yet reviewed. Export anyway or review first." },
  can_review: true,
  ensemble: { allowed: true, reason: null, run: null },
  ...changes,
});

const review = (shown: ReviewStatus | null, flags: WordFlag[] = [flag()]): WordReview => ({
  status: shown,
  flags,
  failed: false,
  reload: () => undefined,
  replace: () => undefined,
  setRun: () => undefined,
});

const detail = {
  id: "d1",
  display_name: "invoice_march.pdf",
  detected_type: "pdf",
  pages: [{ page_no: 3, width: 595, height: 842, unit: "pt", has_image: false, route: "ocr", read_quality: "good" }],
} as unknown as DocumentDetail;

const bar = (shown: ReviewStatus, more: Partial<Parameters<typeof SummaryBar>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(SummaryBar, {
      documentId: "d1",
      review: review(shown),
      scanning: "Scanning page 6 of 14",
      reviewing: false,
      reviewRef: { current: null },
      onReview: () => undefined,
      onEnsemble: () => undefined,
      ensembleBusy: false,
      ensembleError: null,
      onExport: () => undefined,
      download: "/api/documents/d1/file",
      say: () => undefined,
      detailsId: "document-details",
      detailsOpen: false,
      onDetails: () => undefined,
      ...more,
    }),
  );

const panel = (shown: ReviewStatus, flags: WordFlag[], allowed = true) =>
  renderToStaticMarkup(
    createElement(ReviewPanel, {
      detail,
      review: review(shown, flags),
      current: flags[0]?.word_key ?? null,
      onCurrent: () => undefined,
      onExit: () => undefined,
      ensembleAllowed: allowed,
      onEnsemble: () => undefined,
      say: () => undefined,
      headingRef: { current: null },
    }),
  );

const css = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

/** fetch answers `reply` (or an error) and records each call. */
function answer(reply: unknown, httpStatus = 200) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(reply), { status: httpStatus, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("bands", () => {
  it("render with an underline, a glyph and words, never colour alone", () => {
    const html = renderToStaticMarkup(createElement(FlagText, { flag: flag({ band: "check" }) }));
    expect(html).toContain('data-band="check"');
    expect(html).toContain("Flagged for review: &#x27;Rs 4,850&#x27;, a second look is wise, reason: smudged print");
    const text = css("components/review/Review.module.css");
    expect(text).toMatch(/\.flagText\[data-band="check"\] \{[^}]*text-decoration-style: dotted;[^}]*var\(--warning\)/);
    expect(text).toMatch(/\.flagText\[data-band="review"\] \{[^}]*text-decoration-style: solid;[^}]*var\(--critical\)/);
    expect(text).toMatch(/\.flagText\[data-band="check"\]::after \{[^}]*content: "\?";/);
    expect(text).toMatch(/\.flagText\[data-band="review"\]::after \{[^}]*content: "!";/);
    expect(text).toMatch(/\.flagText::after \{[^}]*inline-size: 1\.25em;/); // a fixed width: nothing shifts
  });

  it("are drawn over the page as named buttons, the page itself unchanged", () => {
    expect(flagLabel(flag())).toBe("Flagged for review: 'Rs 4,850', unclear source, please confirm, reason: smudged print");
    const page = css("components/viewer/PageViewer.module.css");
    expect(page).toMatch(/\.flag \{[^}]*position: absolute;/);
    expect(page).toMatch(/\.flag\[data-band="check"\] \{[^}]*dotted/);
    expect(page).toMatch(/\.flag\[data-band="review"\] \{[^}]*solid/);
    const viewer = css("components/viewer/PageViewer.tsx");
    expect(viewer).toContain("aria-label={name}");
    expect(viewer).toContain("flagLabel(flag)");
  });
});

describe("review keys", () => {
  const press = (key: string, changes: Partial<KeyEventLike> = {}, tagName = "H2") =>
    reviewKey({
      key,
      shiftKey: false,
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      target: { tagName, isContentEditable: false, inOverlay: false },
      ...changes,
    });

  it("map N, Shift+N, Enter, E, S and Esc", () => {
    expect(press("n")).toBe("next");
    expect(press("N", { shiftKey: true })).toBe("previous");
    expect(press("Enter")).toBe("confirm");
    expect(press("e")).toBe("edit");
    expect(press("s")).toBe("ensemble");
    expect(press("Escape")).toBe("exit");
    expect(press("x")).toBeNull();
  });

  it("never act while typing, in a dialog or a menu, or with a modifier", () => {
    for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) expect(press("n", {}, tag)).toBeNull();
    expect(press("e", { target: { tagName: "DIV", isContentEditable: true, inOverlay: false } })).toBeNull();
    expect(press("Escape", { target: { tagName: "BUTTON", isContentEditable: false, inOverlay: true } })).toBeNull();
    expect(press("s", { ctrlKey: true })).toBeNull();
    expect(press("n", { metaKey: true })).toBeNull();
    expect(press("Enter", {}, "BUTTON")).toBeNull(); // a focused button keeps its own Enter
  });

  it("move to the next open word, and pick a run with Shift+click", () => {
    const flags = [flag({ word_key: "a", state: "reviewed" }), flag({ word_key: "b" }), flag({ word_key: "c" })];
    expect(nextOpen(flags, 1)).toBe(2);
    expect(nextOpen(flags, 2)).toBe(1);
    expect(nextOpen([flag({ state: "resolved" })], 0)).toBe(-1);
    expect([...selectRange(["a", "b", "c", "d"], new Set(), "a", "c", true)]).toEqual(["a", "b", "c"]);
    expect([...selectRange(["a", "b", "c"], new Set(["a", "b", "c"]), null, "b", false)]).toEqual(["a", "c"]);
  });
});

describe("confirm, edit and undo", () => {
  it("confirm sends the text the person saw", async () => {
    const calls = answer(flag({ state: "reviewed" }));
    const done = await saveWord("d1", flag(), null);
    expect(calls).toEqual([
      { url: `/api/documents/d1/flags/${KEY}/confirm`, method: "POST", body: { expected_text: "Rs 4,850" } },
    ]);
    expect(done).toMatchObject({ ok: true, message: "Confirmed 'Rs 4,850'." });
  });

  it("edit sends the new text and the text seen", async () => {
    const calls = answer(flag({ text: "Rs 4,350", state: "reviewed" }));
    const done = await saveWord("d1", flag(), "Rs 4,350");
    expect(calls[0]).toEqual({
      url: `/api/documents/d1/flags/${KEY}/edit`,
      method: "POST",
      body: { text: "Rs 4,350", expected_text: "Rs 4,850" },
    });
    expect(done).toMatchObject({ ok: true, message: "Saved 'Rs 4,350'." });
  });

  it("a word changed meanwhile says the backend's words and reloads", async () => {
    const said = "This word was changed since you opened it. Your change was not saved. Reload to see the latest text.";
    answer({ error: { code: "changed_since", message: said } }, 409);
    expect(await saveWord("d1", flag(), null)).toEqual({ ok: false, message: said, reload: true });
  });

  it("undo names the change, and a refusal keeps the API's message", async () => {
    const change = { id: "c1", before_text: "Rs 4,850" } as WordChange;
    const calls = answer(change);
    expect(await undoWord("d1", "c1")).toMatchObject({ ok: true, message: "Undone. The word reads 'Rs 4,850' again." });
    expect(calls[0]).toMatchObject({ url: "/api/documents/d1/changes/c1/undo", method: "POST" });
    answer({ error: { code: "not_latest", message: "Only the latest change can be undone. Undo the changes after it first." } }, 409);
    expect(await undoWord("d1", "c1")).toMatchObject({ ok: false, reload: false });
  });

  it("the panel shows the word, its band, reason and score, and the reviewer's actions", () => {
    const html = panel(status(), [flag()]);
    expect(html).toContain("Review · unclear source, please confirm · smudged print");
    expect(html).toContain("Score 0.62");
    expect(html).toContain("4 of 12 reviewed");
    for (const label of ["Confirm", "Edit", "Send to Ensemble", "Skip", "Previous", "Next", "Rs 4,350"]) {
      expect(html).toContain(`>${label}<`);
    }
    expect(html.indexOf(">Confirm<")).toBeGreaterThan(html.indexOf(">Skip<")); // the primary last
  });

  it("a member sees flags and reasons only", () => {
    const html = panel(status({ can_review: false }), [flag({ alternatives: null })], false);
    expect(html).toContain("smudged print");
    for (const label of [">Confirm<", ">Edit<", ">Send to Ensemble<", "type=\"checkbox\""]) expect(html).not.toContain(label);
    // QA 17 Oct: it says why, instead of going silent.
    expect(html).toContain(">Only a reviewer or an admin can confirm or edit these words.<");
    expect(panel(status(), [flag()])).not.toContain("Only a reviewer or an admin");
  });

  it("a reviewed word says who and when, with Undo; all reviewed says the document is ready", () => {
    const done = flag({ state: "reviewed", change: { id: "c1", action: "confirm", at: "2026-10-16T10:42:00+05:30", by_you: true } });
    const html = panel(status({ reviewed: 12, remaining: 0 }), [done]);
    expect(html).toContain("Reviewed by you · ");
    expect(html).toContain(">Undo<");
    expect(html).toContain("Verified. All 12 flagged words are reviewed or resolved. Document ready to export.");
    expect(html).toContain(">Verified<");
    for (const label of [">Confirm<", ">Edit<", ">Skip<", ">Send to Ensemble<"]) expect(html).not.toContain(label);
  });
});

describe("Ensemble", () => {
  it("asks first for the full document or more than 50 words, never for one word", () => {
    expect(needsConfirm("document", 3)).toBe(true);
    expect(needsConfirm("flagged", 51)).toBe(true);
    expect(needsConfirm("flagged", 50)).toBe(false);
    expect(needsConfirm("word", 1)).toBe(false);
    expect(confirmText("document", { pages: 14, words: 900, seconds: 180 })).toBe(
      "Run Ensemble on all 14 pages? About 3 min. You can keep reviewing while it runs.",
    );
  });

  it("starts, stops and keeps through the API", async () => {
    let calls = answer(run());
    expect(await runEnsemble("d1", "words", [KEY])).toMatchObject({ ok: true, message: "Ensemble started on 14 pages." });
    expect(calls[0]).toEqual({ url: "/api/documents/d1/ensemble", method: "POST", body: { scope: "words", word_keys: [KEY] } });
    calls = answer(run({ status: "stopped" }));
    expect(await stopRun("d1", "r1")).toMatchObject({ ok: true, message: "Ensemble stopped. The pages it finished are saved." });
    expect(calls[0]?.url).toBe("/api/documents/d1/ensemble/r1/stop");
    calls = answer({ kept: 9, skipped: 0 });
    expect(await keepAll("d1", "r1")).toMatchObject({ ok: true, message: "Kept 9 changes." });
    expect(calls[0]?.url).toBe("/api/documents/d1/ensemble/r1/keep-all");
    calls = answer({ id: "x1", decision: "kept", change_id: "c9" });
    expect(await decide("d1", "x1", true)).toMatchObject({ ok: true, message: "Kept Ensemble's reading." });
    expect(calls[0]?.url).toBe("/api/documents/d1/ensemble/readings/x1/keep");
    calls = answer({ id: "x1", decision: "reverted" });
    expect(await decide("d1", "x1", false)).toMatchObject({ ok: true, message: "Reverted. The word stays flagged for you." });
    expect(calls[0]?.url).toBe("/api/documents/d1/ensemble/readings/x1/revert");
  });

  it("shows its progress in the bar, politely, with Stop in place of the Ensemble button", () => {
    const html = bar(status({ ensemble: { allowed: true, reason: null, run: run() } }));
    expect(runText(run())).toBe("Ensemble · page 5 of 14");
    expect(html).toMatch(/aria-live="polite"[^>]*>Ensemble · page 5 of 14</);
    expect(html).toContain(">Stop<");
    expect(html).not.toContain(">Run Ensemble on flagged words<");
  });

  it("says what it resolved, and offers Keep all for its changes", () => {
    const ended = run({ status: "done", page_now: null, pages_done: 14, agreed: 6, changed: 3, unresolved: 3 });
    expect(resultText(ended)).toBe("Ensemble resolved 9 of 12 flagged words. 3 still need your review.");
    const html = bar(status({ ensemble: { allowed: true, reason: null, run: ended } }));
    expect(html).toContain("Ensemble resolved 9 of 12 flagged words. 3 still need your review. Review the changes.");
    expect(html).toContain(">Keep all<");
  });

  it("shows a proposed change before and after, with Keep and Revert", () => {
    const proposed = flag({
      state: "proposed",
      ensemble: { reading_id: "x1", run_id: "r1", outcome: "changed", proposal: "Rs 4,350", decision: null },
    });
    const html = panel(status(), [proposed]);
    expect(html).toContain("Rs 4,850 → Rs 4,350");
    expect(html).toContain(">Keep<");
    expect(html).toContain(">Revert<");
  });

  it("shows every reading of a word it could not settle", () => {
    const left = flag({
      alternatives: ["Rs 4,350", "Rs 4,550"],
      ensemble: { reading_id: "x1", run_id: "r1", outcome: "unresolved", proposal: null, decision: null },
    });
    const html = panel(status(), [left]);
    const said = readingsDifferText(["Rs 4,850", "Rs 4,350", "Rs 4,550"]);
    expect(said).toBe("Readings differ: 'Rs 4,850' / 'Rs 4,350' / 'Rs 4,550'. Choose one or type the correct value.");
    expect(html).toContain(said.replaceAll("'", "&#x27;"));
    for (const reading of ["Rs 4,850", "Rs 4,350", "Rs 4,550"]) expect(html).toContain(`<bdi>${reading}</bdi>`);
  });

  it("marks a word in Ensemble, still editable", () => {
    const html = panel(status(), [flag({ state: "in_ensemble" })]);
    expect(html).toContain(">in Ensemble<");
    expect(html).toContain(">Edit<");
  });

  it("says why it could not finish, with Retry for the pages left", () => {
    const message = "Ensemble couldn't finish pages 9–14. The connection timed out. Pages 1–8 are done and saved.";
    const failed = run({ status: "failed", page_now: null, failed_pages: [9, 10, 11, 12, 13, 14], error: { code: "timeout", message } });
    expect(pageRanges([9, 10, 11, 12, 13, 14])).toBe("9–14");
    expect(pageRanges([3, 5, 6, 7])).toBe("3 and 5–7");
    expect(retryLabel([9, 10, 11, 12, 13, 14])).toBe("Retry pages 9–14");
    const html = bar(status({ ensemble: { allowed: true, reason: null, run: failed } }));
    expect(html).toContain(message.replace("'", "&#x27;"));
    expect(html).toContain(">Retry pages 9–14<");
  });

  it("is shown disabled with its reason while scanning", () => {
    const wait = "Ensemble is available when scanning finishes.";
    const html = bar(
      status({ status: "received", summary: null, ensemble: { allowed: false, reason: wait, run: null } }),
    );
    expect(html).toContain("Scanning page 6 of 14");
    expect(html).toMatch(/<button[^>]*aria-disabled="true"[^>]*>Run Ensemble on flagged words</);
    expect(html).toContain(wait);
    expect(html).not.toContain(">Review flagged words<");
  });

  // Changed on purpose (document panel UX, 25 Oct 2026): the scan card is one status line with
  // Check it; the counts and Ensemble wait under More details, closed at first.
  it("is not offered when the API does not allow it after the scan", () => {
    const html = bar(status({ ensemble: { allowed: false, reason: "Only reviewers and admins can run Ensemble.", run: null } }));
    expect(html).not.toContain("Run Ensemble on flagged words");
    expect(html).toContain(">Check them<");
  });

  it("says one thing, with Check it first and the counts and Ensemble under a closed More details", () => {
    const html = bar(status());
    expect(html).toContain(">8 words need a look<");
    expect(html).toMatch(/<button[^>]*aria-expanded="false" aria-controls="document-details [^"]+"[^>]*>More details/);
    expect(html.slice(html.indexOf('hidden=""'))).toContain("Run Ensemble on flagged words");
    expect(words.trustText(status())).toBe("14 pages · 2,431 words · 4 of 12 checked · Last change 16 Oct 2026, 10:42 am");
    expect(html.indexOf(">Check them<")).toBeLessThan(html.indexOf("Run Ensemble on flagged words"));
    expect(html).not.toContain("btn-primary"); // Ask is the panel's one primary
    const none = bar(status({ flagged: 0, reviewed: 0, remaining: 0, summary: "Scan complete · 14 pages · 2,431 words · Nothing flagged" }));
    expect(none).toContain(">No words need a look<");
    expect(none).not.toContain("Run Ensemble on flagged words");
    expect(none).not.toContain(">Check");
    // Changed on purpose (Tushar, 24 Oct 2026, "put these in 3 dots"): Ensemble on the full
    // document, Export and Download original are in the document's "⋯" menu, not a menu here.
    expect(none).not.toContain("Run Ensemble on full document");
    const status_ = status({ flagged: 0, reviewed: 0, remaining: 0, summary: "Scan complete · 14 pages · 2,431 words · Nothing flagged" });
    const items = documentMoreItems(status_, { onEnsemble: () => undefined, onExport: () => undefined, download: "/f" });
    expect(items.map((item) => item.label)).toEqual(["Run Ensemble on full document", "Export", "Download original"]);
    expect(items.every((item) => item.hint.length > 0)).toBe(true);
  });

  it("says Everything is checked, with no count beside it, once every word is settled", () => {
    const html = bar(status({ reviewed: 8, resolved: 4, remaining: 0 }));
    const line = /<p[^>]*data-clear="true"[^>]*>([\s\S]*?)<\/p>/.exec(html)?.[1]?.replace(/<[^>]*>/g, "");
    expect(line).toBe("Everything is checked");
    expect(html).not.toContain(">Check");
    // Never a count of words still to check beside it: the line comes from the same count.
    for (const remaining of [0, 1, 5]) {
      const said = words.statusLine(status({ remaining }), "Scanning").text;
      expect(said === "Everything is checked").toBe(remaining === 0);
      if (said === "Everything is checked") expect(said).not.toMatch(/\d/);
    }
  });
});

describe("the export dialog", () => {
  const dialog = (shown: ReviewStatus) =>
    renderToStaticMarkup(
      createElement(ExportDialog, { status: shown, download: "/api/documents/d1/file", onReview: () => undefined, onClose: () => undefined }),
    );

  it("gives both choices equally when words are not yet reviewed", () => {
    const html = dialog(status({ export: { ready: false, message: "3 flagged words not yet reviewed. Export anyway or review first." } }));
    expect(html).toContain("3 flagged words not yet reviewed. Export anyway or review first.");
    expect(html).toMatch(/<button type="button" class="btn"[^>]*>Review first</);
    expect(html).toMatch(/<a class="btn"[^>]*>Export anyway</);
    expect(html).not.toContain("btn-primary");
    expect(html).toContain("Export downloads the original file.");
  });

  it("says ready once every flag is reviewed", () => {
    const html = dialog(status({ remaining: 0, export: { ready: true, message: "12 flagged · 12 reviewed. Ready to export." } }));
    expect(html).toContain("12 flagged · 12 reviewed. Ready to export.");
    expect(html).toMatch(/>Export</);
  });
});

describe("wording (section 6)", () => {
  const BANNED =
    /perfect|flawless|100% accurate|guaranteed|always correct|error-free|zero mistakes|\binstant|effortless|magic|revolutionary|best-in-class|AI-powered|\bfixed\b|don't need to check|fully verified|oops|sorry|unfortunately|we think|maybe|probably|not sure|might be wrong|failed to understand|garbage|bad scan|can't read|confused|struggling|uh-oh|\bhmm|try your luck|hopefully|something went wrong/i;

  it("holds no banned word and no exclamation mark in any string this feature shows", () => {
    const ended = run({ status: "done", page_now: null, agreed: 6, changed: 3, unresolved: 3 });
    const texts: string[] = [
      ...(Object.values(words) as unknown[]).filter((value): value is string => typeof value === "string"),
      ...words.HISTORY_FILTERS.map((choice) => choice.label),
      words.flagLabel(flag()),
      words.bandLine(flag({ band: "check" })),
      words.progressText(4, 12),
      words.finishedText(12),
      words.finishedText(1),
      words.runText(run()),
      words.runText(run({ page_now: null })),
      words.resultText(ended),
      words.resultText(run({ status: "stopped", agreed: 1, words_total: 1 })),
      words.confirmText("flagged", { pages: 3, words: 64, seconds: 90 }),
      words.trustText(status()),
      words.stateText(flag()),
      words.failureText(new Error("x")),
      bar(status()),
      bar(status({ status: "received", summary: null, ensemble: { allowed: false, reason: "Ensemble is available when scanning finishes.", run: null } })),
      panel(status(), [flag()]),
      panel(status({ reviewed: 12, remaining: 0 }), [flag({ state: "reviewed", change: { id: "c", action: "edit", at: null, by_you: true } })]),
    ];
    for (const text of texts) {
      expect(text, text).not.toMatch(BANNED);
      expect(text.replace(/<[^>]*>/g, ""), text).not.toContain("!");
    }
  });

  it("uses the owner's Ensemble explanation word for word", () => {
    expect(words.ENSEMBLE_INFO).toBe(
      "Ensemble reads the same text with several recognition models and compares their readings. When they agree, the word is resolved. When they don't, it stays flagged for you, with each reading shown.",
    );
  });
});

// KZ-007 (QA 17 Oct): a rejected file showed "The flagged words could not be loaded"
// with a Try again that did nothing. It has no words, so nothing is asked or shown.
describe("a rejected file", () => {
  it("asks for no flagged words and shows no summary bar", () => {
    const view = css("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toMatch(/useWordReview\(documentId, reloads, loadedStatus !== null && loadedStatus !== "rejected"\)/);
    expect(view).toMatch(/\{detail\.status !== "rejected" && \(\s*<SummaryBar/);
    const hook = css("app/(app)/documents/[id]/WordReview.tsx");
    expect(hook).toMatch(/useEffect\(\(\) => \{\s*if \(!enabled\) return;\s*let cancelled = false;\s*load\(\)/);
  });
});
