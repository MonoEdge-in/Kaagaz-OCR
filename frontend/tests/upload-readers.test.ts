// "Read with" (PRD 4 #17, model choice MS4, components/documents/readers.ts):
// the options come from the server's readers, never a list of our own; the
// choice is sent as read_model only when it is not the organisation's default;
// and the select is labelled.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  DUPLICATE_READER_NOTE,
  offersChoice,
  pickedReader,
  readAgainModel,
  readAgainWarning,
  readerOptions,
  readerToSend,
} from "@/components/documents/readers";
import { ReaderSelect } from "@/components/documents/UploadArea";
import type { UploadReader } from "@/lib/types";

// As GET /upload-limits answers on a laptop with the backup reader: Auto first (PRD 5 U8 titles).
const auto: UploadReader = {
  id: "auto",
  title: "Auto",
  hint: "The usual reader here: the backup reader",
  available: true,
  reason: null,
};
const layout: UploadReader = {
  id: "layout_reader",
  title: "Layout reader",
  hint: "Best for handwriting and complex pages (slower)",
  available: false,
  reason: "Not set up on this server",
};
const rapid: UploadReader = {
  id: "rapidocr",
  title: "Backup reader",
  hint: "Fast, for clean printed pages",
  available: true,
  reason: null,
};
const LAPTOP = [auto, layout, rapid];

describe("Read with options", () => {
  // Changed on purpose (UI quality council, 15 Oct 2026): no "(recommended)"; the server
  // names the tiers itself ("Standard", "Careful"; no cost words, Tushar 15 Oct).
  it("names each reader by its title and disables one the server can't use, saying why", () => {
    expect(readerOptions(LAPTOP)).toEqual([
      { id: "auto", label: "Auto", disabled: false },
      { id: "layout_reader", label: "Layout reader (Not set up on this server)", disabled: true },
      { id: "rapidocr", label: "Backup reader", disabled: false },
    ]);
  });

  it("takes whatever readers the server names, in its order", () => {
    const other: UploadReader = { ...rapid, id: "some_new_reader", title: "Some new reader" };
    expect(readerOptions([auto, other]).map((option) => option.label)).toEqual(["Auto", "Some new reader"]);
  });

  it("is shown only when two or more readers can be used", () => {
    expect(offersChoice(LAPTOP)).toBe(true);
    expect(offersChoice([auto, layout])).toBe(false);
    const missing = { ...rapid, available: false, reason: "Not installed on this server" };
    expect(offersChoice([auto, missing])).toBe(false);
    expect(offersChoice([])).toBe(false);
  });

  it("falls back to the default when the pick is gone or can't be used", () => {
    expect(pickedReader(LAPTOP, null)).toBe(auto);
    expect(pickedReader(LAPTOP, "rapidocr")).toBe(rapid);
    expect(pickedReader(LAPTOP, "layout_reader")).toBe(auto);
    expect(pickedReader(LAPTOP, "gone")).toBe(auto);
    expect(pickedReader([], "rapidocr")).toBeNull();
  });
});

describe("what an upload sends", () => {
  it("sends the picked reader, and nothing for the default", () => {
    expect(readerToSend(LAPTOP, "rapidocr")).toBe("rapidocr");
    expect(readerToSend(LAPTOP, "auto")).toBeUndefined();
    expect(readerToSend(LAPTOP, null)).toBeUndefined();
  });

  it("sends nothing for a reader the server can't use, or when there is no choice", () => {
    expect(readerToSend(LAPTOP, "layout_reader")).toBeUndefined();
    expect(readerToSend([auto, layout], "layout_reader")).toBeUndefined();
    expect(readerToSend([], "rapidocr")).toBeUndefined();
  });

  it("tells a duplicate it was not read again", () => {
    // Changed on purpose (UI quality council, 15 Oct 2026): plain words.
    expect(DUPLICATE_READER_NOTE).toBe("Already uploaded, so it was not read again.");
  });
});

describe("the Read with select in the source", () => {
  const source = readFileSync(join(__dirname, "..", "components/documents/UploadArea.tsx"), "utf8");
  const reader = source.slice(source.indexOf("export function ReaderSelect"));
  const select = reader.slice(reader.indexOf("<select"), reader.indexOf("</select>"));

  it("is a native select with a visible label tied to it", () => {
    expect(reader).toContain("<label htmlFor={`${id}-select`}");
    expect(reader).toMatch(/>\s*Read with\s*<\/label>/);
    expect(select).toContain("id={`${id}-select`}");
  });

  it("points at the picked reader's hint and uses the shared control size", () => {
    expect(select).toContain("aria-describedby={`${id}-hint`}");
    expect(reader).toContain('<p id={`${id}-hint`}');
    expect(select).toContain("className={`input ");
  });

  it("is offered once, alone in the page header", () => {
    const view = readFileSync(join(__dirname, "..", "components/documents/DocumentsView.tsx"), "utf8");
    // Changed on purpose (Tushar, 16 Oct 2026): the header has no Upload files beside it.
    expect(view).toContain("<div className={styles.uploadControls}>{readerSelect}</div>");
    // Changed on purpose (UI touch-up council, 15 Oct 2026): the slim dropzone no longer repeats
    // it; two "Read with" pickers on one screen looked like two different choices.
    expect(view.match(/\{readerSelect\}/g)).toHaveLength(1);
    expect(readFileSync(join(__dirname, "..", "components/documents/UploadArea.tsx"), "utf8")).not.toContain("readerChoice");
  });

  // Tushar, 16 Oct 2026: two upload buttons side by side. The app header's Upload is the one
  // upload action with documents; an empty list's empty state keeps its own Upload files.
  it("leaves Upload files out of the page header, and keeps it in the empty state", () => {
    const view = readFileSync(join(__dirname, "..", "components/documents/DocumentsView.tsx"), "utf8");
    const header = view.slice(view.indexOf('<header className="page-header">'), view.indexOf("</header>"));
    expect(header).toContain("{readerSelect}");
    expect(header).not.toContain("uploadButton");
    expect(view.match(/\{uploadButton\}/g)).toHaveLength(1);
    expect(view).toMatch(/view === "empty" \? \(\s*<Dropzone[\s\S]*?action=\{\s*<>\s*\{uploadButton\}/);
  });

  it("lays Read with out as one row in the header: the label beside the select, the hint under it", () => {
    const docs = readFileSync(join(__dirname, "..", "components/documents/Documents.module.css"), "utf8");
    expect(docs).toMatch(/\.uploadControls \.reader \{[^}]*display: grid;[^}]*grid-template-columns: auto minmax\(0, auto\);/);
    expect(docs).toMatch(/\.uploadControls \.readerHint \{[^}]*grid-column: 2;/);
    expect(docs).toMatch(/\.uploadControls \.readerSelect \{[^}]*width: auto;/); // beats select.input's 100%
    expect(docs).toMatch(/@media \(max-width: 639\.98px\) \{\s*\.uploadControls \{[^}]*justify-content: flex-start;/);
  });
});

// Model choice: the organisation's two tiers, as GET /upload-limits sends them (Standard
// first; backend/kaagaz/api/readers.py picks them by price).
const gemini: UploadReader = {
  id: "google/gemini-3.1-pro-preview",
  title: "Standard",
  hint: "Right for most documents.",
  available: true,
  reason: null,
  default: true,
};
const sonnet: UploadReader = {
  id: "anthropic/claude-sonnet-5.5",
  title: "Careful",
  hint: "Takes more care with faint, handwritten or crowded pages.",
  available: true,
  reason: null,
  default: false,
};

describe("Read with, on screen (MS4)", () => {
  const render = (readers: UploadReader[]) =>
    renderToStaticMarkup(createElement(ReaderSelect, { readers, picked: null, onPick: () => undefined }));

  it("is hidden with one model", () => {
    expect(render([gemini])).toBe("");
  });

  it("is shown with two, labelled Read with, Standard picked, its hint below, never a model or price", () => {
    const html = render([gemini, sonnet]);
    expect(html).toMatch(/<label[^>]*>Read with<\/label>/);
    expect(html).toMatch(/<option value="google\/gemini-3.1-pro-preview" selected="">Standard<\/option>/);
    expect(html).toContain(">Careful</option>");
    // Changed on purpose (UI touch-up, 15 Oct 2026): no cost words on org screens (Tushar).
    expect(html.replace(/<[^>]*>/g, " ")).not.toMatch(/cost|paid|price|charge|cheap|expensive/i);
    expect(html).toContain("Right for most documents.");
    // The words shown, not the option values sent.
    expect(html.replace(/<[^>]*>/g, " ")).not.toMatch(/gemini|claude|\$|recommended/i);
  });
});

describe("the default, only the real one (R14)", () => {
  // The organisation's default can't be used now: the server marks none.
  const unmarked = [
    { ...sonnet, default: false },
    { ...gemini, default: false },
  ];


  it("sends the model shown when none is the default, nothing for the real default", () => {
    expect(readerToSend(unmarked, null)).toBe(sonnet.id);
    expect(readerToSend([gemini, sonnet], null)).toBeUndefined();
    expect(readerToSend([gemini, sonnet], sonnet.id)).toBe(sonnet.id);
  });
});

describe("Read again for the whole document (MS4)", () => {
  it("sends nothing until the user picks, so the document keeps its own model", () => {
    expect(readAgainModel([gemini, sonnet], null)).toBeUndefined();
  });

  it("sends the model the user picked, the default too", () => {
    expect(readAgainModel([gemini, sonnet], sonnet.id)).toBe(sonnet.id);
    expect(readAgainModel([gemini, sonnet], gemini.id)).toBe(gemini.id);
    expect(readAgainModel([gemini, sonnet], "gone/model")).toBeUndefined();
  });

  it("sends none when there is no choice", () => {
    expect(readAgainModel([gemini], gemini.id)).toBeUndefined();
    expect(readAgainModel([], null)).toBeUndefined();
  });

  // Changed on purpose (UI touch-up, 15 Oct 2026): no cost words on org screens (Tushar): no model name, no "paid for again", no "Careful costs more".
  it("says every page is read again, in plain words with no cost", () => {
    expect(readAgainWarning(12)).toBe("All 12 pages will be read again.");
    expect(readAgainWarning(1)).toBe("The page will be read again.");
    expect(readAgainWarning(null)).toBe("Every page will be read again.");
  });

  it("is offered in the Read again question, and per-page Read again has no model choice", () => {
    const dialog = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/ReadAgain.tsx"), "utf8");
    // The select starts on the document's own model (read_model), not the default.
    // Changed on purpose (Ensemble after the scan, 16 Oct 2026): no "More than one reader…" here.
    expect(dialog).toMatch(/<ReaderSelect\s+readers=\{readers\}\s+picked=\{picked \?\? readModel\}\s+onPick=\{setPicked\}/);
    expect(dialog).not.toMatch(/ensemble=\{|agents=\{/);
    const view = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("readModel={detail.read_model}");
    expect(dialog).toContain("reprocessDocument(documentId, model)");
    expect(dialog).toContain("readAgainWarning(pages)");
    const page = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/PageProblems.tsx"), "utf8");
    expect(page).not.toContain("ReaderSelect");
    expect(page).toContain("readPageAgain(documentId, page.page_no)");
  });
});
