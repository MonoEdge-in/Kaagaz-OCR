// Dynamic (feature 26, 15 Oct 2026): "Read with" puts Dynamic first when the server
// offers it and sends the reading whole; and a value's vote in plain words ("2 of 3
// readers read …"), with no model name and no money: Ensemble documents of before keep
// their votes. Changed on purpose (Ensemble after the scan, 16 Oct 2026): "More than one
// reader…" is gone from upload and Read again; Ensemble is offered only after the scan.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { offersChoice, readAgainReading, readerOptions, readingToSend } from "@/components/documents/readers";
import { ReaderSelect } from "@/components/documents/UploadArea";
import { REASON_FILTERS } from "@/components/review/review";
import { reasonText, voteText } from "@/components/viewer/labels";
import type { UploadReader } from "@/lib/types";

// Changed on purpose (Cost saving, 16 Oct 2026): the server names it "Automatic" for members.
const dynamic: UploadReader = {
  id: "dynamic",
  title: "Automatic",
  hint: "Reads clear pages quickly and takes extra care with hard-to-read words.",
  available: true,
  reason: null,
  default: true,
};
const standard: UploadReader = {
  id: "google/gemini-3.8-flash",
  title: "Standard",
  hint: "Right for most documents.",
  available: true,
  reason: null,
  default: false,
};
const careful: UploadReader = {
  id: "google/gemini-3.1-pro-preview",
  title: "Careful",
  hint: "Takes more care with faint, handwritten or crowded pages.",
  available: true,
  reason: null,
  default: false,
};
const READERS = [dynamic, standard, careful];

describe("Read with: Dynamic", () => {
  it("is the default and is sent as nothing; another pick is sent as its model", () => {
    expect(readingToSend(READERS, null)).toBeUndefined();
    expect(readingToSend(READERS, "dynamic")).toBeUndefined();
    expect(readingToSend(READERS, careful.id)).toEqual({ read_model: careful.id });
    expect(readingToSend(READERS, standard.id)).toEqual({ read_model: standard.id });
  });

  it("is sent as read_mode when picked for a Read again", () => {
    expect(readAgainReading(READERS, "dynamic")).toEqual({ read_mode: "dynamic" });
    expect(readAgainReading(READERS, null)).toBeUndefined(); // keeps the document's reading
  });
});

describe("Read with: no more than one reader at upload", () => {
  it("offers the server's readings only: one reading is no choice", () => {
    expect(readerOptions(READERS).map((option) => option.label)).toEqual(["Automatic", "Standard", "Careful"]);
    expect(offersChoice([standard])).toBe(false);
  });

  it("has no readers to tick", () => {
    const html = renderToStaticMarkup(createElement(ReaderSelect, { readers: READERS, picked: null, onPick: () => undefined }));
    expect(html).not.toMatch(/More than one reader|<fieldset|type="checkbox"|Main reader/);
    expect(html).not.toMatch(/\$|₹|price|cost|budget|spend/i);
  });

  it("shows a member Automatic with its hint, never Cost saving (Cost saving, 16 Oct 2026)", () => {
    const html = renderToStaticMarkup(createElement(ReaderSelect, { readers: READERS, picked: null, onPick: () => undefined }));
    expect(html).toContain(">Automatic</option>");
    expect(html).toContain("Reads clear pages quickly and takes extra care with hard-to-read words.");
    expect(html).not.toMatch(/saving|>Dynamic</i); // its id "dynamic" is no word shown
  });
});

describe("a value's vote, in plain words", () => {
  it("says how many readers read the value, and what the outvoted main reader read", () => {
    const reads = [
      { main: true, text: "27AAKFS88I2L1Z9" },
      { main: false, text: "27AAKFS8812L1Z9" },
      { main: false, text: "27AAKFS8812L1Z9" },
    ];
    expect(voteText({ value: "27AAKFS8812L1Z9", agreed: 2, readers: 3, reads })).toBe(
      "2 of 3 readers read 27AAKFS8812L1Z9. The main reader read 27AAKFS88I2L1Z9.",
    );
    const agreed = reads.map((read) => ({ ...read, text: "1,180.00" }));
    expect(voteText({ value: "1,180.00", agreed: 3, readers: 3, reads: agreed })).toBe("3 of 3 readers read 1,180.00.");
  });

  it("says what each read when there was no majority, and to check the page", () => {
    const reads = [
      { main: true, text: "1,130.00" },
      { main: false, text: "1,180.00" },
      { main: false, text: null },
    ];
    expect(voteText({ value: null, agreed: 1, readers: 3, reads })).toBe(
      "The readers did not agree: the main reader read 1,130.00; another reader read 1,180.00; another reader found nothing here. Check it against the page.",
    );
  });

  it("speaks of readers, never of two", () => {
    expect(reasonText("readers_disagree")).toBe("The readers read different values.");
    expect(REASON_FILTERS.find((choice) => choice.value === "readers_disagree")?.text).toBe("Readers disagree");
  });
});
