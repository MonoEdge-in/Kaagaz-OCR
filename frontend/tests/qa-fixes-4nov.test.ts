// Product QA fixes (4 Nov 2026), the frontend's half: an admin told to set up two-step mid-session
// is sent to sign in (C1); the Health tab says when Mistral has stopped (C2); Read again during a
// reading pause shows the server's words (C3); the pause words, never "tomorrow morning" (C4); the
// admin's "Pages read today" with no AI use in the period (P7); and the words and small fixes U12
// to U15. The other items' tests sit with their screens' tests.

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { retryErrorText, usageState } from "@/components/admin/logic";
import { UsageReportView } from "@/components/admin/UsagePanel";
import { MistralStopped } from "@/components/operator/OrgAiSettings";
import { ORG_NUMBERS_NOTE } from "@/components/operator/OrgNumbers";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import { failureText } from "@/components/review/wordReview";
import { fixErrorText } from "@/components/viewer/pageProblems";
import { reprocessErrorText } from "@/components/viewer/reprocess";
import { ApiError, getUploadLimits, onSessionEnded } from "@/lib/api";
import type { UsageReport, UsageTotals } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

describe("an admin told to set up two-step mid-session (C1)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    onSessionEnded(null);
  });

  const reply = (status: number, code: string) =>
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: { code, message: "x" } }), { status }));

  it("ends the session, as a 401 does, so the sign-in sets it up", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    reply(403, "mfa_enrol_required");
    await expect(getUploadLimits()).rejects.toMatchObject({ status: 403, code: "mfa_enrol_required" });
    expect(ended).toHaveBeenCalledTimes(1);
  });

  it("not for any other 403", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    for (const code of ["forbidden", "admin_session_required", "feature_off"]) {
      reply(403, code);
      await expect(getUploadLimits()).rejects.toMatchObject({ status: 403, code });
    }
    expect(ended).not.toHaveBeenCalled();
  });
});

describe("Mistral on the Health tab (C2)", () => {
  it("shows the Mistral stopped banner above Reader, by worker, from the health's mistral_status", () => {
    const view = read("components/operator/HealthView.tsx");
    const banner = view.indexOf("<MistralStopped");
    expect(banner).toBeGreaterThan(-1);
    expect(banner).toBeLessThan(view.indexOf("Reader, by worker"));
    expect(view).toContain("health.mistral_status");
    expect(read("lib/types.ts")).toContain("mistral_status?: MistralStatus;"); // the health reply's, optional as reader and queues
  });

  it("says Mistral stopped from the status alone, and nothing while it reads", () => {
    const since = "2026-11-02T09:00:00Z";
    const banner = (stopped: boolean) =>
      renderToStaticMarkup(
        createElement(MistralStopped, {
          env: {
            mistral_status: { stopped, code: stopped ? "mistral" : null, since, documents_waiting: stopped ? 2 : 0 },
            mistral_ready: true,
            mistral_missing: null,
          },
        }),
      );
    expect(banner(true)).toContain("2 documents are waiting across all organisations.");
    expect(banner(true)).toContain('role="status"');
    expect(banner(false)).toBe("");
  });
});

describe("Read again during a reading pause (C3)", () => {
  const DETAIL =
    "Reading is paused, so this document can't be read again now. It stays searchable. Try again when the daily limit resets.";
  const paused = new ApiError(409, "reading_paused", DETAIL);

  it("shows the server's words on every Read again and Retry", () => {
    expect(reprocessErrorText(paused)).toBe(DETAIL); // the document's Read again, the list's Retry
    expect(fixErrorText(paused)).toBe(DETAIL); // a page's Read again
    expect(failureText(paused)).toBe(DETAIL); // Read this spot again
    expect(retryErrorText(paused)).toBe(DETAIL); // Admin, Failed documents
    // The list's bulk Read again says the server's message as is.
    expect(read("components/documents/DocumentsTable.tsx")).toContain(
      'return caught instanceof ApiError ? caught.message : "Check your connection, then try again.";',
    );
  });
});

describe("the pause words (C4)", () => {
  it("never say tomorrow morning or next morning: the limit may be raised sooner", () => {
    const files = ["components", "app", "lib"].flatMap((dir) =>
      (readdirSync(join(ROOT, dir), { recursive: true, encoding: "utf8" }) as string[])
        .filter((file) => /\.(ts|tsx)$/.test(file))
        .map((file) => join(dir, file)),
    );
    for (const file of files) expect(read(file), file).not.toMatch(/(tomorrow|next) morning/i);
  });
});

describe("the admin's Pages read today (P7)", () => {
  const totals = (calls: number): UsageTotals => ({
    calls,
    cached_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    cost_usd: 0,
    cost_estimated: false,
    cost_part_unknown: false,
  });
  const report = (calls: number, aiEnabled = true): UsageReport => ({
    from: "2026-11-04",
    to: "2026-11-04",
    totals: totals(calls),
    by_user: [],
    by_model: [],
    by_kind: [],
    by_day: [],
    top_documents: [],
    ai_enabled: aiEnabled,
    reading_today: { pages: 2000, limit: 2000 },
  });
  const view = (r: UsageReport) =>
    renderToStaticMarkup(createElement(UsageReportView, { state: usageState(r, null), onRetry: () => undefined }));
  const line = "Pages read today: 2,000 of 2,000. Reading is paused until the limit resets, or until it is raised.";

  it("shows even when the period has no AI calls", () => {
    expect(view(report(0))).toContain(line);
    expect(view(report(0))).toContain("No AI use in this period.");
    expect(view(report(3))).toContain(line);
    expect(view(report(0, false))).toContain(line);
    expect(view({ ...report(0), reading_today: { pages: 4, limit: null } })).not.toContain("Pages read today");
  });
});

describe("words and small fixes (U12 to U15)", () => {
  it("says what the AI cost is for each reader (U12)", () => {
    expect(ORG_NUMBERS_NOTE).toContain("AI cost is what OpenRouter reported for Gemini, and Mistral's price a page for Mistral.");
  });

  it("names everything the AI settings choose (U13)", () => {
    // "which AI answers", not "...its questions": a menu's plain line stays within 110 characters.
    expect(MENU_HELP.ai).toBe("Choose who reads its pages, which AI answers, Ensemble, its daily page limit and its monthly budget.");
  });

  it("shows a request limits problem once, and the code field before the first Save (U15)", () => {
    const view = read("components/operator/OrgModelRates.tsx");
    expect(view.match(/role="alert"/g)).toHaveLength(2); // one while nothing loaded, one in the form
    expect(view).toContain('{problem && !rates && <p className="error-text" role="alert">{problem}</p>}');
    expect(view.indexOf("<CodeField")).toBeGreaterThan(-1);
    expect(view.indexOf("<CodeField")).toBeLessThan(view.indexOf("Save organisation cap"));
  });
});
