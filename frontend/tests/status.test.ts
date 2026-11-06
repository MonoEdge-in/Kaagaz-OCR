// The six-state mapping (docs/architecture.md, The screens): every value the backend
// sends lands on exactly one state.

import { describe, expect, it } from "vitest";
import {
  answerStatus,
  documentStatus,
  documentWords,
  failureFix,
  pageStatus,
  STATUS_WORDS,
  valueStatus,
} from "@/lib/status";
import type { DocumentStatus, LabelReason } from "@/lib/types";

const REASONS: LabelReason[] = [
  "direct_cell",
  "readers_agree",
  "readers_disagree",
  "single_reader",
  "low_confidence",
  "corrected",
  "learned_correction",
  "model_chosen",
  "ocr_script",
  "check_failed",
  "unsure_text",
  "box_unanchored",
  "local_reader",
  "mixed_digits",
  "amount_commas",
  "amount_mark",
  "amount_outlier",
];

describe("STATUS_WORDS", () => {
  // The review state keeps "Ready to verify" (tests/ready-to-verify.test.ts), not the handoff's "Needs review".
  it("names the six states", () => {
    expect(STATUS_WORDS).toEqual({
      verified: "Verified",
      review: "Ready to verify",
      from: "From documents",
      notfound: "Not found",
      reading: "Reading",
      failed: "Failed",
    });
  });
});

describe("valueStatus", () => {
  it("keeps the label, with the reason in plain words", () => {
    expect(valueStatus("verified", "readers_agree")).toEqual({ state: "verified", reason: null });
    expect(valueStatus("verified", "direct_cell")).toEqual({ state: "verified", reason: null });
    expect(valueStatus("needs_review", "readers_disagree")).toEqual({
      state: "review",
      reason: "Two readers read this differently",
    });
    for (const reason of ["unsure_text", "low_confidence", "ocr_script"] as const) {
      expect(valueStatus("needs_review", reason).reason).toBe("Hard to read");
    }
    expect(valueStatus("needs_review", "box_unanchored").reason).toBe("The exact spot isn't confirmed");
    expect(valueStatus("needs_review", "check_failed").reason).toBe("The totals don't add up");
    expect(valueStatus("needs_review", "single_reader").reason).toBe("Only one reader saw this");
    expect(valueStatus("needs_review", "model_chosen").reason).toBe("Picked by Kaagaz, check the page");
    expect(valueStatus("needs_review", "local_reader").reason).toBe("Read by the backup reader");
    expect(valueStatus("needs_review", "mixed_digits").reason).toBe("Mixed Marathi and English digits");
    expect(valueStatus("needs_review", "amount_commas").reason).toBe("The amount's commas look wrong");
    expect(valueStatus("needs_review", "direct_cell")).toEqual({ state: "review", reason: null });
  });

  it("calls a corrected value Verified, fixed by a reviewer", () => {
    for (const reason of ["corrected", "learned_correction"] as const) {
      expect(valueStatus("verified", reason)).toEqual({ state: "verified", reason: "Fixed by a reviewer" });
      expect(valueStatus("needs_review", reason)).toEqual({ state: "verified", reason: "Fixed by a reviewer" });
    }
  });

  it("never turns a needs review value Verified unless a reviewer fixed it", () => {
    for (const reason of REASONS) {
      if (reason === "corrected" || reason === "learned_correction") continue;
      expect(valueStatus("needs_review", reason).state, reason).toBe("review");
    }
  });
});

describe("answerStatus", () => {
  it("maps every answer status and label", () => {
    expect(answerStatus({ status: "answered", label: "verified" })).toBe("verified");
    expect(answerStatus({ status: "answered", label: "needs_review" })).toBe("review");
    expect(answerStatus({ status: "answered", label: null })).toBe("from");
    expect(answerStatus({ status: "unchecked", label: null })).toBe("review");
    expect(answerStatus({ status: "unchecked", label: "needs_review" })).toBe("review");
    expect(answerStatus({ status: "not_found", label: null })).toBe("notfound");
  });
});

describe("pageStatus", () => {
  it("gives a page read well no badge", () => {
    expect(pageStatus("good")).toBeNull();
    expect(pageStatus("uncertain")).toBe("review");
    expect(pageStatus("unreadable")).toBe("failed");
  });
});

describe("documentStatus", () => {
  const cases: [DocumentStatus, "reading" | "failed" | null, string][] = [
    ["received", "reading", "Reading"],
    ["clean", "reading", "Reading"],
    ["ready", null, "Ready"],
    ["failed", "failed", "Failed"],
    ["rejected", "failed", "Failed"],
    ["needs_reader", "failed", "Failed"],
  ];

  it.each(cases)("%s is %s", (status, state, words) => {
    expect(documentStatus(status)).toBe(state);
    expect(documentWords(status)).toBe(words);
  });
});

describe("failureFix", () => {
  it("has a fix line for every failure code the backend sends", () => {
    expect(failureFix("failed", "too_many_pages")).toBe("Split it into parts and upload each one.");
    expect(failureFix("failed", "retries_exhausted")).toBe("Read it again, or ask your admin to look at it.");
    expect(failureFix("failed", "unreadable")).toBe("Upload a sharper scan.");
    expect(failureFix("failed", "ocr_failed")).toBe("Read it again, or upload a clearer scan.");
    expect(failureFix("failed", "customer_disabled")).toBe("Read it again.");
    expect(failureFix("rejected", "virus_found")).toBe("Upload a clean copy of the file.");
    expect(failureFix("rejected", "unscannable")).toBe("Save it again and upload that copy.");
    expect(failureFix("rejected", "blocked_content")).toBe(
      "Save it again as a plain PDF, photo or spreadsheet and upload that.",
    );
    expect(failureFix("needs_reader", null)).toBe("Upload it as a PDF, photo or spreadsheet.");
  });

  it("falls back to one line for a code it does not know", () => {
    expect(failureFix("failed", "unknown")).toBe("Read it again, or try another copy of it.");
    expect(failureFix("failed", null)).toBe("Read it again, or try another copy of it.");
    expect(failureFix("failed", "toString")).toBe("Read it again, or try another copy of it.");
  });
});
