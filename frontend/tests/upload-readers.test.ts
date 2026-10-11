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
  it("names the first recommended and disables a reader the server can't use, saying why", () => {
    expect(readerOptions(LAPTOP)).toEqual([
      { id: "auto", label: "Auto (recommended)", disabled: false },
      { id: "layout_reader", label: "Layout reader (Not set up on this server)", disabled: true },
      { id: "rapidocr", label: "Backup reader", disabled: false },
    ]);
  });

  it("takes whatever readers the server names, in its order", () => {
    const other: UploadReader = { ...rapid, id: "some_new_reader", title: "Some new reader" };
    expect(readerOptions([auto, other]).map((option) => option.label)).toEqual([
      "Auto (recommended)",
      "Some new reader",
    ]);
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

  it("tells a duplicate it keeps the reader it was read with", () => {
    expect(DUPLICATE_READER_NOTE).toBe("Already uploaded; it keeps the reader it was read with.");
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

  it("is offered beside Upload files and in the slim dropzone", () => {
    const view = readFileSync(join(__dirname, "..", "components/documents/DocumentsView.tsx"), "utf8");
    expect(view).toMatch(/\{readerSelect\}\s*\{uploadButton\}/);
    expect(view).toContain("readerChoice={readerSelect}");
  });
});

// Model choice: the organisation's models, as GET /upload-limits sends them (default first).
const gemini: UploadReader = {
  id: "google/gemini-3.1-pro-preview",
  title: "Gemini 3.1 Pro",
  hint: "about $0.05 a page",
  available: true,
  reason: null,
};
const sonnet: UploadReader = {
  id: "anthropic/claude-sonnet-5.5",
  title: "Claude Sonnet 5.5",
  hint: "about $0.08 a page",
  available: true,
  reason: null,
};

describe("Read with, on screen (MS4)", () => {
  const render = (readers: UploadReader[]) =>
    renderToStaticMarkup(createElement(ReaderSelect, { readers, picked: null, onPick: () => undefined }));

  it("is hidden with one model", () => {
    expect(render([gemini])).toBe("");
  });

  it("is shown with two, labelled Read with, the default picked, its price below", () => {
    const html = render([gemini, sonnet]);
    expect(html).toMatch(/<label[^>]*>Read with<\/label>/);
    expect(html).toContain("Gemini 3.1 Pro (recommended)");
    expect(html).toContain("Claude Sonnet 5.5");
    expect(html).toContain("about $0.05 a page");
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

  it("warns that every page is read again by that model", () => {
    expect(readAgainWarning(12, "Claude Sonnet 5.5")).toBe("All 12 pages will be read again by Claude Sonnet 5.5.");
    expect(readAgainWarning(1, "Gemini 3.1 Pro")).toBe("The page will be read again by Gemini 3.1 Pro.");
    expect(readAgainWarning(null, "Gemini 3.1 Pro")).toBe("All pages will be read again by Gemini 3.1 Pro.");
  });

  it("is offered in the Read again question, and per-page Read again has no model choice", () => {
    const dialog = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/ReadAgain.tsx"), "utf8");
    // The select starts on the document's own model (read_model), not the default.
    expect(dialog).toContain("<ReaderSelect readers={readers} picked={picked ?? readModel} onPick={setPicked} />");
    const view = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("readModel={detail.read_model}");
    expect(dialog).toContain("reprocessDocument(documentId, model)");
    const page = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/PageProblems.tsx"), "utf8");
    expect(page).not.toContain("ReaderSelect");
    expect(page).toContain("readPageAgain(documentId, page.page_no)");
  });
});
