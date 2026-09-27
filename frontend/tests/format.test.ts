import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatNumber,
  formatRelative,
  statusChip,
  typeLabel,
} from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";

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
    expect(typeLabel("zip")).toBe("Spreadsheet");
    expect(typeLabel("text")).toBe("CSV");
  });
});

describe("statusChip", () => {
  const all: DocumentStatus[] = ["received", "clean", "ready", "needs_reader", "rejected", "failed"];

  it("always has words, so colour is never the only signal", () => {
    for (const status of all) expect(statusChip(status).text.length).toBeGreaterThan(0);
  });

  it("maps statuses to the shared badge classes", () => {
    expect(statusChip("ready")).toEqual({
      text: "Ready",
      tone: "verified",
      className: "badge badge-verified",
    });
    expect(statusChip("failed").className).toBe("badge badge-danger");
    expect(statusChip("rejected").className).toBe("badge badge-danger");
    expect(statusChip("needs_reader").className).toBe("badge badge-review");
    expect(statusChip("received").className).toBe("badge badge-neutral");
  });
});
