// "Scan with" (PRD 4 #17, components/documents/readers.ts): the options come
// from the server's readers, never a list of our own; the choice is sent only
// when it is not the server's default; and the select is labelled.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DUPLICATE_READER_NOTE,
  offersChoice,
  pickedReader,
  readerOptions,
  readerToSend,
} from "@/components/documents/readers";
import type { UploadReader } from "@/lib/types";

// As GET /upload-limits answers on a laptop with the backup reader: Auto first (PRD 5 U8 titles).
const auto: UploadReader = {
  id: "auto",
  title: "Auto",
  hint: "The usual reader here: the backup reader",
  available: true,
  reason: null,
};
const paddle: UploadReader = {
  id: "paddleocr_vl",
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
const LAPTOP = [auto, paddle, rapid];

describe("Scan with options", () => {
  it("names the first recommended and disables a reader the server can't use, saying why", () => {
    expect(readerOptions(LAPTOP)).toEqual([
      { id: "auto", label: "Auto (recommended)", disabled: false },
      { id: "paddleocr_vl", label: "Layout reader (Not set up on this server)", disabled: true },
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
    expect(offersChoice([auto, paddle])).toBe(false);
    const missing = { ...rapid, available: false, reason: "Not installed on this server" };
    expect(offersChoice([auto, missing])).toBe(false);
    expect(offersChoice([])).toBe(false);
  });

  it("falls back to the default when the pick is gone or can't be used", () => {
    expect(pickedReader(LAPTOP, null)).toBe(auto);
    expect(pickedReader(LAPTOP, "rapidocr")).toBe(rapid);
    expect(pickedReader(LAPTOP, "paddleocr_vl")).toBe(auto);
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
    expect(readerToSend(LAPTOP, "paddleocr_vl")).toBeUndefined();
    expect(readerToSend([auto, paddle], "paddleocr_vl")).toBeUndefined();
    expect(readerToSend([], "rapidocr")).toBeUndefined();
  });

  it("tells a duplicate it keeps the reader it was read with", () => {
    expect(DUPLICATE_READER_NOTE).toBe("Already uploaded; it keeps the reader it was read with.");
  });
});

describe("the Scan with select in the source", () => {
  const source = readFileSync(join(__dirname, "..", "components/documents/UploadArea.tsx"), "utf8");
  const reader = source.slice(source.indexOf("export function ReaderSelect"));
  const select = reader.slice(reader.indexOf("<select"), reader.indexOf("</select>"));

  it("is a native select with a visible label tied to it", () => {
    expect(reader).toContain("<label htmlFor={`${id}-select`}");
    expect(reader).toMatch(/>\s*Scan with\s*<\/label>/);
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
