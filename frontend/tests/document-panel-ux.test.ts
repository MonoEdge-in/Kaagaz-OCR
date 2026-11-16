// The document panel UX (Tushar, 25 Oct 2026): what is this (the AI summary at the top, once,
// cut after about five lines, Devanagari marked; only the card's own facts), can I trust it
// (one status line), what can I do (one primary). No all-caps buttons, at most four tabs,
// Needs attention only above 0, read-only details, no sideways scroll at 320px, and motion
// only where reduced motion lets it run.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LONG_SUMMARY, MAX_FACTS, SUMMARY_TEXT, SummaryCard } from "@/app/(app)/documents/[id]/SummaryCard";
import { statusLine } from "@/components/review/wordReview";
import { tabsThatFit } from "@/components/viewer/viewer";
import type { DocumentCard, DocumentDetail, ExtractedValue, Region, ReviewStatus } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

const at = (y: number): Region => ({ kind: "box", page: 1, box: [0.1, y, 0.5, y + 0.02] });
const card = (over: Partial<DocumentCard> = {}): DocumentCard => ({
  version: 1,
  run_id: null,
  model: "x",
  prompt: "p",
  doc_type: "land_record",
  languages: ["mr"],
  title: null,
  summary: "A ७/१२ उतारा for survey number 72 in village Daregaon.",
  document_date: null,
  parties: [{ role: "land_owner_name", field: "land_owner_name", name: "कृष्णा पाटील", name_latin: null, page: 1, region: at(0.3) }],
  identifiers: [{ kind: "survey_number", field: "land_survey_number", value: "72", page: 1, region: at(0.2) }],
  key_facts: [],
  dropped_quotes: 0,
  ...over,
});
const verified = { id: "v1", field: "land_survey_number", value: "72", raw: "72", label: "verified", region: at(0.2) } as ExtractedValue;
const detail = (over: Partial<DocumentDetail> = {}) =>
  ({ id: "d1", status: "ready", understanding: false, card: card(), values: [verified], pages: [], ...over }) as unknown as DocumentDetail;
// Changed on purpose (owner, 29 Oct 2026): the card takes `reviewer`; its count line is in value-marks.test.ts.
// Changed on purpose (owner, 29 Oct 2026, #453): and onResolved and onStale, for a value's menu (fact-actions.test.ts).
const summary = (shown: DocumentDetail) =>
  renderToStaticMarkup(
    createElement(SummaryCard, { detail: shown, active: null, onShow: () => undefined, reviewer: false, onResolved: () => undefined, onStale: () => undefined, userId: "u-1" }), // Changed on purpose (issue 468): drafts are per person
  );

describe("the summary at the top of the panel", () => {
  it("sits under the name and above Ask, the one primary, and shows once on the page", () => {
    const view = read(VIEW);
    const head = view.slice(view.indexOf("<header className={styles.header}>"), view.indexOf("</header>"));
    // Changed on purpose (PRD 7, PR 13): the heading takes focus after a lock or unlock.
    const title = head.indexOf("<h1 ref={headingRef} tabIndex={-1} className={styles.title}>");
    expect(title).toBeGreaterThanOrEqual(0);
    expect(head.indexOf("<SummaryCard")).toBeGreaterThan(title);
    expect(head.indexOf("<SummaryCard")).toBeLessThan(head.indexOf("btn btn-primary ${styles.ask}"));
    // Not under Details any more.
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx")).not.toContain("card.summary");
    expect(summary(detail()).match(/Daregaon/g)).toHaveLength(1);
  });

  it("says it is written by AI, and marks Devanagari runs lang=mr", () => {
    const html = summary(detail());
    expect(html).toContain(`>${SUMMARY_TEXT.heading}</h2>`);
    expect(html).toContain(SUMMARY_TEXT.caution);
    expect(html).toContain('<span lang="mr">७/१२ उतारा</span>');
    expect(html).toContain('<span lang="mr">कृष्णा पाटील</span>');
  });

  it("is cut after about five lines, with a real button that opens it", () => {
    const long = summary(detail({ card: card({ summary: "Survey 72, Daregaon. ".repeat(20) }) }));
    expect("Survey 72, Daregaon. ".repeat(20).length).toBeGreaterThan(LONG_SUMMARY);
    const id = /<p id="([^"]+)"[^>]*data-clamped="true"/.exec(long)?.[1];
    expect(long).toMatch(new RegExp(`<button type="button"[^>]*aria-expanded="false" aria-controls="${id}"[^>]*>${SUMMARY_TEXT.more}`));
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain("onClick={() => setOpen(!open)}");
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain('data-clamped={long && !open ? "true" : undefined}');
    expect(css(VIEW_CSS)).toMatch(/\.summaryText\[data-clamped\] \{[^}]*-webkit-line-clamp: 5;[^}]*overflow: hidden;/);
    // A short one is shown whole, with no button.
    const short = summary(detail());
    expect(short).not.toContain("data-clamped");
    expect(short).not.toContain(SUMMARY_TEXT.more);
  });

  it("lists only the facts the card has, each with its label, at most five", () => {
    const html = summary(detail());
    // Changed on purpose (review panel, 29 Oct 2026, #453): a name wraps, so it needs no tooltip.
    expect(html.match(/<dt>/g)).toHaveLength(2);
    expect(html).toContain(">Land owner name</dt>");
    expect(html).toContain(">Survey number</dt>");
    expect(html).toContain("Verified"); // the survey number's value is verified
    expect(html).toContain("Ready to verify"); // the owner has no checked value
    expect(html).not.toMatch(/Document date|Village/);
    const many = card({ identifiers: Array.from({ length: 9 }, (_, i) => ({ kind: `id_${i}`, field: `id_${i}`, value: `${i}`, page: 1, region: at(i / 20) })) });
    expect(summary(detail({ card: many })).match(/<dt>/g)).toHaveLength(MAX_FACTS);
  });

  it("never shows an empty box: a quiet line while it is read, nothing when none will come", () => {
    expect(summary(detail({ card: null, status: "received" }))).toMatch(new RegExp(`^<p class="[^"]+">${SUMMARY_TEXT.waiting}</p>$`));
    expect(summary(detail({ card: null, understanding: true }))).toContain(SUMMARY_TEXT.waiting);
    expect(summary(detail({ card: null }))).toBe("");
    expect(summary(detail({ card: card({ summary: null, parties: [], identifiers: [] }) }))).toBe("");
  });
});

describe("the panel's words and controls", () => {
  it("has no all-caps button labels: every button in the panel is sentence case", () => {
    const rule = /\.panels :global\(\.btn\) \{([^}]*)\}/.exec(css(VIEW_CSS))?.[1] ?? "";
    expect(rule).toMatch(/text-transform:\s*none;/);
    expect(rule).toMatch(/letter-spacing:\s*normal;/);
    expect(rule).toMatch(/min-height:\s*var\(--h-lg\);/);
    expect(css(VIEW_CSS)).not.toMatch(/text-transform:\s*uppercase/);
    const view = read(VIEW);
    for (const words of ["Ask about this document", "Download", "{CLEAN_COPY}"]) expect(view).toContain(words);
    expect(view).not.toMatch(/>\s*[A-Z]{2,}(?: [A-Z]{2,})+\s*</);
  });

  it("names the clean copy one way everywhere", () => {
    expect(read("components/viewer/clearCopy.ts")).toContain('export const CLEAN_COPY = "Clean copy";');
    for (const file of [VIEW, "components/viewer/ReadClearlyPanel.tsx"]) {
      expect(read(file)).toContain("CLEAN_COPY");
      expect(read(file)).not.toMatch(/>\s*Read clearly\s*<|"Read clearly"/);
    }
  });

  it("shows at most four tabs, fewer in a narrow panel, the open one always, the rest under More", () => {
    expect(tabsThatFit(null, false)).toBe(4); // before it is measured
    for (const rem of [10, 18, 20, 24, 40, 90]) {
      for (const long of [false, true]) expect(tabsThatFit(rem, long)).toBeLessThanOrEqual(4);
    }
    // 320px: the row is 18rem; with Needs attention only two fit, beside More's icon.
    // Changed on purpose (24 Oct 2026): padded tabs need about 1rem more each (#301).
    expect([tabsThatFit(21, false), tabsThatFit(21, true), tabsThatFit(28, false), tabsThatFit(28, true)]).toEqual([3, 2, 4, 3]);
    expect([tabsThatFit(25, true), tabsThatFit(32, true), tabsThatFit(20, false)]).toEqual([3, 4, 2]);
    const view = read(VIEW);
    expect(view).toContain("const shown = at >= room ? [...tabs.slice(0, room - 1), ...tabs.slice(at, at + 1)] : tabs.slice(0, room);");
    expect(view).toContain("{shown.map((item, index) => (");
    expect(view).toMatch(/<RowMenu\s+name="the other tabs"\s+compact=\{room < 3\}/);
  });

  it("shows Needs attention only when it holds something (or while reviewing)", () => {
    const view = read(VIEW);
    const tabs = view.slice(view.indexOf("const tabs: TabItem[] = ["), view.indexOf("];", view.indexOf("const tabs: TabItem[] = [")));
    expect(tabs).toContain('...(attention > 0 || reviewing ? [{ id: "attention" as const, label: "Needs attention", count: attention || undefined }] : []),');
  });

  it("never puts a count of words to check beside Everything is checked", () => {
    const base = { status: "ready", flagged: 3, reviewed: 0, resolved: 0, pages: 1, words: 395 } as ReviewStatus;
    expect(statusLine({ ...base, remaining: 1 }, "").text).toBe("1 word needs a look");
    expect(statusLine({ ...base, remaining: 0 }, "")).toEqual({ text: "Everything is checked", clear: true });
    expect(statusLine({ ...base, flagged: 0, remaining: 0 }, "")).toEqual({ text: "No words need a look", clear: true });
    expect(statusLine({ ...base, status: "received", remaining: 0 }, "Scanning page 1 of 1").clear).toBe(false);
  });

  it("keeps the panel inside 320px: Ask and the quiet row wrap, the tabs give way to More", () => {
    const text = css(VIEW_CSS);
    expect(text).toMatch(/\.ask \{[^}]*width: 100%;/);
    expect(text).toMatch(/\.headActions \{[^}]*flex-wrap: wrap;[^}]*max-width: 100%;/);
    expect(text).toMatch(/\.keyFact \{[^}]*flex-wrap: wrap;[^}]*min-width: 0;/);
    expect(text).toMatch(/\.summaryText \{[^}]*overflow-wrap: break-word;/);
    expect(text).toMatch(/\.tabs \{[^}]*min-width: 0;/);
    expect(text).toMatch(/\.status\[hidden\] \{\s*display: none;/);
    expect(text).toMatch(/\.title \{[^}]*overflow-wrap: anywhere;/);
  });
});

describe("motion", () => {
  const TOUCHED = [
    VIEW_CSS,
    "app/(app)/documents/[id]/WordReview.module.css",
    "components/files/Files.module.css",
    "components/viewer/ReadClearly.module.css",
  ];

  it("moves only where reduced motion allows it", () => {
    const found: string[] = [];
    for (const file of TOUCHED) {
      const text = css(file);
      const stack: string[] = [];
      let start = 0;
      for (let i = 0; i < text.length; i += 1) {
        if (text[i] === "{") {
          const prelude = text.slice(start, i).trim();
          if (prelude.startsWith("@")) stack.push(prelude);
          else {
            const end = text.indexOf("}", i);
            const body = text.slice(i + 1, end);
            const moving = /(?:^|;)\s*(animation|transition)\s*:/.test(body) && !/(animation|transition):\s*none/.test(body);
            if (moving && !stack.some((at) => at.includes("prefers-reduced-motion: no-preference"))) found.push(`${file}: ${prelude}`);
            i = end;
          }
          start = i + 1;
        } else if (text[i] === "}") {
          stack.pop();
          start = i + 1;
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("draws the check in only when the line has just turned clear, not on load", () => {
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).toContain("setChanged(seen === false && line.clear);");
    expect(css("app/(app)/documents/[id]/WordReview.module.css")).toMatch(
      /\.status\[data-changed\] \.statusIcon path \{[^}]*animation: draw-check var\(--motion-draw\) var\(--ease-out\) both;/,
    );
  });

  it("rises the summary in once: on the card above the tabs, never on a tab change", () => {
    const view = read(VIEW);
    expect(view.indexOf("<SummaryCard")).toBeLessThan(view.indexOf("<PanelTabs"));
    expect(css(VIEW_CSS)).toMatch(/\.summaryCard,\s*\.keyFact \{\s*animation: rise-in var\(--motion-calm\) var\(--ease-out\) both;/);
    expect(css(VIEW_CSS)).toMatch(/@keyframes rise-in \{\s*from \{\s*opacity: 0;\s*transform: translateY\(6px\);/);
  });
});

// Changed on purpose (Tushar, 24 Oct 2026, "remove document card fully"): the Document card is
// gone from Details; its facts live in the AI summary at the top (SummaryCard).
describe("the Details tab without the Document card", () => {
  it("never shows the Document card", () => {
    expect(read(VIEW)).not.toContain("<CardPanel");
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx")).not.toContain("Document card");
  });
});
