// The operator's Activity and Health tabs (PR 7c): the words for codes and
// counts, and source guards for the tabs and what is never shown.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { OPERATOR_TABS } from "@/components/operator/OperatorConsole";
import {
  ACTION_FILTERS,
  moreFor,
  actionWords,
  byWords,
  countsInWords,
  failureWords,
  NOT_REPORTING_MS,
  queueWords,
  readerWords,
  statusWords,
  targetWords,
  waitWords,
} from "@/components/operator/platform";
import { query } from "@/lib/api";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("activity in words", () => {
  it("says each action plainly, an unknown code safely", () => {
    expect(actionWords("customer.delete_scheduled")).toBe("Scheduled an organisation's deletion");
    expect(actionWords("admin.reset_totp")).toBe("Reset an admin's two-step");
    expect(actionWords("org.purged")).toBe("Organisation deleted for good");
    expect(actionWords("document.upload")).toBe("Uploaded a document"); // the admin screen's words
    expect(actionWords("document.reprocess")).toBe("Document reprocess");
    expect(actionWords("constructor")).toBe("Constructor"); // not a key of the maps' prototype
    expect(actionWords("__proto__")).toBe("Proto");
    expect(actionWords("<b>x</b>")).toBe("<b>x</b>"); // plain text: React escapes it
    expect(actionWords("")).toBe("Unknown");
  });

  it("names who acted as a kind only, and the target's type", () => {
    expect(["user", "operator", "system"].map(byWords)).toEqual(["A user", "An operator", "The system"]);
    expect(byWords("toString")).toBe("ToString");
    expect(targetWords("customer")).toBe("Organisation");
    expect(targetWords("document")).toBe("Document");
    expect(targetWords(null)).toBe("None");
  });

  it("filters by actions the server accepts, and leaves an empty filter out", () => {
    for (const filter of ACTION_FILTERS) expect(filter.value).toMatch(/^([a-z_.]{1,64})?$/);
    expect(ACTION_FILTERS[0]).toEqual({ value: "", label: "All actions" });
    expect(query({ before: "MjAyNi0x", org: "", action: "customer." })).toBe("?before=MjAyNi0x&action=customer.");
  });
});

describe("Load more", () => {
  it("forgets a Load more and its failure once the filter changes", () => {
    const failed = { key: "o-1|customer.|0", busy: false, failed: "Could not reach Kaagaz." };
    expect(moreFor(failed, "o-1|customer.|0")).toBe(failed);
    expect(moreFor(failed, "|customer.|0")).toEqual({ key: "|customer.|0", busy: false, failed: null });
    expect(moreFor({ ...failed, busy: true }, "o-2||0").busy).toBe(false);
  });
});

describe("health in words", () => {
  it("lists counts by status in the app's words, known ones first, none at 0", () => {
    const counts = { failed: 2, ready: 120000, received: 0, mystery: 1 };
    expect(countsInWords(counts, statusWords)).toEqual(["1,20,000 Ready", "2 Failed", "1 Mystery"]);
    expect(countsInWords({}, statusWords)).toEqual([]);
  });

  it("names each failure reason, an unknown one safely", () => {
    expect(failureWords("ocr_failed")).toBe("Scan reader failed on a page");
    expect(failureWords("unknown")).toBe("No reason recorded");
    expect(failureWords("corrupt_file")).toBe("Corrupt file");
    const failed = countsInWords({ unknown: 1, ocr_failed: 3 }, failureWords);
    expect(failed).toEqual(["3 Scan reader failed on a page", "1 No reason recorded"]);
  });

  it("says how long the oldest document has waited", () => {
    expect(waitWords(null)).toBe("Nothing waiting");
    expect(waitWords(30)).toBe("under a minute");
    expect(waitWords(120)).toBe("2 minutes");
    expect(waitWords(3600)).toBe("1 hour");
    expect(waitWords(7200)).toBe("2 hours");
    expect(waitWords(3 * 86400)).toBe("3 days");
  });
});

describe("activity and health source guards", () => {
  it("uses the admin screen's tablist pattern for three tabs", () => {
    expect(OPERATOR_TABS.map((tab) => tab.label)).toEqual(["Organisations", "Activity", "Health"]);
    const console = read("components/operator/OperatorConsole.tsx");
    for (const part of ['role="tablist"', 'role="tab"', 'role="tabpanel"', "aria-controls", "tabAfterKey(", "tabIndex="]) {
      expect(console, part).toContain(part);
    }
    expect(read("app/operator/(console)/page.tsx")).toContain("<OperatorConsole />");
  });

  it("shows the agreed words and never details, a target id or a person", () => {
    const activity = read("components/operator/ActivityView.tsx");
    for (const label of ["Load more", "result.next", 'htmlFor="activity-org"', 'htmlFor="activity-action"', 'role="status"']) {
      expect(activity, label).toContain(label);
    }
    expect(activity).not.toMatch(/\.(details|target_id|actor\w*|email)/);
    const health = read("components/operator/HealthView.tsx");
    for (const label of ["Documents by status", "Failed, by reason", "Oldest waiting", "Deleted for good", 'role="status"']) {
      expect(health, label).toContain(label);
    }
    expect(health).not.toMatch(/\.(display_name|email)/);
  });
});

describe("the reader and the queues (PRD 5 U9)", () => {
  const now = Date.parse("2026-10-09T10:00:00Z");
  const seen = (msAgo: number) => new Date(now - msAgo).toISOString();

  it("says each worker's reader status, failing as slow", () => {
    expect(readerWords({ state: "working", seen_at: seen(1000) }, now)).toBe("Working");
    expect(readerWords({ state: "failing", seen_at: seen(1000) }, now)).toBe("Slow");
    expect(readerWords({ state: "down", seen_at: seen(1000) }, now)).toBe("Down");
    expect(readerWords({ state: "key_problem", seen_at: seen(1000) }, now)).toBe("Key problem");
  });

  it("says not reporting once a worker has been quiet for 2 minutes", () => {
    expect(NOT_REPORTING_MS).toBe(120000);
    expect(readerWords({ state: "working", seen_at: seen(NOT_REPORTING_MS - 1) }, now)).toBe("Working");
    expect(readerWords({ state: "working", seen_at: seen(NOT_REPORTING_MS) }, now)).toBe("Not reporting");
    expect(readerWords({ state: "working", seen_at: "not a time" }, now)).toBe("Not reporting");
  });

  it("gives the jobs waiting in both lanes, or not answering", () => {
    expect(queueWords({ express: 2, main: 1450 })).toEqual([
      { label: "Waiting in the express lane", value: "2" },
      { label: "Waiting in the main lane", value: "1,450" },
    ]);
    expect(queueWords(null).map((lane) => lane.value)).toEqual(["Not answering", "Not answering"]);
  });
});
