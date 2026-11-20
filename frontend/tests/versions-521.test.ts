// #521 (Tushar, 6 Nov 2026): a near match is only ever suggested, with two buttons, Upload as a
// new version and Keep both, the share of the text and See the differences (the
// banner, the upload tray's row and the compare screen); Make it a separate file on the
// Versions tab. The server's side: backend tests/test_versions*.py.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BatchTray, TrayChoice, trayChoices } from "@/components/documents/UploadArea";
import { buildTree } from "@/components/documents/folderTree";
import { CompareBody, isJumpKey } from "@/components/versions/CompareView";
import { SeparateDialog } from "@/components/versions/SeparateDialog";
import { VersionChoice, chooseFile, chooseVersion } from "@/components/versions/VersionChoice";
import { VersionsPanel } from "@/components/versions/VersionsPanel";
import { flashTarget, saveFlash, takeFlash } from "@/components/versions/flash";
import {
  VERSION_TEXT,
  bannerTitle,
  highlightVersion,
  reasonsText,
  separateDefaultName,
  separateNameProblem,
} from "@/components/versions/words";
import * as api from "@/lib/api";
import type { DocumentCompare, DocumentDetail, DocumentSummary, VersionSuggestion } from "@/lib/types";

const render = (element: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(element);
const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

afterEach(() => {
  vi.unstubAllGlobals();
});

const suggestion = (over: Partial<VersionSuggestion> = {}): VersionSuggestion => ({
  id: "s1",
  role: "new",
  document: { id: "d3", name: "Satbara 44.pdf" },
  candidate: { family_id: "d1", id: "d1", name: "7/12 Survey 44 Wakad.pdf" },
  next_version_no: 2,
  text_overlap: 0.86,
  text_overlap_estimated: false,
  name_similarity: 0.31,
  shared_identifier: "survey_number",
  reasons: "86% the same text",
  outcome: "suggested",
  status: "pending",
  ...over,
});

describe("Pattern A: the words and the share of the text", () => {
  it("rounds down, never says 100% unless counted exactly the same, and says about for an estimate", () => {
    const scores = { name_similarity: null, shared_identifier: null };
    expect(reasonsText({ ...scores, text_overlap: 0.869 })).toBe("86% the same text");
    expect(reasonsText({ ...scores, text_overlap: 0.8299999833106995 })).toBe("83% the same text"); // a 4-byte real
    expect(reasonsText({ ...scores, text_overlap: 0.996 })).toBe("99% the same text");
    expect(reasonsText({ ...scores, text_overlap: 1 })).toBe("100% the same text");
    expect(reasonsText({ ...scores, text_overlap: 1, text_overlap_estimated: true })).toBe("about 99% the same text");
    expect(bannerTitle("Lease.pdf", "99% the same text")).toBe('This looks like a new version of "Lease.pdf" (99% the same text).');
  });

  it("highlights Save as new version for the same name (0.8 or more), else Keep both", () => {
    expect(highlightVersion(suggestion({ name_similarity: 0.8 }))).toBe(true);
    expect(highlightVersion(suggestion({ name_similarity: 0.79 }))).toBe(false);
    expect(highlightVersion(suggestion({ name_similarity: null }))).toBe(false);
  });

  it("renders the two buttons, the highlight by name and See the differences", () => {
    const other = render(createElement(VersionChoice, { suggestion: suggestion(), onDone: () => undefined }));
    expect(other).toContain('This looks like a new version of &quot;7/12 Survey 44 Wakad.pdf&quot; (86% the same text).');
    expect(other).toMatch(/<button[^>]*data-highlight="false"[^>]*>Save as new version<\/button>/);
    expect(other).toMatch(/<button[^>]*data-highlight="true"[^>]*>Keep both<\/button>/); // the NDA case
    expect(other).toContain('href="/documents/d3/compare?with=d1"');
    expect(other).toContain(">See the differences<");
    const same = render(
      createElement(VersionChoice, { suggestion: suggestion({ name_similarity: 1 }), onDone: () => undefined }),
    );
    expect(same).toMatch(/<button[^>]*data-highlight="true"[^>]*>Save as new version<\/button>/);
  });

  it("accepts or keeps apart, with the words after each and Undo after a version", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url}`);
      return new Response("{}", { status: 200 });
    });
    const version = await chooseVersion(suggestion({ next_version_no: 3 }));
    expect(version.message).toBe('Saved as version 3 of "7/12 Survey 44 Wakad.pdf".');
    await version.undo?.();
    const file = await chooseFile(suggestion());
    expect(file).toEqual({ message: "Kept as its own file. Kaagaz won't ask about these two again." });
    expect(calls).toEqual([
      "POST /api/version-suggestions/s1/accept",
      "POST /api/documents/d3/version-undo",
      "POST /api/version-suggestions/s1/reject",
    ]);
  });

  it("gives each file of the upload tray that looks like a new version its own row with the choice", () => {
    const document = { id: "d3", display_name: "Satbara 44.pdf", status: "ready" } as DocumentSummary;
    const html = render(
      createElement(BatchTray, {
        entries: [{ key: "u1", name: "Satbara 44.pdf", size: 10, batchId: "b1", state: { kind: "done", documentId: "d3", document } }],
        views: {
          b1: {
            id: "b1",
            documents: [document],
            counts: { total: 1, ready: 1, reading: 0, waiting: 0, attention: 0, failed: 0 },
            pages_read: 1,
            pages_total: 1,
            time_left: null,
            version_suggestion_count: 1,
            version_suggestions: [suggestion()],
          },
        },
        stored: [],
        onClearFinished: () => undefined,
        onRestore: () => undefined,
        onRetry: () => undefined,
        tree: buildTree([]),
        onUndone: () => undefined,
        canReread: true,
      }),
    );
    expect(html).toContain("1 file looks like a new version"); // the summary stays
    // Changed on purpose (#690): one wording everywhere; the file is already uploaded, so "Save as".
    expect(html).toContain(">Save as new version<");
    expect(html).toContain(">Keep both<");
    expect(html).toContain(">See the differences<");
  });
});

describe("the upload tray keeps what was decided", () => {
  it("keeps the row's words and Undo after a later poll says the suggestion was decided", () => {
    const decided = suggestion({ status: "accepted" });
    const chosen = { message: 'Saved as version 2 of "7/12 Survey 44 Wakad.pdf".', undo: async () => undefined };
    // The poll now sends it accepted: no longer asked, but its row still says what was done.
    const rows = trayChoices([decided], new Map([["s1", { suggestion: suggestion(), chosen, undone: false }]]));
    expect(rows.get("d3")?.chosen?.message).toBe(chosen.message);
    expect(trayChoices([decided], new Map()).get("d3")).toBeUndefined(); // decided elsewhere: nothing
    expect(trayChoices([suggestion()], new Map()).get("d3")?.chosen).toBeUndefined(); // still asked
    const html = render(
      createElement(TrayChoice, { suggestion: suggestion(), chosen, undone: false, onDone: () => undefined, onUndone: () => undefined }),
    );
    expect(html).toContain('Saved as version 2 of &quot;7/12 Survey 44 Wakad.pdf&quot;.');
    expect(html).toContain(">Undo<");
  });
});

describe("See the differences", () => {
  const pages: DocumentCompare["pages"] = [
    {
      page_no: 1,
      changes: 1,
      truncated: false,
      words_changed: 2,
      left_words: 40,
      right_words: 40,
      runs: [
        { kind: "same", text: "Survey 44 " },
        { kind: "removed", text: "old owner " },
        { kind: "added", text: "new owner " },
      ],
    },
    { page_no: 2, changes: 0, truncated: false, words_changed: 0, left_words: 3, right_words: 2, runs: [{ kind: "same", text: "seal" }] },
  ];
  const data: DocumentCompare = {
    left_id: "d1",
    right_id: "d3",
    pages,
    summary: "1 change on page 1",
    truncated: false,
    overview: "94% the same. 2 words changed on page 1.",
    // Changed on purpose (PRD 9 F-08): the reply names each side's pages with a picture (scans here).
    image_pages: { left: [1, 2], right: [1, 2] },
  };

  it("says it in plain words, marks each change with + and − and a label, and jumps to the next change", () => {
    const html = render(createElement(CompareBody, { data }));
    expect(html).toContain("94% the same. 2 words changed on page 1.");
    expect(html).toContain(">Jump to next change<");
    expect(html).toMatch(/<del[^>]*><span aria-hidden="true">−<\/span><span class="visually-hidden">Removed: <\/span>old owner/);
    expect(html).toMatch(/<ins[^>]*><span aria-hidden="true">\+<\/span><span class="visually-hidden">Added: <\/span>new owner/);
    expect(html).toContain('aria-keyshortcuts="n"');
  });

  it("shows a page with little text as the two page images side by side", () => {
    const html = render(createElement(CompareBody, { data }));
    expect(html).toContain('src="/api/documents/d1/pages/2/image"');
    expect(html).toContain('src="/api/documents/d3/pages/2/image"');
    expect(html).not.toContain('src="/api/documents/d1/pages/1/image"');
  });

  it("says when only the first part of long files is compared", () => {
    const long = { ...data, truncated: true, overview: "These files are long, so only the first part is compared." };
    expect(render(createElement(CompareBody, { data: long }))).toContain(
      "These files are long, so only the first part is compared.",
    );
  });

  it("puts the decision at the bottom for a suggested pair", () => {
    const html = render(createElement(CompareBody, { data, choice: suggestion(), onChosen: () => undefined }));
    expect(html.indexOf(">Save as new version<")).toBeGreaterThan(html.indexOf("new owner"));
    expect(html).toContain(">Keep both<");
    expect(html).not.toContain(">See the differences<"); // already here
  });

  it("carries the words after the choice to the document it goes back to, and only to it, once", () => {
    // Changed on purpose (review, 6 Nov 2026): the words name their document; another page leaves them.
    const store = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    });
    saveFlash({ message: 'Saved as version 2 of "Lease.pdf".', documentId: "d3", undoLinkOf: "d3" });
    expect(takeFlash("d1")).toBeNull(); // another document's page: not shown, kept
    expect(takeFlash("d3")).toEqual({ message: 'Saved as version 2 of "Lease.pdf".', documentId: "d3", undoLinkOf: "d3" });
    expect(takeFlash("d3")).toBeNull(); // once
    expect(flashTarget({ href: "/documents/d7", name: "previous document" }, "d3")).toBe("d7");
    expect(flashTarget({ href: "/chat", name: "chat" }, "d3")).toBeNull(); // no document: no words
    expect(flashTarget(null, "d3")).toBe("d3");
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toContain("takeFlash(documentId)");
  });

  it("jumps with N only while the focus is inside the compare screen, never while typing (WCAG 2.1.4)", () => {
    const key = (k: string, more: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean }> = {}) => ({
      key: k,
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      ...more,
    });
    expect(isJumpKey(key("n"), { inside: true, typing: false })).toBe(true);
    expect(isJumpKey(key("N"), { inside: true, typing: false })).toBe(true);
    expect(isJumpKey(key("n"), { inside: false, typing: false })).toBe(false);
    expect(isJumpKey(key("n"), { inside: true, typing: true })).toBe(false);
    expect(isJumpKey(key("n", { ctrlKey: true }), { inside: true, typing: false })).toBe(false);
    const source = read("components/versions/CompareView.tsx");
    expect(source).not.toContain('window.addEventListener("keydown"');
    expect(render(createElement(CompareBody, { data }))).toMatch(/<div[^>]*data-compare-area="true"/);
  });

  it("shows a page image only for a side that has that page, else says so", () => {
    const short = { ...data, left_pages: 1, right_pages: 2, image_pages: { left: [1], right: [1, 2] } };
    const html = render(createElement(CompareBody, { data: short }));
    expect(html).not.toContain('src="/api/documents/d1/pages/2/image"');
    expect(html).toContain("No page 2 in this file");
    expect(html).toContain('src="/api/documents/d3/pages/2/image"');
  });
});

describe("Make it a separate file", () => {
  const family = {
    id: "d1",
    family_id: "d1",
    display_name: "Lease.pdf",
    deleted_at: null,
    versions: [
      { id: "d1", version_no: 1, status: "ready", is_current: false, created_at: "2025-06-12T10:00:00Z", display_name: "Lease.pdf" },
      { id: "d3", version_no: 2, status: "ready", is_current: true, created_at: "2026-06-12T10:00:00Z", display_name: "Lease.pdf" },
    ],
  } as unknown as DocumentDetail;

  it("is offered on each newer version's menu, never on version 1 or for a single version", () => {
    // Changed on purpose (review, 6 Nov 2026): version 1's id is its family's, so it is refused.
    const html = render(createElement(VersionsPanel, { detail: family, onChanged: () => undefined }));
    expect(html.match(/>Make it a separate file</g)).toHaveLength(1);
    expect(html).toContain('aria-label="Actions for version 2"');
    expect(html).not.toContain('aria-label="Actions for version 1"');
    const alone = { ...family, versions: family.versions.slice(0, 1) } as DocumentDetail;
    expect(render(createElement(VersionsPanel, { detail: alone, onChanged: () => undefined }))).not.toContain(
      "Make it a separate file",
    );
  });

  it("names it by its own upload name, else the family's with the version number", () => {
    expect(separateDefaultName("Lease draft.pdf", "Lease.pdf", 2)).toBe("Lease draft.pdf");
    expect(separateDefaultName("Lease.pdf", "Lease.pdf", 2)).toBe("Lease (version 2).pdf");
    expect(separateDefaultName("Notes", "Notes", 3)).toBe("Notes (version 3)");
  });

  it("refuses an empty name, hidden or bidi characters and a name too long", () => {
    expect(separateNameProblem("Lease B.pdf")).toBeNull();
    expect(separateNameProblem("   ")).toBe("Enter a name.");
    expect(separateNameProblem("Lease‮gnp.pdf")).toBe("Remove the hidden characters from the name.");
    expect(separateNameProblem("Lease​.pdf")).toBe("Remove the hidden characters from the name.");
    expect(separateNameProblem("x".repeat(256))).toBe("Use at most 255 characters.");
  });

  it("asks in a dialog with the name filled in, the note, and Make separate file", () => {
    const html = render(
      createElement(SeparateDialog, {
        version: family.versions[1]!,
        familyName: "Lease.pdf",
        onDone: () => undefined,
        onClose: () => undefined,
      }),
    );
    expect(html).toContain("Make version 2 a separate file");
    expect(html).toMatch(/<label[^>]*>Name<\/label>/);
    expect(html).toContain('value="Lease (version 2).pdf"');
    expect(html).toContain(
      "It keeps its reading, words you fixed, and history. Kaagaz won&#x27;t suggest it as a version of &quot;Lease.pdf&quot; again.",
    );
    expect(html).toContain(">Make separate file<");
    expect(html).toContain(">Cancel<");
  });

  it("says what it did, with Undo, and calls the two routes", async () => {
    expect(VERSION_TEXT.separated(2, "Lease B.pdf")).toBe('Version 2 is now its own file, "Lease B.pdf".');
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`.trim());
      return new Response('{"document": {}, "family_id": "d1"}', { status: 200 });
    });
    await api.separateVersion("d3", "Lease B.pdf");
    await api.undoSeparate("d3", "d1", "Lease.pdf");
    expect(calls).toEqual([
      'POST /api/documents/d3/separate {"name":"Lease B.pdf"}',
      'POST /api/documents/d3/separate/undo {"family_id":"d1","name":"Lease.pdf"}',
    ]);
  });
});
