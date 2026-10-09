// The banner over a problem page (PRD 5 U4): what happened and the fix from the issue
// table, then Read this page again, Upload a better scan and Keep as is, asked inside
// the page first (components/viewer/pageProblems.ts, PageProblems.tsx).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PageProblems } from "@/app/(app)/documents/[id]/PageProblems";
import {
  READING_AGAIN_TEXT,
  fixErrorText,
  fixQuestion,
  fixReducer,
  keptText,
  problemsOf,
} from "@/components/viewer/pageProblems";
import type { FixState } from "@/components/viewer/pageProblems";
import { ApiError, acceptPage, readPageAgain } from "@/lib/api";
import type { PageSummary } from "@/lib/types";

const page = (more: Partial<PageSummary> = {}): PageSummary => ({
  page_no: 3,
  width: 595,
  height: 842,
  unit: "px",
  has_image: true,
  route: "ocr",
  effective_dpi: 300,
  low_resolution: false,
  read_by: "google/gemini-3-flash",
  read_quality: "unreadable",
  read_issues: ["glare"],
  unsure_words: 0,
  illegible_words: 12,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
  problems: [
    {
      code: "glare",
      says: "Glare or a shadow covers part of the page.",
      fix: "Take the photo without flash, away from a window, with the page flat.",
    },
  ],
  ...more,
});

const render = (props: Partial<Parameters<typeof PageProblems>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(PageProblems, {
      documentId: "d1",
      page: page(),
      userId: "u1",
      canFix: true,
      canUpload: true,
      onReadAgain: () => undefined,
      onKept: () => undefined,
      ...props,
    }),
  );

describe("the banner's words", () => {
  it("lists the page's problems; none on a good page or a reply from before PR 2", () => {
    expect(problemsOf(page()).map((problem) => problem.code)).toEqual(["glare"]);
    expect(problemsOf(page({ read_quality: "good" }))).toEqual([]);
    expect(problemsOf(page({ problems: undefined }))).toEqual([]);
  });

  it("asks first, in the page, saying what each fix does", () => {
    expect(fixQuestion("read", 3)).toBe(
      "Read page 3 again, more carefully? The rest of the document is not read again.",
    );
    expect(fixQuestion("keep", 3)).toBe(
      "Keep page 3 as it was read? It leaves the attention list but stays marked on the page and in answers.",
    );
  });

  it("says a page kept as is stays marked", () => {
    expect(keptText(page())).toBeNull();
    expect(keptText(page({ accepted_at: "2026-10-09T10:00:00Z" }))).toMatch(
      /^Kept as is on .+\. It stays marked on the page and in answers\.$/,
    );
    expect(READING_AGAIN_TEXT).toBe("Being read again. This page updates as it goes.");
  });

  it("says why a fix did not go through", () => {
    expect(fixErrorText(new ApiError(403, "forbidden", "No."))).toBe("Only a reviewer or an admin can do this.");
    expect(fixErrorText(new ApiError(409, "not_reprocessable", "No."))).toBe(
      "This page can't be read again right now. Try again once the document is ready.",
    );
    expect(fixErrorText(new ApiError(429, "rate_limited", "Too many reads again this hour."))).toBe(
      "Too many reads again this hour.",
    );
    expect(fixErrorText(new TypeError("offline"))).toBe("Check your internet connection, then try again.");
  });
});

describe("the banner's steps", () => {
  const idle: FixState = { step: "idle" };

  it("asks, then sends once, then says it is done, so focus can go to its note", () => {
    const asking = fixReducer(idle, { type: "open", fix: "keep" });
    expect(asking).toEqual({ step: "confirming", fix: "keep" });
    const sending = fixReducer(asking, { type: "send" });
    expect(sending).toEqual({ step: "sending", fix: "keep" });
    expect(fixReducer(sending, { type: "send" })).toBe(sending); // never twice
    expect(fixReducer(sending, { type: "open", fix: "read" })).toBe(sending);
    const done = fixReducer(sending, { type: "sent" });
    expect(done).toEqual({ step: "done", fix: "keep" });
    expect(fixReducer(done, { type: "open", fix: "read" })).toEqual({ step: "confirming", fix: "read" });
  });

  it("goes back on Cancel, and keeps the reason on a failure until asked again", () => {
    const asking = fixReducer(idle, { type: "open", fix: "read" });
    expect(fixReducer(asking, { type: "cancel" })).toEqual(idle);
    const failed = fixReducer(fixReducer(asking, { type: "send" }), { type: "failed", message: "No." });
    expect(failed).toEqual({ step: "error", message: "No." });
    expect(fixReducer(failed, { type: "open", fix: "read" })).toEqual({ step: "confirming", fix: "read" });
    expect(fixReducer(idle, { type: "send" })).toBe(idle);
    expect(fixReducer(idle, { type: "failed", message: "x" })).toBe(idle);
  });
});

describe("the banner (PageProblems)", () => {
  it("says what happened and the fix, with the three buttons for a reviewer", () => {
    const html = render();
    expect(html).toContain("Glare or a shadow covers part of the page.");
    expect(html).toContain("Take the photo without flash, away from a window, with the page flat.");
    expect(html).toContain("Read this page again");
    expect(html).toContain("Upload a better scan");
    expect(html).toContain("Keep as is");
  });

  it("offers a member only the better scan, and nothing in Trash", () => {
    const member = render({ canFix: false });
    expect(member).not.toContain("Read this page again");
    expect(member).not.toContain("Keep as is");
    expect(member).toContain("Upload a better scan");
    expect(render({ canFix: false, canUpload: false })).not.toContain("<button");
  });

  it("says a kept page stays marked, and a page waiting to be read again", () => {
    const kept = render({ page: page({ accepted_at: "2026-10-09T10:00:00Z", accepted_by: "u2" }) });
    expect(kept).toContain("It stays marked on the page and in answers.");
    expect(kept).not.toContain("Keep as is<");
    const again = render({ page: page({ read_again_requested_at: "2026-10-09T10:00:00Z" }) });
    expect(again).toContain(READING_AGAIN_TEXT);
    expect(again).not.toContain("Read this page again");
    // Each note can take focus, which goes to it once its fix has gone through (the
    // confirmation it replaces is gone): the read again note is also the live status.
    expect(again).toMatch(/<p [^>]*role="status"[^>]*tabindex="-1"[^>]*>Being read again/);
    expect(kept).toMatch(/<p [^>]*tabindex="-1"[^>]*>[^<]*It stays marked/);
  });

  it("shows nothing for a good page", () => {
    expect(render({ page: page({ read_quality: "good", problems: [] }) })).toBe("");
  });
});

describe("the page's fixes (lib/api.ts)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts Read again and Keep as is for one page", async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push([String(init.method), url]);
      return new Response("{}", { status: 200 });
    });
    await readPageAgain("d1", 3);
    await acceptPage("d1", 3);
    expect(calls).toEqual([
      ["POST", "/api/documents/d1/pages/3/read-again"],
      ["POST", "/api/documents/d1/pages/3/accept"],
    ]);
  });
});
