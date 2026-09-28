import { describe, expect, it } from "vitest";
import {
  ACCEPT_ATTRIBUTE,
  MAX_UPLOAD_BYTES,
  canDelete,
  deletedMessage,
  estimateIngestSeconds,
  formatEtaText,
  formatProgressPercent,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
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
      "This file is larger than 50 MB.",
    );
  });

  it("uses a smaller limit when given one", () => {
    expect(validateUpload({ name: "a.csv", size: 6 * MB }, 5 * MB)).toBe(
      "This file is larger than 5 MB.",
    );
  });

  it("offers the same kinds in the file picker", () => {
    expect(ACCEPT_ATTRIBUTE).toBe(".pdf,.png,.jpg,.jpeg,.tif,.tiff,.csv,.xlsx");
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
