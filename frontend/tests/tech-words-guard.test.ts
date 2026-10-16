// Technical words stay off user and org-admin screens (UI quality council, 15 Oct 2026):
// no model or provider name, no DPI, no percentage, no tokens, no "model" and no "OCR" in
// any text these screens build. The super admin console (components/operator/) keeps them.
// One exception: the org admin's Usage screen shows tokens, and no money (Tushar, 15 Oct 2026).

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  TABS,
  describeAction,
  describeDetails,
  describeTarget,
  breakdowns,
  kpiTiles,
} from "@/components/admin/logic";
import { UsageReportView } from "@/components/admin/UsagePanel";
import { DUPLICATE_READER_NOTE, readAgainWarning, readerOptions } from "@/components/documents/readers";
import { REASON_FILTERS } from "@/components/review/review";
import { REASON_FILTER_TEXT, reasonText, voteText } from "@/components/viewer/labels";
import { lowResolutionText, noCardText } from "@/components/viewer/panels";
import {
  CHECKER,
  CONFIDENCE_NOTE,
  MAIN_READER,
  confidenceText,
  readerHeading,
  secondReaderNote,
} from "@/components/viewer/scanned";
import type {
  DocumentStatus,
  LabelReason,
  PageRoute,
  ReadIssue,
  ReadLine,
  Stats,
  UsageReport,
  UsageTotals,
} from "@/lib/types";

// "satisfies" makes the type checker fail when a new reason, issue, route or status is added.
const REASONS = Object.keys({
  direct_cell: 1,
  readers_agree: 1,
  readers_disagree: 1,
  single_reader: 1,
  low_confidence: 1,
  corrected: 1,
  learned_correction: 1,
  model_chosen: 1,
  ocr_script: 1,
  check_failed: 1,
  unsure_text: 1,
  box_unanchored: 1,
  local_reader: 1,
} satisfies Record<LabelReason, 1>) as LabelReason[];
const ISSUES = Object.keys({
  blurry: 1,
  low_resolution: 1,
  faint: 1,
  glare: 1,
  shadow: 1,
  cut_off: 1,
  stamp_over_text: 1,
  overwritten: 1,
  handwriting_hard: 1,
  illegible_words: 1,
  readers_disagree: 1,
  unpinned: 1,
  reader_refused: 1,
  read_locally: 1,
  no_text: 1,
} satisfies Record<ReadIssue, 1>) as ReadIssue[];
const ROUTES = Object.keys({ cells: 1, text_layer: 1, ocr: 1, blank: 1 } satisfies Record<PageRoute, 1>) as PageRoute[];
const STATUSES = Object.keys({
  received: 1,
  clean: 1,
  rejected: 1,
  needs_reader: 1,
  ready: 1,
  failed: 1,
} satisfies Record<DocumentStatus, 1>) as DocumentStatus[];

const TECHNICAL = /gemini|claude|openrouter|google\/|\bOCR\b|\bDPI\b|\d+\s?%|tokens?\b|\bmodel\b/i;

const line = (confidence: number | null): ReadLine => ({
  text: "x",
  confidence,
  origin: "ocr",
  kind: null,
  region: { kind: "box", page: 1, box: [0, 0, 1, 1] },
});

const STATS: Stats = {
  documents_by_status: { ready: 3 },
  questions_total: 1,
  answered: 1,
  not_found: 0,
  verified_answers: 1,
  needs_review_answers: 0,
  feedback_up: 0,
  feedback_down: 0,
  open_review_items: 0,
  older_versions: 0,
  in_trash: 0,
  pages_this_month: { cells: 1, text_layer: 2, ocr: 3, blank: 4 },
};

/** Every text the user and admin screens build from these helpers. */
function shownTexts(): string[] {
  const texts: (string | null)[] = [
    CONFIDENCE_NOTE,
    MAIN_READER,
    CHECKER,
    DUPLICATE_READER_NOTE,
    ...REASONS.map(reasonText),
    ...Object.values(REASON_FILTER_TEXT),
    ...REASON_FILTERS.map((choice) => choice.text),
    ...[...ROUTES, null].map(secondReaderNote),
    ...STATUSES.map(noCardText),
    lowResolutionText({ low_resolution: true, effective_dpi: 96.4 }),
    ...TABS.map((tab) => tab.label),
    // Changed on purpose (UI audit, 16 Oct 2026): AD4, four headline numbers and small lists.
    ...kpiTiles(STATS).flatMap((tile) => [tile.label, tile.note ?? null]),
    ...breakdowns(STATS).flatMap((group) => [group.title, group.none, ...group.rows.map((row) => row.label)]),
    ...[...STATUSES, "answered"].map((status) =>
      describeDetails({ status, page_no: 3, versions: 2, tag_count: 1, max_upload_mb: 25, role: "admin" }),
    ),
    describeDetails({ read_model: "google/gemini-3.1-pro-preview", model: "x", input_tokens: 9, folder_id: "f" }),
    ...["document", "document_story", "extracted_value", "folder", "tag", "user", "user_word", "message", "customer"].map(
      describeTarget,
    ),
    ...[12, 1, null].map(readAgainWarning),
  ];
  for (const confidence of [null, Number.NaN, 0, 0.62, 0.8999, 0.9, 0.97, 1]) {
    for (const origin of ["ocr", "cell", "text_layer", null] as const) texts.push(confidenceText(confidence, origin));
  }
  for (const role of ["first", "second"] as const) {
    for (const issues of [[], ...ISSUES.map((issue) => [issue])]) {
      const heading = readerHeading(
        { role, reader: "gemini-ocr", reader_version: "1", truncated: false, lines: [line(0.62), line(0.97)] },
        { read_issues: issues },
      );
      texts.push(heading.title, heading.notSureCount);
    }
  }
  // Features 25-27 (15 Oct 2026): Dynamic and a value's vote. Changed on purpose (Ensemble
  // after the scan, 16 Oct 2026): the upload picker has no "More than one reader…" now.
  const options = readerOptions([
    { id: "dynamic", title: "Dynamic", hint: "", available: true, reason: null, default: true },
    { id: "google/gemini-3.1-pro-preview", title: "Standard", hint: "", available: true, reason: null },
    { id: "anthropic/claude-sonnet-5.5", title: "Careful", hint: "", available: true, reason: null },
  ]);
  texts.push(...options.map((option) => option.label));
  const reads = [
    { main: true, text: "1,130.00" },
    { main: false, text: "1,180.00" },
    { main: false, text: null },
  ];
  texts.push(voteText({ value: "1,180.00", agreed: 2, readers: 3, reads }));
  texts.push(voteText({ value: null, agreed: 1, readers: 3, reads }));
  return texts.filter((text): text is string => text !== null);
}

// No money on user and org-admin screens (Tushar, 15 Oct 2026): money is the super admin's only.
const MONEY = /[$₹]|\bRs\b|\bUSD\b|\bprice|\bcost|\bbudget|\bspen[dt]|\bpaid\b/i;

describe("technical words on user and admin screens", () => {
  it("are in none of the texts these screens build", () => {
    const texts = shownTexts();
    expect(texts.length).toBeGreaterThan(100);
    for (const text of texts) expect(text, text).not.toMatch(TECHNICAL);
  });

  // Changed on purpose (features 25-27, 15 Oct 2026): money words are banned outside the operator console too.
  it("hold no money words", () => {
    for (const text of shownTexts()) expect(text, text).not.toMatch(MONEY);
  });

  // Features 25-27: a model's name reaches a member only where they pick readers by name.
  it("show model names from the server only in the upload reader picker", () => {
    const app = join(__dirname, "..");
    const files = ["app/(app)", "components"].flatMap((dir) =>
      readdirSync(join(app, dir), { recursive: true, encoding: "utf8" })
        .filter((file) => /\.tsx?$/.test(file))
        .map((file) => join(dir, file).replace(/\\/g, "/")),
    );
    const picker = new Set([
      "components/documents/readers.ts",
      "components/documents/UploadArea.tsx",
      "components/documents/DocumentsView.tsx",
      "app/(app)/documents/[id]/ReadAgain.tsx",
    ]);
    const naming = files.filter(
      (file) =>
        !file.startsWith("components/operator/") &&
        !picker.has(file) &&
        /\bEnsembleOffer\b|limits\.ensemble\b/.test(readFileSync(join(app, file), "utf8")),
    );
    expect(naming).toEqual([]);
  });

  it("are in none of the words for the audit actions the backend writes", () => {
    const root = join(__dirname, "..", "..", "backend", "kaagaz");
    const files = readdirSync(root, { recursive: true, encoding: "utf8" }).filter((file) => file.endsWith(".py"));
    const actions = /"((?:auth|user|user_word|document|document_story|folder|tag|review|chat|settings|customer)\.[a-z_]+)"/g;
    for (const file of files) {
      for (const [, action = ""] of readFileSync(join(root, file), "utf8").matchAll(actions)) {
        expect(describeAction(action), action).not.toMatch(TECHNICAL);
      }
    }
  });

  it("are not read from the API on any screen but the super admin's", () => {
    const fields = /\b(read_by_name|input_tokens|output_tokens|by_model|model_name)\b/;
    const app = join(__dirname, "..");
    const files = ["app/(app)", "components"].flatMap((dir) =>
      readdirSync(join(app, dir), { recursive: true, encoding: "utf8" })
        .filter((file) => /\.tsx?$/.test(file))
        .map((file) => join(dir, file).replace(/\\/g, "/")),
    );
    // The org admin's Usage screen adds input and output tokens (Tushar, 15 Oct 2026); only that file.
    const readers = files.filter(
      (file) =>
        !file.startsWith("components/operator/") &&
        file !== "components/admin/UsagePanel.tsx" &&
        fields.test(readFileSync(join(app, file), "utf8")),
    );
    expect(readers).toEqual([]);
  });
});

describe("the org admin's Usage screen", () => {
  const totals: UsageTotals = {
    calls: 3,
    cached_calls: 0,
    input_tokens: 900,
    output_tokens: 100,
    reasoning_tokens: 40,
    cost_usd: 0.004,
    cost_estimated: true,
    cost_part_unknown: true,
  };
  const report: UsageReport = {
    from: "2026-10-01",
    to: "2026-10-15",
    totals,
    by_user: [{ ...totals, user_id: null, user_name: null }],
    by_model: [{ ...totals, model: "google/gemini-3.1-pro-preview", model_name: "Gemini 3.1 Pro" }],
    by_kind: [{ ...totals, kind: "chat" }],
    by_day: [{ ...totals, day: "2026-10-15" }],
    top_documents: [{ ...totals, document_id: "d1", name: "Invoice.pdf" }],
    // Changed on purpose (features 25-27, 15 Oct 2026): GET /admin/usage sends no budget any more.
    ai_enabled: true,
  };

  it("shows no money and no model, only tokens", () => {
    const texts = [
      renderToStaticMarkup(createElement(UsageReportView, { state: { kind: "ready", report }, onRetry: () => undefined })),
      renderToStaticMarkup(createElement(UsageReportView, { state: { kind: "off" }, onRetry: () => undefined })),
    ];
    for (const text of texts) {
      expect(text, text).not.toMatch(/[$₹]|\bRs\b|\bUSD\b|spen[dt]|cost/i);
      expect(text, text).not.toMatch(/gemini|claude|openrouter|google\/|\bmodel\b/i);
    }
    expect(texts[0]).toContain("1,000");
  });
});
