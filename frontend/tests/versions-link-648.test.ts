// #648 (V-C): the screens for linking files already uploaded as versions (V13 to V16), the
// Possible versions view (V17, with the lead's flexible group of 16 Nov), the Kept apart list
// (V18) and the banner after a reload (V22). Only Admins and Management see any of it. The
// server's side: backend tests/test_isolation_version_links.py and test_isolation_version_groups.py.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FolderNav } from "@/components/documents/FolderNav";
import { buildTree } from "@/components/documents/folderTree";
import { menuEntries } from "@/components/documents/library";
import { ALL_DOCUMENTS } from "@/components/documents/libraryQuery";
import { ConfirmStep, OrderStep } from "@/components/versions/LinkDialog";
import {
  LINK_TEXT,
  byDate,
  confirmLines,
  linkBody,
  linkErrorText,
  linkableIds,
  mayLinkVersions,
  moved,
  movedTo,
  reasonLine,
  undoLink,
} from "@/components/versions/link";
import { GroupCard, KeptApartList } from "@/components/versions/PossibleVersions";
import { VersionsPanel } from "@/components/versions/VersionsPanel";
import { bannerState } from "@/components/versions/words";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type {
  DocumentDetail,
  DocumentSummary,
  KeptApart,
  LinkPreview,
  Me,
  VersionGroup,
  VersionGroupReason,
  VersionSuggestion,
} from "@/lib/types";

const render = (element: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(element);

afterEach(() => {
  vi.unstubAllGlobals();
});

const me = (role: Me["user"]["role"], scope: Me["session"]["scope"], management = false): Me =>
  ({
    user: { id: "u1", role },
    session: { scope },
    access: { management, heads: [], in_department: true },
  }) as unknown as Me;

describe("who sees the screens", () => {
  it("is an Admin in an admin session, or Management; never anyone else", () => {
    expect(mayLinkVersions(me("admin", "admin"))).toBe(true);
    expect(mayLinkVersions(me("member", "app", true))).toBe(true);
    expect(mayLinkVersions(me("reviewer", "app", true))).toBe(true);
    expect(mayLinkVersions(me("admin", "app"))).toBe(false); // the link needs the admin session
    // Review (#659): the server asks every Admin for an admin session, Management or not.
    expect(mayLinkVersions(me("admin", "app", true))).toBe(false);
    expect(mayLinkVersions(me("admin", "admin", true))).toBe(true);
    expect(mayLinkVersions(me("member", "app"))).toBe(false);
    expect(mayLinkVersions(me("reviewer", "app"))).toBe(false);
    const older = { user: { id: "u1", role: "member" }, session: { scope: "app" } } as unknown as Me;
    expect(mayLinkVersions(older)).toBe(false); // an older server sends no access
  });
});

describe("the order (V13 step 2)", () => {
  const file = (id: string, date: string | null, uploaded: string) => ({ id, name: `${id}.pdf`, date: date ?? uploaded });

  it("puts the oldest first, by the document's own date, else the upload date", () => {
    const files = [
      file("c", null, "2026-11-01T10:00:00Z"),
      file("a", "2024-03-01", "2026-11-02T10:00:00Z"),
      file("b", "2025-06-30", "2026-10-01T10:00:00Z"),
    ];
    expect(byDate(files).map((each) => each.id)).toEqual(["a", "b", "c"]);
  });

  it("moves a file up or down by one, and never past either end", () => {
    expect(moved(["a", "b", "c"], "c", -1)).toEqual(["a", "c", "b"]);
    expect(moved(["a", "b", "c"], "a", 1)).toEqual(["b", "a", "c"]);
    expect(moved(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
    expect(moved(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
  });

  it("drops a dragged file at the place of the one it was dropped on", () => {
    expect(movedTo(["a", "b", "c", "d"], "d", "b")).toEqual(["a", "d", "b", "c"]);
    expect(movedTo(["a", "b", "c", "d"], "a", "c")).toEqual(["b", "c", "a", "d"]);
    expect(movedTo(["a", "b"], "a", "a")).toEqual(["a", "b"]);
    expect(movedTo(["a", "b"], "x", "a")).toEqual(["a", "b"]);
  });
});

describe("the link (the lead's flexible group)", () => {
  it("links only the ticked files, in the order shown, with the latest picked", () => {
    expect(linkBody(["x", "y", "z"], new Set(["x", "y"]), "x")).toEqual({
      document_ids: ["x", "y"],
      order: ["x", "y"],
      current_id: "x",
    });
  });

  it("takes the newest ticked file as the latest when the one picked is unticked", () => {
    expect(linkBody(["x", "y", "z"], new Set(["x", "y"]), "z")?.current_id).toBe("y");
    expect(linkBody(["x", "y", "z"], new Set(["x", "z"]), null)?.current_id).toBe("z");
  });

  it("needs 2 to 20 files", () => {
    expect(linkBody(["x", "y"], new Set(["x"]), "x")).toBeNull();
    const many = Array.from({ length: 21 }, (_, n) => `f${n}`);
    expect(linkBody(many, new Set(many), null)).toBeNull();
    expect(linkBody(many.slice(0, 20), new Set(many), null)?.document_ids).toHaveLength(20);
  });
});

describe("why each file is here, in plain words", () => {
  const reason = (over: Partial<VersionGroupReason>): VersionGroupReason => ({
    with: "x",
    text_overlap: null,
    text_overlap_estimated: false,
    name_similarity: null,
    shared_identifier: null,
    words: "",
    ...over,
  });
  const names: Record<string, string> = { x: "X", y: "Y" };
  const nameOf = (id: string) => names[id] ?? "another file";

  it("says the share of the text and the name, as the lead wrote it", () => {
    expect(reasonLine([reason({ text_overlap: 0.94, name_similarity: 1 })], nameOf)).toBe("94% same text as X · same name");
    expect(
      reasonLine([reason({ with: "y", text_overlap: 0.79 }), reason({ with: "x", text_overlap: 0.81 })], nameOf),
    ).toBe("81% same text as X · 79% as Y");
  });

  it("says about for an estimate, and the name and identifier when the text did not count", () => {
    expect(reasonLine([reason({ text_overlap: 0.9, text_overlap_estimated: true })], nameOf)).toBe(
      "about 90% same text as X",
    );
    expect(reasonLine([reason({ name_similarity: 0.7, shared_identifier: "survey_number" })], nameOf)).toBe(
      "similar name as X · same survey number",
    );
    expect(reasonLine([], nameOf)).toBe("");
  });
});

describe("the confirm step (V13 step 3, V15)", () => {
  const preview = (over: Partial<LinkPreview> = {}): LinkPreview => ({
    latest: { id: "z", name: "Lease 2026.pdf" },
    folder: { id: "f1", path: "Legal / Leases" },
    held: false,
    versions: 3,
    losing: [],
    gaining: [],
    ...over,
  });

  it("says what Ask will answer from and where the files will be kept", () => {
    expect(confirmLines(preview(), 3)).toEqual([
      "These 3 files become one file with 3 versions.",
      "Ask will answer from the latest: Lease 2026.pdf.",
      "They'll be kept in Legal / Leases.",
    ]);
  });

  it("counts every version a file brings, and says the top level or held for one person", () => {
    expect(confirmLines(preview({ versions: 5, folder: null }), 2)[0]).toBe("These 2 files become one file with 5 versions.");
    expect(confirmLines(preview({ folder: null }), 3)[2]).toBe("They'll be kept at the top level, in no folder.");
    expect(confirmLines(preview({ folder: null, held: true }), 3)[2]).toBe(
      "They'll be kept where Lease 2026.pdf is, held for one person.",
    );
  });

  it("names who would lose access", () => {
    const one = confirmLines(preview({ losing: [{ id: "p1", name: "Priya" }] }), 3);
    expect(one[3]).toBe("1 person will lose access: Priya.");
    const three = confirmLines(
      preview({ losing: [{ id: "p1", name: "Priya" }, { id: "p2", name: "Ravi" }, { id: "p3", name: "Sana" }] }),
      3,
    );
    expect(three[3]).toBe("3 people will lose access: Priya, Ravi and Sana.");
  });
});

describe("a refused link, in plain words", () => {
  it("shows the server's words, and a connection hint for anything else", () => {
    const changed = new ApiError(409, "changed", "These files changed just now. Try again.");
    expect(linkErrorText(changed)).toBe("These files changed just now. Try again.");
    expect(linkErrorText(new TypeError("fetch failed"))).toBe(LINK_TEXT.connection);
    // V-A's review fixes (#658) add these refusals: their words are the server's own.
    for (const code of ["not_ready", "name_taken", "too_many_versions"]) {
      expect(linkErrorText(new ApiError(409, code, `Words for ${code}.`))).toBe(`Words for ${code}.`);
    }
  });
});

describe("a refused Undo, in plain words (review of #659)", () => {
  const refuse = (code: string, message: string) =>
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: { code, message } }), { status: code === "password_required" ? 403 : 409 }));

  it("sends the person to the Versions tab when Undo needs their password", async () => {
    refuse("password_required", "Type your password to give yourself access.");
    const caught = await undoLink("y").catch((error: unknown) => error);
    expect(linkErrorText(caught)).toBe("To undo this, open the file and use Undo linking in its Versions tab.");
  });

  it("says the server's words for any other refusal", async () => {
    refuse("not_newest", "The versions changed since they were linked, so this can't be undone.");
    const caught = await undoLink("y").catch((error: unknown) => error);
    expect(linkErrorText(caught)).toBe("The versions changed since they were linked, so this can't be undone.");
  });

  it("is said on the document screen in those words, never a fixed connection sentence", () => {
    const view = readFileSync(join(__dirname, "..", "app/(app)/documents/[id]/DocumentView.tsx"), "utf8");
    expect(view).toContain("setToast(linkErrorText(caught))");
    expect(view).not.toContain("It could not be undone. Check your connection");
    const dialog = readFileSync(join(__dirname, "..", "components/versions/LinkDialog.tsx"), "utf8");
    expect(dialog).toContain("undo: () => undoLink(current)");
  });
});

describe("V22: the Linked banner after a reload", () => {
  it("shows Linked with Undo for the accepted suggestion the detail now returns", () => {
    const accepted = {
      id: "s1",
      role: "new",
      outcome: "suggested",
      status: "accepted",
      document: { id: "d2", name: "Lease v2.pdf" },
      candidate: { family_id: "d1", id: "d1", name: "Lease.pdf" },
    } as VersionSuggestion;
    const detail = {
      id: "d2",
      version_suggestion: accepted,
      versions: [
        { id: "d1", version_no: 1 },
        { id: "d2", version_no: 2 },
      ],
    } as unknown as DocumentDetail;
    expect(bannerState(detail)).toMatchObject({ kind: "linked", canUndo: true });
  });
});

describe("where it starts (V13): the row menu and the selection", () => {
  const row = { id: "d1", status: "ready" as const, deleted_at: null, can_delete: true, locked: false };

  it("offers Link to another version in the row menu only when the person may link", () => {
    const words = (entries: ReturnType<typeof menuEntries>) =>
      entries.map((entry) => (entry.kind === "action" ? entry.words : "Download"));
    expect(words(menuEntries(row, false, "list", false, true))).toContain("Link to another version…");
    expect(words(menuEntries(row, false))).not.toContain("Link to another version…");
    expect(words(menuEntries({ ...row, deleted_at: "2026-11-01T00:00:00Z" }, false, "list", false, true))).not.toContain(
      "Link to another version…",
    );
    expect(words(menuEntries(row, false, "document", false, true))).not.toContain("Link to another version…");
  });

  it("links the ticked files that are not locked, 2 to 20 of them", () => {
    const item = (id: string, locked = false) => ({ id, locked }) as DocumentSummary;
    const items = [item("a"), item("b"), item("c", true)];
    expect(linkableIds(items, new Set(["a", "b", "c"]))).toEqual(["a", "b"]);
    expect(linkableIds(items, new Set(["a", "c"]))).toEqual([]);
    const many = Array.from({ length: 21 }, (_, n) => item(`f${n}`));
    expect(linkableIds(many, new Set(many.map((each) => each.id)))).toEqual([]);
  });
});

describe("the library's Possible versions (N)", () => {
  const nav = (possibleVersions?: { count: number; more: boolean } | null) =>
    render(
      createElement(FolderNav, {
        tree: buildTree([]),
        library: ALL_DOCUMENTS,
        loading: false,
        error: null,
        onRetry: () => undefined,
        onNavigate: () => undefined,
        possibleVersions,
      }),
    );

  it("shows the view with its count for Admins and Management, and nothing for anyone else", () => {
    const html = nav({ count: 3, more: false });
    expect(html).toContain('href="/documents/possible-versions"');
    expect(html).toContain("Possible versions (3)");
    expect(nav({ count: 200, more: true })).toContain("Possible versions (200+)");
    expect(nav(undefined)).not.toContain("Possible versions");
    expect(nav(null)).not.toContain("Possible versions");
  });
});

const reasonWith = (other: string): VersionGroupReason => ({
  with: other,
  text_overlap: 0.94,
  text_overlap_estimated: false,
  name_similarity: 1,
  shared_identifier: null,
  words: "94% the same text",
});

const group = (over: Partial<VersionGroup> = {}): VersionGroup => ({
  id: "g1",
  files: [
    {
      id: "x",
      name: "Lease 2024.pdf",
      folder_id: null,
      doc_type: null,
      document_date: "2024-04-01",
      uploaded_at: "2026-11-01T10:00:00Z",
      page_count: 4,
      position: 1,
      state: "open",
      reasons: [reasonWith("y")],
    },
    {
      id: "y",
      name: "Lease 2025.pdf",
      folder_id: null,
      doc_type: null,
      document_date: null,
      uploaded_at: "2026-11-02T10:00:00Z",
      page_count: 5,
      position: 2,
      state: "later",
      reasons: [reasonWith("x")],
    },
  ],
  suggested_latest_id: "y",
  kept_apart: [],
  ...over,
});

describe("a group of possible versions (V17, the lead's flexible group)", () => {
  const card = (g: VersionGroup) =>
    render(createElement(GroupCard, { group: g, busy: false, onLink: () => undefined, onNoneAre: () => undefined }));

  it("ticks every file to start, with why each is there, and links the ticked ones", () => {
    const html = card(group());
    expect(html.match(/type="checkbox"[^>]*checked=""/g)).toHaveLength(2);
    expect(html).toContain("94% same text as Lease 2025.pdf · same name");
    expect(html).toContain("94% same text as Lease 2024.pdf · same name");
    expect(html).toContain("Link as versions (2)");
    expect(html).toContain("None of these are versions");
    expect(html).toContain("Left for later");
  });

  it("compares any two files of the group", () => {
    const html = card(group());
    expect(html).toContain('href="/documents/y/compare?with=x"');
    expect(html.match(/<select/g)).toHaveLength(2);
  });

  it("names no file it was not sent", () => {
    const files = group().files.map((file, at) => (at === 0 ? { ...file, reasons: [reasonWith("hidden")] } : file));
    const html = card(group({ files }));
    expect(html).toContain("94% same text as another file");
  });
});

describe("the order and the confirm steps (V13)", () => {
  const files = [
    { id: "x", name: "Lease 2024.pdf", date: "2024-04-01" },
    { id: "y", name: "Lease 2025.pdf", date: "2025-04-01" },
  ];

  it("lists oldest first with the latest picked, and arrows that stop at either end", () => {
    const html = render(
      createElement(OrderStep, { files, order: ["x", "y"], latest: "y", onOrder: () => undefined, onLatest: () => undefined }),
    );
    const radio = (value: string) => html.match(new RegExp(`<input type="radio"[^>]*value="${value}"[^>]*>`))?.[0] ?? "";
    expect(radio("y")).toContain('checked=""');
    expect(radio("x")).not.toContain("checked");
    expect(html).toContain(">This is the latest<");
    expect(html).toMatch(/aria-label="Move up: Lease 2024.pdf"[^>]*disabled=""/);
    expect(html).toMatch(/aria-label="Move down: Lease 2025.pdf"[^>]*disabled=""/);
    expect(html).toContain('draggable="true"');
  });

  it("says which file Ask answers from, where they go, who loses access, and asks for the password when told", () => {
    const preview: LinkPreview = {
      latest: { id: "y", name: "Lease 2025.pdf" },
      folder: { id: "f1", path: "Legal" },
      held: false,
      versions: 2,
      losing: [{ id: "p1", name: "Priya" }],
      gaining: [],
    };
    const html = render(createElement(ConfirmStep, { preview, files: 2, password: null, onPassword: () => undefined }));
    expect(html).toContain("Ask will answer from the latest: Lease 2025.pdf.");
    expect(html).toContain("1 person will lose access: Priya.");
    expect(html).not.toContain('type="password"');
    const asked = render(createElement(ConfirmStep, { preview, files: 2, password: "", onPassword: () => undefined }));
    expect(asked).toContain('type="password"');
  });
});

describe("the Kept apart list (V18)", () => {
  it("offers Link as versions for a pair and Undo for a file marked not versions", () => {
    const kept: KeptApart = {
      pairs: [
        { id: "s1", separated: false, decided_at: null, document: { id: "a", name: "A.pdf" }, other: { id: "b", name: "B.pdf" } },
      ],
      groups: [{ group_id: "g1", id: "c", name: "C.pdf" }],
    };
    const html = render(createElement(KeptApartList, { kept, busy: false, onLink: () => undefined, onUndo: () => undefined }));
    expect(html).toContain("&quot;A.pdf&quot; and &quot;B.pdf&quot;");
    expect(html).toContain(">Link as versions<");
    expect(html).toContain("C.pdf");
    expect(html).toContain(">Undo<");
  });
});

describe("the Versions tab (V13, V16)", () => {
  const detail = {
    id: "d1",
    family_id: "d1",
    display_name: "Lease.pdf",
    deleted_at: null,
    versions: [{ id: "d1", version_no: 1, status: "ready", is_current: true, created_at: "2025-06-12T10:00:00Z" }],
    version_link: { id: "l1", linked_at: "2026-11-16T10:00:00Z", can_undo: true },
  } as unknown as DocumentDetail;
  const panel = (canLink: boolean, over: Partial<DocumentDetail> = {}) =>
    render(createElement(VersionsPanel, { detail: { ...detail, ...over }, onChanged: () => undefined, canLink }));

  it("offers Add an older or newer version, and Undo linking while the link can be undone", () => {
    expect(panel(true)).toContain(">Add an older or newer version<");
    expect(panel(true)).toContain(">Undo linking<");
    expect(panel(true, { version_link: { id: "l1", linked_at: "2026-11-16T10:00:00Z", can_undo: false } })).not.toContain(
      "Undo linking",
    );
    expect(panel(false)).not.toContain("Add an older or newer version");
    expect(panel(false)).not.toContain("Undo linking");
    expect(panel(true, { deleted_at: "2026-11-16T10:00:00Z" })).not.toContain("Add an older or newer version");
  });
});

describe("the calls", () => {
  it("goes to V-A's and V-B's routes with the ticked files, the order, the latest and a password", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`.trim());
      return new Response("{}", { status: 200 });
    });
    const body = { document_ids: ["x", "y"], order: ["x", "y"], current_id: "y" };
    await api.listVersionGroups();
    await api.getVersionGroup("g1");
    await api.decideVersionGroup("g1", "keep-apart", ["z"]);
    await api.decideVersionGroup("g1", "keep-apart/undo", ["z"]);
    await api.decideVersionGroup("g1", "later", ["z"]);
    await api.previewLinkVersions(body);
    await api.linkVersions(body);
    await api.linkVersions(body, "secret");
    await api.undoLinkVersions("y");
    await api.undoLinkVersions("y", "secret");
    await api.listKeptApart();
    const sent = JSON.stringify(body);
    expect(calls).toEqual([
      "GET /api/version-groups",
      "GET /api/version-groups/g1",
      'POST /api/version-groups/g1/keep-apart {"document_ids":["z"]}',
      'POST /api/version-groups/g1/keep-apart/undo {"document_ids":["z"]}',
      'POST /api/version-groups/g1/later {"document_ids":["z"]}',
      `POST /api/documents/link-versions/preview ${sent}`,
      `POST /api/documents/link-versions ${sent}`,
      `POST /api/documents/link-versions ${sent.slice(0, -1)},"password":"secret"}`,
      "POST /api/documents/y/link-versions/undo",
      'POST /api/documents/y/link-versions/undo {"password":"secret"}',
      "GET /api/version-kept-apart",
    ]);
  });
});
