import { describe, expect, it } from "vitest";
import {
  docTypeLabel,
  formatBytes,
  formatCost,
  formatDate,
  formatDateTime,
  formatNumber,
  formatRelative,
  formatUsd,
  statusChip,
  typeLabel,
} from "@/lib/format";
import type { DetectedType, DocType, DocumentStatus } from "@/lib/types";

describe("formatBytes", () => {
  it("shows bytes as whole numbers", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
  });

  it("uses base 1024 with one decimal, dropping .0", () => {
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(50 * 1024 * 1024)).toBe("50 MB");
    expect(formatBytes(2.25 * 1024 * 1024 * 1024)).toBe("2.3 GB");
  });

  it("gives nothing for a value that is not a size", () => {
    expect(formatBytes(-1)).toBe("");
    expect(formatBytes(Number.NaN)).toBe("");
  });
});

describe("dates in India time", () => {
  // 09:00 UTC is 14:30 in India.
  const iso = "2026-09-27T09:00:00Z";

  it("formats a date and time in en-IN", () => {
    expect(formatDateTime(iso)).toBe("27 Sept 2026, 2:30 pm");
  });

  it("uses India's date even when UTC is still the day before", () => {
    expect(formatDate("2026-09-26T20:00:00Z")).toBe("27 Sept 2026");
  });

  it("gives nothing for an unreadable date", () => {
    expect(formatDateTime("not a date")).toBe("");
    expect(formatRelative("not a date", Date.parse(iso))).toBe("");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-09-27T12:00:00Z");
  const ago = (seconds: number) => new Date(now - seconds * 1000).toISOString();

  it("says now for the last few seconds", () => {
    expect(formatRelative(ago(10), now)).toBe("now");
  });

  it("counts minutes, hours and days", () => {
    expect(formatRelative(ago(5 * 60), now)).toBe("5 minutes ago");
    expect(formatRelative(ago(3 * 3600), now)).toBe("3 hours ago");
    expect(formatRelative(ago(24 * 3600), now)).toBe("yesterday");
    expect(formatRelative(ago(3 * 86400), now)).toBe("3 days ago");
  });

  it("gives the date after a week", () => {
    expect(formatRelative(ago(10 * 86400), now)).toBe("17 Sept 2026");
  });
});

describe("formatNumber", () => {
  it("groups digits the Indian way", () => {
    expect(formatNumber(120000)).toBe("1,20,000");
    expect(formatNumber(12345678)).toBe("1,23,45,678");
  });
});

describe("typeLabel", () => {
  it("names each detected type in words", () => {
    expect(typeLabel("pdf")).toBe("PDF");
    // Changed on purpose (#522): a ready spreadsheet is xlsx, and zip and text are only the
    // sniffed types, until the worker has told the file's kind.
    expect(typeLabel("xlsx")).toBe("Spreadsheet");
    expect(typeLabel("csv")).toBe("CSV");
    expect(typeLabel("zip")).toBe("ZIP file");
    expect(typeLabel("text")).toBe("Text file");
  });

  it("names every kind the worker can tell (#522)", () => {
    const kinds: [DetectedType, string][] = [
      ["xlsx", "Spreadsheet"],
      ["xls", "Spreadsheet (XLS)"],
      ["csv", "CSV"],
      ["docx", "Word document"],
      ["dotx", "Word template"],
      ["doc", "Word document (DOC)"],
      ["pptx", "PowerPoint"],
      ["ppt", "PowerPoint (PPT)"],
      ["txt", "Text"],
      ["md", "Markdown"],
      ["json", "JSON"],
      ["html", "Web page (HTML)"],
      ["heic", "Image (HEIC)"],
      ["zip", "ZIP file"],
    ];
    for (const [kind, words] of kinds) {
      expect(typeLabel(kind), kind).toBe(words);
    }
  });
});

describe("docTypeLabel (PRD 2, D6)", () => {
  it("says Unknown when there is no type, as with the model off", () => {
    expect(docTypeLabel(null)).toBe("Unknown");
  });

  it("names each type the document card can give, in words", () => {
    const all: DocType[] = [
      "invoice",
      "receipt",
      "bank_statement",
      "land_record",
      "contract",
      "register",
      "form",
      "letter",
      "other",
    ];
    expect(all.map(docTypeLabel)).toEqual([
      "Invoice",
      "Receipt",
      "Bank statement",
      "Land record",
      "Contract",
      "Register",
      "Form",
      "Letter",
      "Other",
    ]);
  });
});

describe("statusChip", () => {
  const all: DocumentStatus[] = ["received", "clean", "ready", "needs_reader", "rejected", "failed"];

  it("always has words, so colour is never the only signal", () => {
    for (const status of all) expect(statusChip(status).text.length).toBeGreaterThan(0);
  });

  it("uses the status chip with a tone, never the label badge classes", () => {
    expect(statusChip("ready")).toEqual({ text: "Ready", tone: "ready", className: "status-chip" });
    // Changed on purpose (UI rebuild Step 1, 20 Oct 2026): the words follow the six
    // states in lib/status.ts, so a file being checked is Reading and a refused or
    // unreadable one is Failed.
    expect(statusChip("received")).toEqual({ text: "Reading", tone: "working", className: "status-chip" });
    expect(statusChip("clean")).toEqual({ text: "Reading", tone: "working", className: "status-chip" });
    expect(statusChip("needs_reader")).toEqual({ text: "Failed", tone: "danger", className: "status-chip" });
    expect(statusChip("rejected")).toEqual({ text: "Failed", tone: "danger", className: "status-chip" });
    expect(statusChip("failed")).toEqual({ text: "Failed", tone: "danger", className: "status-chip" });
    for (const status of all) expect(statusChip(status).className).not.toMatch(/badge/);
  });
});

// Model choice: AI spend is in US dollars, never shown as $0.00 when something was spent.
describe("formatUsd and formatCost", () => {
  it("shows US dollars with cents and US grouping, whatever the page's locale", () => {
    expect(formatUsd(12.4)).toBe("$12.40");
    expect(formatUsd(50)).toBe("$50.00");
    expect(formatUsd(123456.789)).toBe("$123,456.79");
    expect(formatUsd(0)).toBe("$0.00");
  });

  it("says < $0.01 for more than nothing but under a cent, and a cent from a cent", () => {
    expect(formatUsd(0.004)).toBe("< $0.01");
    expect(formatUsd(0.009999)).toBe("< $0.01");
    expect(formatUsd(0.000001)).toBe("< $0.01");
    expect(formatUsd(0.01)).toBe("$0.01");
  });

  it("marks an estimate in words, not only with the sign", () => {
    expect(formatUsd(12.4, true)).toBe("≈ $12.40 (estimated)");
    expect(formatUsd(0.002, true)).toBe("≈ < $0.01 (estimated)");
  });

  it("says Not known when a cost is missing, and marks an estimated one", () => {
    expect(formatCost({ cost_usd: null, cost_estimated: false })).toBe("Not known");
    expect(formatCost({ cost_usd: null, cost_estimated: true })).toBe("Not known");
    expect(formatCost({ cost_usd: 3.5, cost_estimated: false })).toBe("$3.50");
    expect(formatCost({ cost_usd: 3.5, cost_estimated: true })).toBe("≈ $3.50 (estimated)");
    // R9: a sum of the known costs only says so.
    expect(formatCost({ cost_usd: 3.5, cost_estimated: false, cost_part_unknown: true })).toBe(
      "$3.50 (part not known)",
    );
    expect(formatCost({ cost_usd: null, cost_estimated: false, cost_part_unknown: true })).toBe("Not known");
  });
});
