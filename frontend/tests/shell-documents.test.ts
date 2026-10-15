import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ACCEPT_ATTRIBUTE,
  FALLBACK_UPLOAD_LIMITS,
  MB_PER_PDF_PAGE,
  SECONDS_PER_PAGE,
  deletedMessage,
  estimateIngestSeconds,
  estimatePages,
  estimateText,
  formatEtaText,
  formatProgressPercent,
  limitFor,
  listView,
  offsetAfterDelete,
  pageInfo,
  sessionChangedMessage,
  shouldPoll,
  tooLargeMessage,
  uploadKind,
  uploadLimitsHint,
  uploadStateText,
  validateUpload,
} from "@/components/documents/logic";
import {
  dateChipText,
  documentNameProblem,
  downloadHref,
  findTagByName,
  folderNameProblem,
  folderTrail,
  libraryErrorText,
  menuEntries,
  noMatchTitle,
  tagNameProblem,
  tagsChipText,
  typeChipText,
} from "@/components/documents/library";
import { ApiError, uploadDocument, uploadVersion } from "@/lib/api";
import type { DocumentStatus, UploadLimits } from "@/lib/types";

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
    expect(validateUpload({ name: "a.pdf", size: 50 * MB })).toBeNull();
    expect(validateUpload({ name: "a.pdf", size: 50 * MB + 1 })).toBe(
      "PDFs can be up to 50 MB. This one is 51 MB. Split it or save it smaller and try again.",
    );
  });

  it("uses the server's limits when given them", () => {
    const limits: UploadLimits = { ...FALLBACK_UPLOAD_LIMITS, sheet_bytes: 5 * MB };
    expect(validateUpload({ name: "a.csv", size: 6 * MB }, limits)).toBe(
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

  // GET /upload-limits, with a customer limit of 25 MB already applied by the server.
  const customer: UploadLimits = {
    pdf_bytes: 25 * MB,
    image_bytes: 20 * MB,
    sheet_bytes: 10 * MB,
    max_pages: 40,
    readers: [],
  };

  it("uses the platform's limits until the server's arrive", () => {
    expect(FALLBACK_UPLOAD_LIMITS).toEqual({
      pdf_bytes: 50 * MB,
      image_bytes: 20 * MB,
      sheet_bytes: 10 * MB,
      max_pages: 100,
      readers: [], // no "Scan with" until the server says which readers it has
    });
    expect(limitFor("pdf")).toBe(50 * MB);
    expect(limitFor("image")).toBe(20 * MB);
    expect(limitFor("sheet")).toBe(10 * MB);
  });

  it("uses the server's limits, and names them in the refusal", () => {
    expect(limitFor("pdf", customer)).toBe(25 * MB);
    expect(limitFor("image", customer)).toBe(20 * MB);
    expect(limitFor("sheet", customer)).toBe(10 * MB);

    expect(validateUpload({ name: "a.pdf", size: 25 * MB }, customer)).toBeNull();
    expect(validateUpload({ name: "a.pdf", size: 25 * MB + 1 }, customer)).toBe(
      "PDFs can be up to 25 MB. This one is 26 MB. Split it or save it smaller and try again.",
    );
    expect(validateUpload({ name: "a.png", size: 21 * MB }, customer)).toBe(
      "Photos and scans can be up to 20 MB. This one is 21 MB. Save it at a lower resolution and try again.",
    );
    expect(validateUpload({ name: "a.csv", size: 10 * MB }, customer)).toBeNull();
  });

  it("tells people each type's limit next to the dropzone", () => {
    expect(uploadLimitsHint()).toBe(
      "PDF up to 50 MB. PNG, JPG or TIFF up to 20 MB. CSV or XLSX up to 10 MB.",
    );
    expect(uploadLimitsHint(customer)).toBe(
      "PDF up to 25 MB. PNG, JPG or TIFF up to 20 MB. CSV or XLSX up to 10 MB.",
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

  it("keeps polling while a document is read page by page (PRD 2, X3)", () => {
    const reading = { status: "clean" as const, progress: { step: "reading", done: 3, total: 40 } };
    expect(shouldPoll([reading])).toBe(true);
  });

  it("keeps polling while a newer version is still being read, so its line moves too", () => {
    const withNewer = (status: DocumentStatus) => ({
      status: "ready" as const,
      newer_version: { status },
    });
    expect(shouldPoll([withNewer("received")])).toBe(true);
    expect(shouldPoll([withNewer("clean")])).toBe(true);
    expect(shouldPoll([withNewer("failed")])).toBe(false);
    expect(shouldPoll([{ status: "ready", newer_version: null }])).toBe(false);
  });
});

describe("menuEntries", () => {
  const TRASHED = "2026-09-20T10:00:00Z";
  const doc = (
    changes: { deleted_at?: string | null; can_delete?: boolean; status?: DocumentStatus } = {},
  ) => ({ id: "d-1", status: "ready" as DocumentStatus, deleted_at: null, can_delete: false, ...changes });
  const words = (entries: ReturnType<typeof menuEntries>) =>
    entries.map((entry) => (entry.kind === "download" ? "Download original" : entry.words));

  it("offers the everyday actions to anyone, and Move to Trash only with can_delete", () => {
    expect(words(menuEntries(doc(), true))).toEqual([
      "Rename",
      "Move to folder",
      "Tags",
      "Upload new version",
      "Download original",
    ]);
    expect(words(menuEntries(doc({ can_delete: true }), false)).at(-1)).toBe("Move to Trash");
  });

  it("offers nothing that changes a document in Trash, only Restore and Delete forever", () => {
    expect(words(menuEntries(doc({ deleted_at: TRASHED, can_delete: true }), true))).toEqual([
      "Restore",
      "Delete forever",
    ]);
  });

  it("offers Restore only with can_delete, and Delete forever only in an admin session", () => {
    expect(words(menuEntries(doc({ deleted_at: TRASHED, can_delete: true }), false))).toEqual(["Restore"]);
    expect(words(menuEntries(doc({ deleted_at: TRASHED }), true))).toEqual(["Delete forever"]);
    expect(words(menuEntries(doc({ deleted_at: TRASHED }), false))).toEqual([]);
  });

  it("leaves Download original out when the server would refuse it", () => {
    expect(words(menuEntries(doc({ status: "received" }), false))).not.toContain(
      "Download original",
    );
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

// Critical #5: once the server says another account has signed in here, the
// files still waiting are not sent, and say why in the server's words.
describe("sessionChangedMessage", () => {
  it("gives the server's words for session_changed only", () => {
    const said = "Not uploaded. A different account has signed in on this browser since you chose this file.";
    expect(sessionChangedMessage(new ApiError(409, "session_changed", said))).toBe(said);
    expect(sessionChangedMessage(new ApiError(409, "conflict", "That already exists."))).toBeNull();
    expect(sessionChangedMessage(new ApiError(0, "network", "Could not reach the server."))).toBeNull();
    expect(sessionChangedMessage(new TypeError("Failed to fetch"))).toBeNull();
  });
});

describe("uploadStateText", () => {
  it("has words for every state and leaves the percentage to the bar", () => {
    expect(uploadStateText({ kind: "waiting" })).toBe("Waiting to upload");
    expect(uploadStateText({ kind: "sending", fraction: 0.42 })).toBe("Sending");
    expect(uploadStateText({ kind: "done", documentId: "d" })).toBe("Uploaded");
    expect(
      uploadStateText({ kind: "duplicate", documentId: "d", existingName: "x.pdf", inTrash: false }),
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
  // PRD 2 X4 adds the file type: the estimate is by pages now, not by MB.
  it("scales up for larger files and multiple files", () => {
    expect(estimateIngestSeconds("pdf", 1 * MB)).toBeGreaterThan(0);
    expect(estimateIngestSeconds("pdf", 5 * MB)).toBeGreaterThan(estimateIngestSeconds("pdf", 1 * MB));
    expect(estimateIngestSeconds("pdf", 2 * MB, 3)).toBeGreaterThan(estimateIngestSeconds("pdf", 2 * MB, 1));
  });

  it("is the estimated pages times the seconds per page for that file type (PRD 2, X4)", () => {
    expect(MB_PER_PDF_PAGE).toBe(0.4);
    expect(estimateIngestSeconds("pdf", 40 * MB)).toBe(Math.round(100 * SECONDS_PER_PAGE.pdf));
    const onePage = (kind: "image" | "sheet") => Math.max(4, Math.round(SECONDS_PER_PAGE[kind]));
    expect(estimateIngestSeconds("image", 15 * MB)).toBe(onePage("image"));
    expect(estimateIngestSeconds("image", 1 * MB)).toBe(onePage("image"));
    expect(estimateIngestSeconds("sheet", 9 * MB)).toBe(onePage("sheet"));
  });

  it("uses a PDF's real page count when it is known, else MB divided by 0.4", () => {
    const seconds = (pages: number) => Math.max(4, Math.round(pages * SECONDS_PER_PAGE.pdf));
    expect(estimateIngestSeconds("pdf", 40 * MB, 1, 30)).toBe(seconds(30));
    expect(estimateIngestSeconds("pdf", 40 * MB, 1, null)).toBe(seconds(100));
    expect(estimateIngestSeconds("pdf", 40 * MB)).toBe(seconds(100));
  });
});

describe("estimatePages (PRD 2, X4)", () => {
  it("counts a PDF as its MB divided by 0.4, rounded up", () => {
    expect(estimatePages("pdf", 0.4 * MB)).toBe(1);
    expect(estimatePages("pdf", 0.41 * MB)).toBe(2);
    expect(estimatePages("pdf", Math.floor(0.4 * MB))).toBe(1);
    expect(estimatePages("pdf", Math.floor(0.4 * MB) + 1)).toBe(2);
    expect(estimatePages("pdf", 4 * MB)).toBe(10);
  });

  it("gives at least 1 page, even for 0 bytes", () => {
    expect(estimatePages("pdf", 0)).toBe(1);
    expect(estimatePages("image", 0)).toBe(1);
    expect(estimatePages("sheet", 0)).toBe(1);
  });

  it("counts a photo or a sheet as one page, whatever its size", () => {
    expect(estimatePages("image", 15 * MB)).toBe(1);
    expect(estimatePages("sheet", 9 * MB)).toBe(1);
  });

  it("uses a PDF's real page count once the upload flow has counted it (PROVISIONAL)", () => {
    expect(estimatePages("pdf", 40 * MB, 3)).toBe(3);
    expect(estimatePages("pdf", 0.1 * MB, 250)).toBe(250);
  });

  it("still rounds the count up, to at least 1", () => {
    expect(estimatePages("pdf", 40 * MB, 2.5)).toBe(3);
    expect(estimatePages("pdf", 40 * MB, 0)).toBe(1);
  });

  it("falls back to MB divided by 0.4 when the count is not known", () => {
    expect(estimatePages("pdf", 0.41 * MB, null)).toBe(2);
    expect(estimatePages("pdf", 4 * MB, null)).toBe(10);
    expect(estimatePages("pdf", 0, null)).toBe(1);
  });

  it("leaves a photo or a sheet at one page, count or not", () => {
    expect(estimatePages("image", 15 * MB, 7)).toBe(1);
    expect(estimatePages("sheet", 9 * MB, 7)).toBe(1);
  });
});

describe("estimateText (PRD 2, X4)", () => {
  it("says up to N minutes for a PDF, rounded up, and 1 minute in the singular", () => {
    expect(estimateText("pdf", 0)).toBe("Up to 1 minute");
    expect(estimateText("pdf", 4)).toBe("Up to 1 minute");
    expect(estimateText("pdf", 60)).toBe("Up to 1 minute");
    expect(estimateText("pdf", 61)).toBe("Up to 2 minutes");
    expect(estimateText("pdf", 600)).toBe("Up to 10 minutes");
  });

  it("keeps the About wording for photos and sheets", () => {
    expect(estimateText("image", 5)).toBe("About 5 seconds");
    expect(estimateText("sheet", 90)).toBe("About 1 minute 30 seconds");
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
  it("names the document, as plain text, and says it went to Trash (PRD 1, F5)", () => {
    expect(deletedMessage("Bill <1>.pdf")).toBe("Moved Bill <1>.pdf to Trash.");
  });
});

// --- WP1.5: folders, tags, filters and downloads ----------------------------------

describe("listView with filters", () => {
  it("shows no match, not the first-upload screen, when filters leave nothing", () => {
    expect(listView(0, false, "", true)).toBe("no-match");
    expect(listView(0, false, "", false)).toBe("empty");
    expect(listView(3, false, "", true)).toBe("list");
  });
});

describe("uploadDocument into a folder", () => {
  const FOLDER = "0b4f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";

  // Stands in for the browser's XMLHttpRequest and keeps what was sent.
  class FakeXhr {
    static sent: FakeXhr[] = [];
    method = "";
    url = "";
    body: unknown = null;
    status = 0;
    responseText = "";
    withCredentials = false;
    headers: Record<string, string> = {};
    upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } =
      { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    send(body: unknown) {
      this.body = body;
      FakeXhr.sent.push(this);
    }
    reply(status: number, body: unknown) {
      this.status = status;
      this.responseText = JSON.stringify(body);
      this.onload?.();
    }
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    FakeXhr.sent = [];
  });

  function lastForm(): FormData {
    const xhr = FakeXhr.sent.at(-1);
    if (!xhr || !(xhr.body instanceof FormData)) throw new Error("nothing was uploaded");
    return xhr.body;
  }

  it("sends the folder being viewed as the form field folder_id, with the file", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const file = new File(["%PDF-1.7"], "bill.pdf", { type: "application/pdf" });
    const done = uploadDocument(file, "u1", () => {}, FOLDER);
    const form = lastForm();
    expect(form.get("folder_id")).toBe(FOLDER);
    expect(form.get("file")).toBeInstanceOf(File);
    expect(FakeXhr.sent[0]?.url).toBe("/api/documents");
    FakeXhr.sent[0]?.reply(201, { document: { id: "d1" }, duplicate: false });
    await expect(done).resolves.toMatchObject({ duplicate: false });
  });

  it("sends no folder_id when uploading at the top level", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const done = uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {});
    expect(lastForm().has("folder_id")).toBe(false);
    FakeXhr.sent[0]?.reply(201, { document: { id: "d2" }, duplicate: false });
    await done;
  });

  it("sends a new version to /documents/{id}/versions, with progress, and no folder", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const fractions: number[] = [];
    const done = uploadVersion("d1", new File(["%PDF-1.7"], "bill v2.pdf"), "u1", (f) => fractions.push(f));
    const xhr = FakeXhr.sent[0];
    expect(xhr?.method).toBe("POST");
    expect(xhr?.url).toBe("/api/documents/d1/versions");
    expect(xhr?.headers["X-Requested-With"]).toBe("kaagaz");
    expect(lastForm().get("file")).toBeInstanceOf(File);
    expect(lastForm().has("folder_id")).toBe(false);
    xhr?.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 });
    expect(fractions).toEqual([0.25]);
    xhr?.reply(201, { document: { id: "d9", version_no: 2 }, duplicate: false, in_trash: false });
    await expect(done).resolves.toMatchObject({ document: { id: "d9" }, in_trash: false });
  });

  // Critical #5: the server refuses the file (409 session_changed) when
  // someone else has signed in on this browser since it was chosen.
  it("sends who was signed in when the file was chosen, on both routes", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    void uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {}, FOLDER);
    expect(lastForm().get("user_id")).toBe("u1");
    void uploadVersion("d1", new File(["%PDF-1.7"], "bill v2.pdf"), "u1", () => {});
    expect(lastForm().get("user_id")).toBe("u1");
  });

  // Model choice MS4, FINAL DECISIONS 6: read_model, never ocr_choice (accepted and ignored).
  // Changed on purpose (features 25-27, 15 Oct 2026): the reading chosen is sent whole
  // (read_mode, read_model, read_agents once per reader), not a model alone.
  it("sends the model chosen as read_model on both routes, and nothing for the default", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const model = "anthropic/claude-sonnet-5.5";
    void uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {}, undefined, { read_model: model });
    expect(lastForm().get("read_model")).toBe(model);
    expect(lastForm().has("ocr_choice")).toBe(false);
    void uploadVersion("d1", new File(["%PDF-1.7"], "bill v2.pdf"), "u1", () => {}, { read_model: model });
    expect(lastForm().get("read_model")).toBe(model);
    expect(lastForm().has("ocr_choice")).toBe(false);
    void uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {}, FOLDER, undefined);
    expect(lastForm().has("read_model")).toBe(false);
    expect(lastForm().has("read_mode")).toBe(false);
    void uploadVersion("d1", new File(["%PDF-1.7"], "bill v2.pdf"), "u1", () => {});
    expect(lastForm().has("read_model")).toBe(false);
  });

  it("sends Dynamic and more than one reader as read_mode with read_agents once each", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    void uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {}, undefined, { read_mode: "dynamic" });
    expect(lastForm().get("read_mode")).toBe("dynamic");
    const reading = { read_mode: "ensemble" as const, read_agents: ["google/a", "google/b"] };
    void uploadVersion("d1", new File(["%PDF-1.7"], "bill v2.pdf"), "u1", () => {}, reading);
    expect(lastForm().get("read_mode")).toBe("ensemble");
    expect(lastForm().getAll("read_agents")).toEqual(["google/a", "google/b"]);
    expect(lastForm().has("read_model")).toBe(false);
  });

  it("gives the server's words when someone else has signed in since", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const done = uploadDocument(new File(["a,b"], "rates.csv"), "u1", () => {});
    const said = "Someone else has signed in on this browser since you chose this file.";
    FakeXhr.sent[0]?.reply(409, { error: { code: "session_changed", message: said } });
    await expect(done).rejects.toMatchObject({ status: 409, code: "session_changed", message: said });
  });
});

describe("downloadHref", () => {
  it("downloads the original through the API, never a storage link", () => {
    expect(downloadHref({ id: "d-1", status: "ready" })).toBe("/api/documents/d-1/file");
    expect(downloadHref({ id: "d-1", status: "needs_reader" })).toBe("/api/documents/d-1/file");
    expect(downloadHref({ id: "d-1", status: "clean" })).toBe("/api/documents/d-1/file");
  });

  it("offers nothing the server would refuse: not yet checked, rejected or failed", () => {
    for (const status of ["received", "rejected", "failed"] as const) {
      expect(downloadHref({ id: "d-1", status })).toBeNull();
    }
  });
});

describe("folderTrail", () => {
  const suppliers = { id: "s", name: "Suppliers" };
  const year = { id: "y", name: "2026" };

  it("lists the folders from the top down to the document's own folder", () => {
    expect(folderTrail({ ...year, path: [suppliers, year] })).toEqual([suppliers, year]);
  });

  it("adds the folder itself when the path stops above it", () => {
    expect(folderTrail({ ...year, path: [suppliers] })).toEqual([suppliers, year]);
    expect(folderTrail({ ...suppliers, path: [] })).toEqual([suppliers]);
  });
});

describe("names for folders, tags and documents", () => {
  it("asks for a folder name of 1 to 100 characters", () => {
    expect(folderNameProblem("   ")).toBe("Enter a name for the folder.");
    expect(folderNameProblem("x".repeat(101))).toBe("Use at most 100 characters.");
    expect(folderNameProblem(" Suppliers ")).toBeNull();
    expect(folderNameProblem("x".repeat(100))).toBeNull();
  });

  it("asks for a tag name of 1 to 40 characters", () => {
    expect(tagNameProblem("")).toBe("Enter a name for the tag.");
    expect(tagNameProblem("x".repeat(41))).toBe("Use at most 40 characters.");
    expect(tagNameProblem("GST")).toBeNull();
  });

  it("asks for a document name of 1 to 255 characters, slashes allowed", () => {
    expect(documentNameProblem(" ")).toBe("Enter a name for the document.");
    expect(documentNameProblem("x".repeat(256))).toBe("Use at most 255 characters.");
    expect(documentNameProblem("GST 2026/27.pdf")).toBeNull();
  });

  it("finds an existing tag whatever the letter case or spaces", () => {
    const tags = [
      { id: "t1", name: "GST" },
      { id: "t2", name: "Paid" },
    ];
    expect(findTagByName(tags, " gst ")).toEqual({ id: "t1", name: "GST" });
    expect(findTagByName(tags, "unpaid")).toBeNull();
  });
});

describe("libraryErrorText", () => {
  const taken = new ApiError(409, "conflict", "That already exists.");

  it("says which name is taken", () => {
    expect(libraryErrorText(taken, "folder")).toBe(
      "A folder with this name is already here. Choose another name.",
    );
    expect(libraryErrorText(taken, "tag")).toBe("A tag with this name already exists.");
  });

  it("passes the server's own words through for the folder rules", () => {
    const notEmpty = new ApiError(
      409,
      "folder_not_empty",
      "This folder isn't empty. Move or delete what is inside it first.",
    );
    expect(libraryErrorText(notEmpty, "folder")).toBe(notEmpty.message);
  });

  it("asks to check the connection when the server was not reached", () => {
    expect(libraryErrorText(new TypeError("Failed to fetch"), "document")).toBe(
      "Check your internet connection, then try again.",
    );
  });
});

describe("noMatchTitle", () => {
  it("names the search words, the filters, or both", () => {
    expect(noMatchTitle("gst", false)).toBe("No document names contain “gst”.");
    expect(noMatchTitle("", true)).toBe("No documents match these filters.");
    expect(noMatchTitle("gst", true)).toBe("No document names contain “gst” with these filters.");
    expect(noMatchTitle("", true, true)).toBe("Nothing needs attention.");
    expect(noMatchTitle("gst", true, true)).toBe("No document names contain “gst” with these filters.");
  });
});

describe("filter chip words", () => {
  it("names the tags chosen, or just says Tags", () => {
    expect(tagsChipText([])).toBe("Tags");
    expect(tagsChipText(["GST"])).toBe("Tag: GST");
    expect(tagsChipText(["GST", "Paid", "2026"])).toBe("Tags: GST and 2 more");
  });

  it("names the type chosen, or just says Type", () => {
    expect(typeChipText(null)).toBe("Type");
    expect(typeChipText("pdf")).toBe("Type: PDF");
  });

  it("names the days chosen, in India dates, or just says Date uploaded", () => {
    expect(dateChipText(null, null)).toBe("Date uploaded");
    expect(dateChipText("2026-09-01", "2026-09-30")).toBe("Uploaded 1 Sept 2026 to 30 Sept 2026");
    expect(dateChipText("2026-09-01", null)).toBe("Uploaded from 1 Sept 2026");
    expect(dateChipText(null, "2026-09-30")).toBe("Uploaded until 30 Sept 2026");
  });
});
