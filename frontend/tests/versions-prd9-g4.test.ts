// PRD 9 G4 "Versions" (Round 1 fixes, 18 Nov 2026): F-06 the pop-up after an upload that looks like
// a new version, F-07 the link dialog's latest marker, F-08 page images only where a page has a
// picture, F-09 "Make it a version of…" in a file's menu. Rendered with react-dom in the small fake
// DOM (tests/fake-dom.ts) and pressed, or as markup. The server's side: backend tests/test_versions*.py.

import { act, createElement } from "react";
import type { ReactElement } from "react";
import type { Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { menuEntries } from "@/components/documents/library";
import { CompareBody } from "@/components/versions/CompareView";
import { LinkDialog } from "@/components/versions/LinkDialog";
import { VersionsPanel } from "@/components/versions/VersionsPanel";
import { VersionPopup } from "@/components/versions/VersionPopup";
import type { LinkFile } from "@/components/versions/link";
import type { DocumentCompare, DocumentDetail, Me, VersionSuggestion } from "@/lib/types";
import { DocumentMenu } from "../app/(app)/documents/[id]/DocumentMenu";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const session = vi.hoisted(() => ({ me: null as unknown }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: session.me, refresh: () => undefined }) }));
// next/link prefetches with requestIdleCallback on `self`, which the fake DOM has not: a plain link.
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const person = (role: Me["user"]["role"], scope: "app" | "admin", management = false): Me =>
  ({
    user: { id: "u1", email: "asha@example.in", display_name: "Asha", role, must_change_password: false },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
    access: { management },
  }) as unknown as Me;

let doc: FakeDocument;
let root: Root | null = null;
let calls: string[] = [];

/** fetch answers each "METHOD /api/path" with its body (200). */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = `${init.method ?? "GET"} ${url}`;
    calls.push(call);
    const body = routes[call];
    if (body === undefined) return new Response(JSON.stringify({ error: { code: "x", message: call } }), { status: 500 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

async function mount(element: ReactElement) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  return container;
}
const rerender = (element: ReactElement) => act(async () => root!.render(element));
const all = () => doc.body.all();
const text = () => doc.body.textContent;
const button = (words: string) => all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === words) ?? null;
/** A menu item, by its words (its hint follows them). */
const item = (words: string) =>
  all().find((element) => element.getAttribute("role") === "menuitem" && element.textContent.startsWith(words)) ?? null;
const byLabel = (label: string) => all().find((element) => element.getAttribute("aria-label") === label) ?? null;
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
};
/** The file whose "This is the latest" radio is on. */
const latest = () =>
  all()
    .filter((element) => element.tagName === "INPUT" && element.type === "radio" && element.checked)
    .map((element) => element.getAttribute("aria-label"));

beforeEach(() => {
  doc = install();
  (window as unknown as { location: { href: string } }).location = { href: "http://localhost/documents/d1" };
  session.me = person("member", "app", true);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("F-07: the link dialog's latest marker", () => {
  // v1 at 7:24 pm, v2 at 8:49 pm (Round 1); `date` is each file's own date, else its upload.
  const v1: LinkFile = { id: "v1", name: "plot data.json", date: "2026-11-17T13:54:00Z", uploaded: "2026-11-17T13:54:00Z" };
  const v2: LinkFile = { id: "v2", name: "plot data v2.json", date: "2026-11-17T15:19:00Z", uploaded: "2026-11-17T15:19:00Z" };
  const dialog = (files: LinkFile[]) =>
    mount(createElement(LinkDialog, { files, start: "order", onClose: () => undefined, onLinked: () => undefined }));

  it("starts on the newest upload, even when a file's own date puts another last", async () => {
    await dialog([{ ...v2, date: "2025-01-01" }, v1]); // v2 dated 2025 by its text: first in the order
    expect(latest()).toEqual(["This is the latest: plot data v2.json"]);
  });

  it("moves with the last row after a reorder, unless the person picked one", async () => {
    await dialog([v1, v2]);
    expect(latest()).toEqual(["This is the latest: plot data v2.json"]); // without touching it: v2
    await press(byLabel("Move up: plot data v2.json"));
    expect(latest()).toEqual(["This is the latest: plot data.json"]); // the last row now
    await press(byLabel("Move up: plot data.json"));
    expect(latest()).toEqual(["This is the latest: plot data v2.json"]);
    const radio = byLabel("This is the latest: plot data.json")!;
    radio.checked = true;
    await press(radio); // picked
    await press(byLabel("Move down: plot data.json"));
    expect(latest()).toEqual(["This is the latest: plot data.json"]); // the pick stays
  });
});

describe("F-07: Make current only when the family's current version is one the person sees", () => {
  const family = (currentSeen: boolean) =>
    ({
      id: "v1",
      family_id: "v1",
      display_name: "plot data.json",
      deleted_at: null,
      versions: [
        { id: "v1", version_no: 1, status: "ready", is_current: false, created_at: "2026-11-17T13:54:00Z", display_name: "plot data.json" },
        ...(currentSeen
          ? [{ id: "v2", version_no: 2, status: "ready", is_current: true, created_at: "2026-11-17T15:19:00Z", display_name: "plot data.json" }]
          : []), // v2, the current one, is out of this person's sight: the server answers 404
      ],
    }) as unknown as DocumentDetail;

  it("is offered on a version that is not current while the current one is listed, else not", () => {
    const shown = renderToStaticMarkup(createElement(VersionsPanel, { detail: family(true), onChanged: () => undefined }));
    expect(shown).toContain(">Make current<");
    const hidden = renderToStaticMarkup(createElement(VersionsPanel, { detail: family(false), onChanged: () => undefined }));
    expect(hidden).not.toContain(">Make current<");
  });

  it("tells a person who may not link versions why, instead of hiding the action (#690)", () => {
    const reason = "Only Management, or an Admin in an admin session, can link versions.";
    const cannot = renderToStaticMarkup(createElement(VersionsPanel, { detail: family(true), onChanged: () => undefined }));
    expect(cannot).toMatch(/<button[^>]*disabled=""[^>]*>Add an older or newer version<\/button>/);
    expect(cannot).toContain(reason);
    const can = renderToStaticMarkup(createElement(VersionsPanel, { detail: family(true), onChanged: () => undefined, canLink: true }));
    expect(can).toMatch(/<button type="button" class="btn">Add an older or newer version<\/button>/);
    expect(can).not.toContain(reason);
  });
});

describe("F-08: page images only for pages that have a picture", () => {
  const data: DocumentCompare = {
    left_id: "d1",
    right_id: "d3",
    summary: "3 changes on page 1",
    truncated: false,
    overview: "3 words changed on page 1.",
    pages: [
      {
        page_no: 1,
        changes: 2,
        truncated: false,
        words_changed: 3,
        left_words: 11,
        right_words: 11,
        runs: [
          { kind: "same", text: '"survey": [72, ' },
          { kind: "removed", text: "73]" },
          { kind: "added", text: "74]" },
        ],
      },
    ],
    image_pages: { left: [], right: [] },
  };

  it("shows a JSON pair (no page pictures) as the text side by side with the change marked, never an image", () => {
    const html = renderToStaticMarkup(createElement(CompareBody, { data }));
    expect(html).not.toContain("<img");
    expect(html).toMatch(/<del[^>]*>.*73\]<\/del>/);
    expect(html).toMatch(/<ins[^>]*>.*74\]<\/ins>/);
    expect(html).toContain(">Jump to next change<");
  });

  it("shows a scan's page images when it has the picture", () => {
    const scans = { ...data, image_pages: { left: [1], right: [1] } };
    const html = renderToStaticMarkup(createElement(CompareBody, { data: scans }));
    expect(html).toContain('src="/api/documents/d1/pages/1/image"');
    expect(html).toContain('src="/api/documents/d3/pages/1/image"');
  });
});

describe("F-09: Make it a version of… in a file's menu", () => {
  const row = { id: "d1", status: "ready" as const, deleted_at: null, can_delete: true, locked: false };
  const words = (entries: ReturnType<typeof menuEntries>) => entries.map((entry) => (entry.kind === "action" ? entry.words : ""));

  it("is in the list's menu and the document's, for whoever may link", () => {
    expect(words(menuEntries(row, false, "list", false, undefined, true))).toContain("Make it a version of…");
    expect(words(menuEntries(row, false, "document", false, undefined, true))).toContain("Make it a version of…");
    expect(words(menuEntries(row, false, "document"))).not.toContain("Make it a version of…");
  });

  const detail = {
    id: "d1",
    family_id: "d1",
    display_name: "plot data.json",
    created_at: "2026-11-17T15:19:00Z",
    document_date: null,
    deleted_at: null,
    can_delete: true,
    status: "ready",
    locked: false,
    folder_id: null,
    page_count: 1,
  } as unknown as DocumentDetail;
  const menu = () =>
    mount(
      createElement(DocumentMenu, {
        buttonRef: { current: null },
        detail,
        readAgain: false,
        onReadAgain: () => undefined,
        onChanged: () => undefined,
      }),
    );

  it("opens the link dialog on the document page with this file fixed", async () => {
    serve({ "GET /api/folders": { items: [] } });
    await menu();
    await press(byLabel("More actions for plot data.json"));
    await press(item("Make it a version of…"));
    const dialog = all().find((element) => element.tagName === "DIALOG");
    expect(dialog?.textContent).toContain("Link as versions");
    expect(dialog?.textContent).toContain("Chosen (1)plot data.json");
    expect(byLabel("Remove plot data.json")).toBeNull(); // fixed
    expect(dialog?.textContent).toContain("Find files by name"); // the files the person may open (GET /documents)
  });

  it("is not there for a member who is not Management, nor an Admin outside an admin session", async () => {
    for (const me of [person("member", "app"), person("admin", "app")]) {
      session.me = me;
      await menu();
      await press(byLabel("More actions for plot data.json"));
      expect(item("Make it a version of…")).toBeNull();
      expect(item("Rename")).not.toBeNull(); // the menu is open
      await act(async () => root?.unmount());
      root = null;
    }
  });
});

describe("F-06: the pop-up after an upload that looks like a new version", () => {
  const suggestion = (id: string, name: string, overlap = 0.8182): VersionSuggestion => ({
    id,
    role: "new",
    outcome: "suggested",
    status: "pending",
    document: { id: `${id}-new`, name },
    candidate: { family_id: `${id}-old`, id: `${id}-old`, name },
    next_version_no: 2,
    text_overlap: overlap,
    text_overlap_estimated: false,
    name_similarity: 1,
    shared_identifier: null,
    reasons: `${Math.floor(overlap * 100)}% the same text`,
  });
  const one = [suggestion("s1", "plot data.json")];

  it("asks once it is read, and Yes saves it as the next version with Undo", async () => {
    serve({ "POST /api/version-suggestions/s1/accept": {}, "POST /api/documents/s1-new/version-undo": {} });
    await mount(createElement(VersionPopup, { suggestions: [] }));
    expect(all().find((element) => element.tagName === "DIALOG")).toBeUndefined(); // still being read
    await rerender(createElement(VersionPopup, { suggestions: one }));
    expect(text()).toContain('This looks like a new version of "plot data.json" (81% the same text). Save it as a new version?');
    expect(all().some((element) => element.tagName === "A" && element.getAttribute("href") === "/documents/s1-new/compare?with=s1-old")).toBe(true);
    // Changed on purpose (#690): one wording everywhere, as the banner and the Upload tab.
    expect(text()).toContain("Versions keep one file with its history. Search and Ask use the Current version.");
    expect(button("Save all as new versions")).toBeNull(); // one pair: no Save all as new versions
    await press(button("Save as new version"));
    expect(calls).toEqual(["POST /api/version-suggestions/s1/accept"]);
    expect(text()).toContain('Saved as version 2 of "plot data.json".');
    await press(button("Undo"));
    expect(calls.at(-1)).toBe("POST /api/documents/s1-new/version-undo");
    expect(text()).toContain("Undone.");
  });

  it("No keeps it separate; closed, the same suggestion is not asked again here", async () => {
    serve({ "POST /api/version-suggestions/s1/reject": { id: "s1", status: "rejected", document_id: "s1-new" } });
    await mount(createElement(VersionPopup, { suggestions: one }));
    await press(button("Keep both"));
    expect(calls).toEqual(["POST /api/version-suggestions/s1/reject"]);
    expect(text()).toContain("Kept as its own file. Kaagaz won't ask about these two again.");
    await press(button("Close"));
    await rerender(createElement(VersionPopup, { suggestions: one })); // a later poll, still listing it
    expect(all().find((element) => element.tagName === "DIALOG")).toBeUndefined();
  });

  it("lists several pairs in one pop-up, Save as new version or Keep both on each, and Save all as new versions", async () => {
    serve({ "POST /api/version-suggestions/s1/reject": {}, "POST /api/version-suggestions/s2/accept": {}, "POST /api/version-suggestions/s3/accept": {} });
    const three = [suggestion("s1", "plot data.json"), suggestion("s2", "Lease.pdf", 0.9), suggestion("s3", "Bill.pdf", 0.95)];
    await mount(createElement(VersionPopup, { suggestions: three }));
    expect(all().filter((element) => element.tagName === "BUTTON" && element.textContent === "Save as new version")).toHaveLength(3);
    await press(byLabel("Keep both: plot data.json"));
    await press(button("Save all as new versions"));
    expect(calls).toEqual([
      "POST /api/version-suggestions/s1/reject",
      "POST /api/version-suggestions/s2/accept",
      "POST /api/version-suggestions/s3/accept",
    ]);
    expect(text()).toContain('Saved as version 2 of "Lease.pdf".');
    expect(text()).toContain('Saved as version 2 of "Bill.pdf".');
    expect(button("Save all as new versions")).toBeNull();
  });

  it("remembers on this browser what it asked, so a reload does not ask again; works without storage", async () => {
    const store = new Map<string, string>();
    (window as unknown as { localStorage: unknown }).localStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    };
    await mount(createElement(VersionPopup, { suggestions: one }));
    await press(button("Close")); // closed without an answer
    await act(async () => root?.unmount());
    root = null;
    await mount(createElement(VersionPopup, { suggestions: one })); // the page loaded again
    expect(all().find((element) => element.tagName === "DIALOG")).toBeUndefined();
    await act(async () => root?.unmount());
    root = null;
    const blocked = () => {
      throw new Error("storage off");
    };
    (window as unknown as { localStorage: unknown }).localStorage = { getItem: blocked, setItem: blocked };
    await mount(createElement(VersionPopup, { suggestions: one }));
    expect(text()).toContain("Save it as a new version?"); // storage off: it still asks
    await press(button("Close"));
    expect(all().find((element) => element.tagName === "DIALOG")).toBeUndefined();
  });

  it("asks only pending suggestions, never one already answered or linked on its own", async () => {
    const answered = [{ ...suggestion("s1", "a.pdf"), status: "accepted" as const }, { ...suggestion("s2", "b.pdf"), outcome: "auto" as const }];
    await mount(createElement(VersionPopup, { suggestions: answered }));
    expect(all().find((element) => element.tagName === "DIALOG")).toBeUndefined();
  });
});
