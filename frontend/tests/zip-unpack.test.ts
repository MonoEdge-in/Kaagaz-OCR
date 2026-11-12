// A zip upload the server unpacks into a folder (#522), in the upload tray: the server's line
// while it unpacks and once it has, the folder to open, and each skipped file with why. Rendered
// in node from fixtures (components/documents/zipUnpack.ts, ZipUnpackNote.tsx).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ZipUnpackNote } from "@/components/documents/ZipUnpackNote";
import { moreSkippedText, zipNote } from "@/components/documents/zipUnpack";
import type { DocumentSummary, ZipUnpack } from "@/lib/types";

const UNPACK: ZipUnpack = {
  folder_upload_id: "u1",
  folder_id: "f1",
  folder_name: "Bills",
  files: 123,
  unpacked: 118,
  already_in: 2,
  skipped: 3,
  waiting: 0,
  skipped_files: [
    { name: "secret.pdf", code: "encrypted", reason: "It has a password. Remove the password, then upload it again." },
    { name: "eicar.txt", code: "virus_found", reason: "It failed the virus check." },
  ],
};
const LINE =
  'Unpacked: 118 files into folder "Bills", 2 already in Kaagaz, 3 skipped (2 password-protected, 1 failed the virus check).';

function zip(fields: Partial<DocumentSummary>): Pick<DocumentSummary, "detected_type" | "status_message" | "unpack"> {
  return { detected_type: "zip", status_message: LINE, unpack: UNPACK, ...fields };
}

describe("zipNote", () => {
  it("is the server's line, the folder and the skipped files once unpacked", () => {
    expect(zipNote(zip({}))).toEqual({
      line: LINE,
      folderId: "f1",
      done: true,
      skipped: UNPACK.skipped_files,
      more: 1, // 3 skipped, 2 listed
    });
  });

  it("follows the unpacking while files wait", () => {
    const line = 'Unpacking: 40 of 123 files into folder "Bills"…';
    expect(zipNote(zip({ status_message: line, unpack: { ...UNPACK, waiting: 83 } }))).toMatchObject({
      line,
      done: false,
    });
  });

  it("is nothing for another kind, or a zip not unpacked yet", () => {
    expect(zipNote(zip({ detected_type: "pdf" }))).toBeNull();
    expect(zipNote(zip({ unpack: null }))).toBeNull();
    expect(zipNote(zip({ unpack: undefined }))).toBeNull();
  });

  it("counts the skipped files not listed", () => {
    expect(moreSkippedText(0)).toBe("");
    expect(moreSkippedText(4)).toBe("and 4 more skipped");
  });
});

describe("ZipUnpackNote", () => {
  const render = (fields: Partial<DocumentSummary>) => {
    const note = zipNote(zip(fields));
    if (note === null) throw new Error("no note");
    return renderToStaticMarkup(createElement(ZipUnpackNote, { note }));
  };

  it("shows the result line, the folder and every skipped file with why", () => {
    const html = render({});
    expect(html).toContain("Unpacked: 118 files into folder &quot;Bills&quot;");
    expect(html).toContain("Open the folder");
    expect(html).toContain("folder=f1");
    expect(html).toContain("secret.pdf");
    expect(html).toContain("It has a password. Remove the password, then upload it again.");
    expect(html).toContain("eicar.txt");
    expect(html).toContain("and 1 more skipped");
  });

  it("offers no folder while files still wait, and no list with none skipped", () => {
    const html = render({ unpack: { ...UNPACK, waiting: 5, skipped: 0, skipped_files: [] } });
    expect(html).not.toContain("Open the folder");
    expect(html).not.toContain("<ul");
  });
});
