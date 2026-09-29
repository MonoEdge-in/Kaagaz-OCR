import { describe, expect, it } from "vitest";
import {
  ACCEPT_ATTRIBUTE,
  MAX_UPLOAD_BYTES,
  canDelete,
  deletedMessage,
  estimateIngestSeconds,
  formatEtaText,
  formatProgressPercent,
  limitFor,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
  tooLargeMessage,
  UPLOAD_LIMITS_HINT,
  uploadKind,
  uploadStateText,
  validateUpload,
} from "@/components/documents/logic";
import type { DocumentStatus, Me, Role } from "@/lib/types";

const MB = 1024 * 1024;

describe("validateUpload", () => {
  it("accepts every supported kind, in any letter case", () => {
    for (const name of ["a.pdf", "b.PNG", "c.jpg", "d.JPEG", "e.tif", "f.tiff", "g.csv", "h.xlsx"]) {
      expect(validateUpload({ name, size: 10 })).toBeNull();
    }
  });

  it("refuses other kinds and names without an ending", () => {
    expect(validateUpload({ name: "virus.exe", size: 10 })).toMatch(/can't be uploaded/);
    expect(validateUpload({ name: "old.xls", size: 10 })).toMatch(/can't be uploaded/);
    expect(validateUpload({ name: "README", size: 10 })).toMatch(/can't be uploaded/);
    expect(validateUpload({ name: "invoice.pdf.exe", size: 10 })).toMatch(/can't be uploaded/);
  });

  it("refuses an empty file", () => {
    expect(validateUpload({ name: "a.pdf", size: 0 })).toBe("This file is empty.");
  });

  it("allows exactly the limit and refuses one byte over", () => {
    expect(validateUpload({ name: "a.pdf", size: MAX_UPLOAD_BYTES })).toBeNull();
    expect(validateUpload({ name: "a.pdf", size: MAX_UPLOAD_BYTES + 1 })).toBe(
      "PDFs can be up to 50 MB. This one is 51 MB. Split it or save it smaller and try again.",
    );
  });

  it("uses a smaller limit when given one", () => {
    expect(validateUpload({ name: "a.csv", size: 6 * MB }, 5 * MB)).toBe(
      "Spreadsheets and CSV files can be up to 5 MB. This one is 6 MB. Split it into smaller files and try again.",
    );
  });

  it("offers the same kinds in the file picker", () => {
    expect(ACCEPT_ATTRIBUTE).toBe(".pdf,.png,.jpg,.jpeg,.tif,.tiff,.csv,.xlsx");
  });
});

describe("upload limits by type (PRD U2)", () => {
  const cases = [
    { names: ["a.pdf"], limit: 50 * MB },
    { names: ["a.png", "a.jpg", "a.jpeg", "a.tif", "a.tiff"], limit: 20 * MB },
    { names: ["a.csv", "a.xlsx"], limit: 10 * MB },
  ];

  it("allows each type exactly at its limit and refuses one byte over", () => {
    for (const { names, limit } of cases) {
      for (const name of names) {
        expect(validateUpload({ name, size: limit }), name).toBeNull();
        expect(validateUpload({ name, size: limit + 1 }), name).toMatch(/can be up to/);
      }
    }
  });

  it("reads the ending in any letter case", () => {
    expect(validateUpload({ name: "SCAN.TIFF", size: 20 * MB })).toBeNull();
    expect(validateUpload({ name: "SCAN.TIFF", size: 20 * MB + 1 })).toBe(
      "Photos and scans can be up to 20 MB. This one is 21 MB. Save it at a lower resolution and try again.",
    );
    expect(validateUpload({ name: "Photo.JpG", size: 20 * MB + 1 })).toMatch(/^Photos and scans/);
    expect(validateUpload({ name: "BOOK.XLSX", size: 10 * MB + 1 })).toMatch(/^Spreadsheets/);
    expect(validateUpload({ name: "DEED.PDF", size: 50 * MB })).toBeNull();
    expect(uploadKind("DEED.PDF")).toBe("pdf");
    expect(uploadKind("IMG_01.JPEG")).toBe("image");
    expect(uploadKind("Ledger.Csv")).toBe("sheet");
  });

  it("knows the kind only for accepted endings", () => {
    expect(uploadKind("virus.exe")).toBeNull();
    expect(uploadKind("README")).toBeNull();
    expect(uploadKind("a.constructor")).toBeNull();
    expect(uploadKind("invoice.pdf.exe")).toBeNull();
  });

  it("names the limit, the size and the fix, in full", () => {
    expect(validateUpload({ name: "photo.jpg", size: 34 * MB })).toBe(
      "Photos and scans can be up to 20 MB. This one is 34 MB. Save it at a lower resolution and try again.",
    );
    expect(validateUpload({ name: "statement.pdf", size: 61 * MB })).toBe(
      "PDFs can be up to 50 MB. This one is 61 MB. Split it or save it smaller and try again.",
    );
    expect(validateUpload({ name: "ledger.xlsx", size: 12 * MB })).toBe(
      "Spreadsheets and CSV files can be up to 10 MB. This one is 12 MB. Split it into smaller files and try again.",
    );
    expect(tooLargeMessage("image", 34 * MB, 20 * MB)).toBe(
      "Photos and scans can be up to 20 MB. This one is 34 MB. Save it at a lower resolution and try again.",
    );
    expect(tooLargeMessage("pdf", 61 * MB, 50 * MB)).toBe(
      "PDFs can be up to 50 MB. This one is 61 MB. Split it or save it smaller and try again.",
    );
    expect(tooLargeMessage("sheet", 12 * MB, 10 * MB)).toBe(
      "Spreadsheets and CSV files can be up to 10 MB. This one is 12 MB. Split it into smaller files and try again.",
    );
  });

  it("rounds the size up, so a file just over the limit never shows the limit", () => {
    expect(tooLargeMessage("image", 20 * MB + 1, 20 * MB)).toContain("This one is 21 MB.");
    expect(tooLargeMessage("image", 33 * MB + 1, 20 * MB)).toContain("This one is 34 MB.");
    expect(tooLargeMessage("pdf", 60.01 * MB, 50 * MB)).toContain("This one is 61 MB.");
    expect(tooLargeMessage("sheet", 11.5 * MB, 10 * MB)).toContain("This one is 12 MB.");
    expect(tooLargeMessage("sheet", 12 * MB, 10 * MB)).toContain("This one is 12 MB.");
  });

  it("uses the lower of the customer's limit and the type's limit", () => {
    expect(limitFor("pdf")).toBe(50 * MB);
    expect(limitFor("image")).toBe(20 * MB);
    expect(limitFor("sheet")).toBe(10 * MB);
    expect(limitFor("pdf", 25 * MB)).toBe(25 * MB);
    expect(limitFor("image", 25 * MB)).toBe(20 * MB);
    expect(limitFor("sheet", 25 * MB)).toBe(10 * MB);
    expect(limitFor("image", null)).toBe(20 * MB);

    // A lower customer limit is the one named.
    expect(validateUpload({ name: "a.pdf", size: 25 * MB }, 25 * MB)).toBeNull();
    expect(validateUpload({ name: "a.pdf", size: 25 * MB + 1 }, 25 * MB)).toBe(
      "PDFs can be up to 25 MB. This one is 26 MB. Split it or save it smaller and try again.",
    );
    // A higher customer limit never raises the type's limit.
    expect(validateUpload({ name: "a.png", size: 21 * MB }, 50 * MB)).toBe(
      "Photos and scans can be up to 20 MB. This one is 21 MB. Save it at a lower resolution and try again.",
    );
    expect(validateUpload({ name: "a.csv", size: 10 * MB }, 50 * MB)).toBeNull();
  });

  it("keeps the largest limit as the overall ceiling", () => {
    expect(MAX_UPLOAD_BYTES).toBe(50 * MB);
  });

  it("tells people each type's limit next to the dropzone", () => {
    expect(UPLOAD_LIMITS_HINT).toBe(
      "PDF up to 50 MB. PNG, JPG or TIFF up to 20 MB. CSV or XLSX up to 10 MB.",
    );
  });
});

describe("shouldPoll", () => {
  const docs = (...statuses: DocumentStatus[]) => statuses.map((status) => ({ status }));

  it("polls while something is waiting for the check or being read", () => {
    expect(shouldPoll(docs("ready", "received"))).toBe(true);
    expect(shouldPoll(docs("clean"))).toBe(true);
  });

  it("stops once everything has settled", () => {
    expect(shouldPoll(docs("ready", "needs_reader", "rejected", "failed"))).toBe(false);
    expect(shouldPoll([])).toBe(false);
  });
});

describe("canDelete", () => {
  const me = (role: Role, name = "Asha"): Me => ({
    user: { id: "u1", email: "asha@example.in", display_name: name, role },
    customer: { id: "c1", name: "Sharma Traders" },
  });

  it("lets an admin delete anything", () => {
    expect(canDelete(me("admin"), { uploaded_by_name: "Someone else" })).toBe(true);
    expect(canDelete(me("admin"), { uploaded_by_name: null })).toBe(true);
  });

  it("lets the uploader delete their own", () => {
    expect(canDelete(me("member"), { uploaded_by_name: "Asha" })).toBe(true);
    expect(canDelete(me("reviewer"), { uploaded_by_name: "Asha" })).toBe(true);
  });

  it("hides delete from others", () => {
    expect(canDelete(me("member"), { uploaded_by_name: "Ravi" })).toBe(false);
    expect(canDelete(me("reviewer"), { uploaded_by_name: null })).toBe(false);
  });
});

describe("pageInfo", () => {
  it("describes the first page", () => {
    expect(pageInfo(0, 25, 80)).toEqual({ from: 1, to: 25, hasPrevious: false, hasNext: true });
  });

  it("describes the last, partial page", () => {
    expect(pageInfo(75, 5, 80)).toEqual({ from: 76, to: 80, hasPrevious: true, hasNext: false });
  });

  it("describes an empty list", () => {
    expect(pageInfo(0, 0, 0)).toEqual({ from: 0, to: 0, hasPrevious: false, hasNext: false });
  });
});

describe("offsetAfterDelete", () => {
  it("steps back a page when the last row of a later page goes", () => {
    expect(offsetAfterDelete(25, 1, 25)).toBe(0);
  });

  it("stays when rows are left, or on the first page", () => {
    expect(offsetAfterDelete(25, 3, 25)).toBe(25);
    expect(offsetAfterDelete(0, 1, 25)).toBe(0);
  });
});

describe("uploadStateText", () => {
  it("has words for every state and leaves the percentage to the bar", () => {
    expect(uploadStateText({ kind: "waiting" })).toBe("Waiting to upload");
    expect(uploadStateText({ kind: "uploading", fraction: 0.42 })).toBe("Uploading");
    expect(uploadStateText({ kind: "done", documentId: "d" })).toBe("Uploaded");
    expect(
      uploadStateText({ kind: "duplicate", documentId: "d", existingName: "x.pdf" }),
    ).toBe("Already uploaded");
    expect(uploadStateText({ kind: "error", message: "No." })).toBe("Not uploaded");
  });
});

describe("listView", () => {
  it("loads first, and shows the error only when nothing is loaded yet", () => {
    expect(listView(null, false, "")).toBe("loading");
    expect(listView(null, true, "")).toBe("error");
    expect(listView(4, true, "")).toBe("list");
  });

  it("tells an empty account from a search with no match", () => {
    expect(listView(0, false, "")).toBe("empty");
    expect(listView(0, false, "gst")).toBe("no-match");
    expect(listView(3, false, "gst")).toBe("list");
  });
});

describe("estimateIngestSeconds", () => {
  it("scales up for larger files and multiple files", () => {
    expect(estimateIngestSeconds(1 * MB)).toBeGreaterThan(0);
    expect(estimateIngestSeconds(5 * MB)).toBeGreaterThan(estimateIngestSeconds(1 * MB));
    expect(estimateIngestSeconds(2 * MB, 3)).toBeGreaterThan(estimateIngestSeconds(2 * MB, 1));
  });
});

describe("formatEtaText", () => {
  it("reads in a short, plain-language format", () => {
    expect(formatEtaText(0)).toBe("About 0 seconds");
    expect(formatEtaText(5)).toBe("About 5 seconds");
    expect(formatEtaText(90)).toBe("About 1 minute 30 seconds");
  });
});

describe("formatProgressPercent", () => {
  it("rounds to a clear whole percentage", () => {
    expect(formatProgressPercent(1, 3)).toBe("33%");
    expect(formatProgressPercent(2, 4)).toBe("50%");
    expect(formatProgressPercent(0, 0)).toBe("0%");
  });
});

describe("deletedMessage", () => {
  it("names the document, as plain text", () => {
    expect(deletedMessage("Bill <1>.pdf")).toBe("Deleted Bill <1>.pdf.");
  });
});
