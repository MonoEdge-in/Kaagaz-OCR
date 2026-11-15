// PRD 7 PR 11b (#604): PR 10c's contract (#637) on the library screens. A refused move or delete
// in the server's words; a move's preview said before it is made (names, or a holder's counts
// only); a folder move or a confirmed keep-delete that opens it to the admin asks their password
// (first, when the preview says they gain it; else on 403 password_required); a keep-delete that
// changes who can see it is a warning, then "Move anyway and delete" sends confirm=true; an admin
// who moves a file where they can't see it gets `{id, hidden: true}`, and no Undo.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hiddenMovedText, moveEffectText } from "@/components/access/words";
import { MoveForm } from "@/components/documents/DocumentActions";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { FolderActions } from "@/components/documents/FolderActions";
import { buildTree } from "@/components/documents/folderTree";
import { libraryErrorText, savedToast } from "@/components/documents/library";
import { lostTheRight } from "@/components/documents/trash";
import { errorWords } from "@/lib/errorWords";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { DocumentSummary, Folder, Me } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

// The list's menu reads who is signed in: an admin session.
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    me: {
      user: {
        id: "me",
        email: "asha@example.in",
        display_name: "Asha",
        role: "admin",
        must_change_password: false,
        totp_enabled: true,
        two_step_turned_off: false,
        can_delete_forever: false,
        email_confirmed: true,
      },
      customer: { id: "c1", name: "Sharma Traders" },
      session: { scope: "admin" },
      providers: [],
    },
    refresh: () => undefined,
  }),
}));

const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-11-15T10:00:00Z",
  kind: "custom",
});
const TREE = buildTree([folder("p", "Accounts"), folder("f", "Bills", "p"), folder("s", "Suppliers")]);
const RAVI = { id: "u1", name: "Ravi" };
const ME = { id: "me", name: "Asha" };

describe("a refused move or delete", () => {
  it("is said in the server's words (ours only when it sends none)", () => {
    const words = "You can't move this here, because it would change who can see it. Ask an admin.";
    expect(libraryErrorText(new ApiError(403, "move_changes_access", "The server's own words."), "document")).toBe(
      "The server's own words.",
    );
    expect(libraryErrorText(new ApiError(403, "move_changes_access", ""), "folder")).toBe(words);
    expect(libraryErrorText(new ApiError(403, "hidden_inside", "You can't delete this folder. Ask an admin."), "folder")).toBe(
      "You can't delete this folder. Ask an admin.",
    );
    const inside = "A private folder can't go inside another private folder.";
    expect(libraryErrorText(new ApiError(409, "inside_private", inside), "folder")).toBe(inside);
  });

  it("in Trash, a folder holding what the person can't see is hidden_inside now: said, not a lost right", () => {
    const hidden = new ApiError(403, "hidden_inside", "You can't delete this folder. Ask an admin.");
    expect(lostTheRight(hidden)).toBe(false);
    expect(errorWords(hidden)).toBe("You can't delete this folder. Ask an admin.");
    expect(lostTheRight(new ApiError(403, "forbidden", "x"))).toBe(true);
  });
});

describe("a move's effect, before it is made", () => {
  it("names who loses and who gains it, or says nothing when the same people see it", () => {
    expect(moveEffectText({ losing: [RAVI], gaining: [ME], files: 3 })).toBe(
      "1 person will lose access: Ravi. 1 person will be able to see it: Asha.",
    );
    expect(moveEffectText({ losing: [], gaining: [], files: 3 })).toBe("");
  });

  it("gives the person a file is held for counts only, never a name", () => {
    expect(moveEffectText({ losing: [], gaining: [], files: 1, losing_count: 0, gaining_count: 4 })).toBe(
      "4 people will be able to see it.",
    );
    expect(moveEffectText({ losing: [], gaining: [], files: 1, losing_count: 2, gaining_count: 0 })).toBe(
      "2 people will lose access.",
    );
  });

  it("says when an admin's move can't be undone here", () => {
    expect(hiddenMovedText(1, 1)).toBe("You can't see it there, so it can't be undone here.");
    expect(hiddenMovedText(2, 5)).toBe("You can't see 2 of them there, so those can't be undone here.");
    expect(hiddenMovedText(1, 2)).toBe("You can't see 1 of them there, so that one can't be undone here.");
  });

  it("puts a move in the toast, with Undo when it has one and without when it can't be undone", () => {
    const undo = async () => undefined;
    expect(savedToast("Moved.", undo, true)).toEqual({ message: "Moved.", undo, undone: "Moved back." });
    expect(savedToast("Moved. You can't see it there, so it can't be undone here.", undefined, true)).toEqual({
      message: "Moved. You can't see it there, so it can't be undone here.",
    });
    expect(savedToast("Renamed to a.pdf.", undefined, false)).toBeNull(); // announced only
  });
});

describe("the calls", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("send PR 10c's routes what they take", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`.trim());
      return new Response('{"id": "d1", "hidden": true}', { status: 200 });
    });
    const moved = await api.moveDocument("d1", "s");
    expect(api.isHiddenMove(moved)).toBe(true);
    await api.moveDocument("d1", null);
    await api.documentMovePreview("d1", "s");
    await api.documentMovePreview("d1", null);
    await api.folderMovePreview("f", "s");
    await api.folderMovePreview("f", null);
    await api.updateFolder("f", { parent_id: "s", password: "secret" });
    await api.updateFolder("f", { parent_id: null, password: undefined });
    await api.deleteFolderWithContents("f", { contents: "keep", to: "s" });
    await api.deleteFolderWithContents("f", { contents: "keep", to: null, confirm: true, password: "secret" });
    await api.deleteFolderWithContents("f", { contents: "trash" });
    expect(calls).toEqual([
      'PATCH /api/documents/d1 {"folder_id":"s"}',
      'PATCH /api/documents/d1 {"folder_id":null}',
      "GET /api/documents/d1/move-preview?folder_id=s",
      "GET /api/documents/d1/move-preview",
      "GET /api/folders/f/move-preview?parent_id=s",
      "GET /api/folders/f/move-preview",
      'PATCH /api/folders/f {"parent_id":"s","password":"secret"}',
      'PATCH /api/folders/f {"parent_id":null}',
      "DELETE /api/folders/f?contents=keep&to=s",
      'DELETE /api/folders/f?contents=keep&confirm=true {"password":"secret"}',
      "DELETE /api/folders/f?contents=trash",
    ]);
    expect(api.isHiddenMove({ id: "d1", display_name: "a.pdf" } as unknown as DocumentSummary)).toBe(false);
  });
});

// --- the screens, pressed in the small fake DOM (tests/fake-dom.ts) ----------------------------

let doc: FakeDocument;
let root: Root | null = null;
let calls: { url: string; method: string; body: unknown }[] = [];

/** Each "METHOD /api/path" answers `{status, body}` (200 when a plain body, 204 for null). */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const route = routes[`${method} ${url}`];
    // A function answers each call afresh (a 403 the first time, say).
    const reply: unknown = typeof route === "function" ? (route as () => unknown)() : route;
    if (reply === undefined) throw new Error(`no route for ${method} ${url}`);
    if (reply === null) return new Response(null, { status: 204 });
    const { status, body } = (reply as { status?: number; body?: unknown }).status
      ? (reply as { status: number; body: unknown })
      : { status: 200, body: reply };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}
const refused = (status: number, code: string, message: string) => ({ status, body: { error: { code, message } } });

const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const all = (within: FakeElement) => within.all();
const button = (within: FakeElement, text: string) =>
  all(within).find((element) => element.tagName === "BUTTON" && [text, text + text].includes(element.textContent.trim())) ??
  null;
const radio = (within: FakeElement, text: string) => {
  const label = all(within).find(
    (element) => element.tagName === "LABEL" && element.textContent.replace(/, Level \d+$/, "").trim().startsWith(text),
  );
  return label?.all().find((element) => element.tagName === "INPUT") ?? null;
};
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
};
async function choose(element: FakeElement | null) {
  element!.checked = true;
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
}
async function submit(element: FakeElement | null) {
  const form = element!.closest("form")!;
  await act(async () => void fire(form, "submit"));
  await settle();
}

/** The open "Confirm it's you" step: type the password and press Give myself access, or Cancel. */
const passwordStep = (within: FakeElement) =>
  all(within).find((element) => element.tagName === "DIALOG" && element.textContent.includes("Confirm it's you")) ?? null;
async function typePassword(within: FakeElement, password: string) {
  const step = passwordStep(within)!;
  const box = step.all().find((element) => element.tagName === "INPUT")!;
  box.value = password;
  await act(async () => void fire(box, "input"));
  await submit(button(step, "Give myself access"));
}
const cancelStep = async (within: FakeElement) => press(button(passwordStep(within)!, "Cancel"));

async function mount(element: ReturnType<typeof createElement>) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  await settle();
  return doc.body as unknown as FakeElement;
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("a folder's Move", () => {
  const moveTo = async (routes: Record<string, unknown>) => {
    serve(routes);
    const onChanged = vi.fn();
    const screen = await mount(
      createElement(FolderActions, { tree: TREE, current: TREE.byId.get("f")!, onChanged, admin: true, myId: "me" }),
    );
    await press(button(screen, "Move this folder"));
    await choose(radio(screen, "Suppliers"));
    return { screen, onChanged };
  };

  it("says who loses and gains what it holds, and asks the password first when the admin gains it", async () => {
    const { screen, onChanged } = await moveTo({
      "GET /api/folders/f/move-preview?parent_id=s": { losing: [RAVI], gaining: [ME], files: 3 },
      "PATCH /api/folders/f": folder("f", "Bills", "s"),
    });
    expect(screen.textContent).toContain("1 person will lose access: Ravi. 1 person will be able to see it: Asha.");
    await submit(button(screen, "Move folder"));
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([]);
    expect(screen.textContent).toContain("Confirm it's you");
    await typePassword(screen, "pw-typed");
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([
      { url: "/api/folders/f", method: "PATCH", body: { parent_id: "s", password: "pw-typed" } },
    ]);
    expect(onChanged).toHaveBeenCalledWith("Moved the folder Bills to Suppliers.");
  });

  it("asks on 403 password_required when the preview didn't say, and sends nothing more on Cancel", async () => {
    const { screen, onChanged } = await moveTo({
      "GET /api/folders/f/move-preview?parent_id=s": { losing: [], gaining: [], files: 3 },
      "PATCH /api/folders/f": refused(403, "password_required", "Type your password."),
    });
    await submit(button(screen, "Move folder"));
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(1);
    expect(screen.textContent).toContain("Confirm it's you");
    await press(button(screen, "Cancel"));
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(1);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows nothing before a move the person may not make, then the server's words", async () => {
    const words = "You can't move this here, because it would change who can see it. Ask an admin.";
    const { screen } = await moveTo({
      "GET /api/folders/f/move-preview?parent_id=s": refused(403, "forbidden", "Only an admin."),
      "PATCH /api/folders/f": refused(403, "move_changes_access", words),
    });
    expect(screen.textContent).not.toContain("will lose access");
    await submit(button(screen, "Move folder"));
    expect(screen.textContent).toContain(words);
    expect(screen.textContent).not.toContain("Confirm it's you");
  });
});

describe("deleting a folder and keeping what's inside", () => {
  const WARNING = "Moving what's inside there changes who can see it. Confirm to move it, choose another folder, or send it to Trash.";

  let onChanged = vi.fn();
  async function open(routes: Record<string, unknown>) {
    serve({ "GET /api/folders/f/summary": { files: 3, folders: 0, bytes: 30 }, ...routes });
    onChanged = vi.fn();
    const screen = await mount(
      createElement(FolderActions, { tree: TREE, current: TREE.byId.get("f")!, onChanged, admin: true, myId: "me" }),
    );
    await press(button(screen, "Delete this folder"));
    return screen;
  }
  const deletes = () => calls.filter((call) => call.method === "DELETE");
  /** The confirmed keep: 403 password_required the first time, then done. */
  const confirmed = () => {
    let asked = 0;
    return () => {
      asked += 1;
      return asked === 1 ? refused(403, "password_required", "Type your password.") : { files: 3, folders: 0 };
    };
  };

  it("warns first, then Move anyway sends it again confirmed, the password typed going in its body", async () => {
    const screen = await open({
      "DELETE /api/folders/f?contents=keep&to=p": refused(409, "access_would_change", WARNING),
      "DELETE /api/folders/f?contents=keep&to=p&confirm=true": confirmed(),
    });
    await press(button(screen, "Move and delete folder"));
    expect(screen.textContent).toContain(WARNING);
    await press(button(screen, "Move anyway and delete"));
    expect(deletes().map((call) => call.url)).toEqual([
      "/api/folders/f?contents=keep&to=p",
      "/api/folders/f?contents=keep&to=p&confirm=true",
    ]);
    expect(screen.textContent).toContain("Confirm it's you");
    await typePassword(screen, "pw-typed");
    const sent = deletes()[2]!;
    expect(sent).toEqual({
      url: "/api/folders/f?contents=keep&to=p&confirm=true",
      method: "DELETE",
      body: { password: "pw-typed" },
    });
    expect(sent.url).not.toContain("pw-typed");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("cancelled at the password step: nothing more sent, nothing deleted, the warning still shown", async () => {
    const screen = await open({
      "DELETE /api/folders/f?contents=keep&to=p": refused(409, "access_would_change", WARNING),
      "DELETE /api/folders/f?contents=keep&to=p&confirm=true": refused(403, "password_required", "Type your password."),
    });
    await press(button(screen, "Move and delete folder"));
    await press(button(screen, "Move anyway and delete"));
    await cancelStep(screen);
    expect(deletes()).toHaveLength(2);
    expect(screen.textContent).toContain(WARNING);
    expect(passwordStep(screen)).toBeNull();
    expect(button(screen, "Move anyway and delete")).not.toBeNull();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("Esc in the password step closes only the step: the delete dialog stays, and nothing more is sent", async () => {
    const screen = await open({
      "DELETE /api/folders/f?contents=keep&to=p": refused(409, "access_would_change", WARNING),
      "DELETE /api/folders/f?contents=keep&to=p&confirm=true": refused(403, "password_required", "Type your password."),
    });
    await press(button(screen, "Move and delete folder"));
    await press(button(screen, "Move anyway and delete"));
    await act(async () => void fire(passwordStep(screen)!, "cancel"));
    await settle();
    expect(passwordStep(screen)).toBeNull();
    expect(button(screen, "Move anyway and delete")).not.toBeNull();
    expect(deletes()).toHaveLength(2);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("takes the warning back when another place is chosen", async () => {
    const screen = await open({
      "DELETE /api/folders/f?contents=keep&to=p": refused(409, "access_would_change", WARNING),
    });
    await press(button(screen, "Move and delete folder"));
    expect(button(screen, "Move anyway and delete")).not.toBeNull();
    await choose(radio(screen, "Top level"));
    expect(button(screen, "Move anyway and delete")).toBeNull();
    expect(button(screen, "Move and delete folder")).not.toBeNull();
    expect(screen.textContent).not.toContain(WARNING);
  });

  it("takes the warning back when Trash is chosen instead", async () => {
    const screen = await open({
      "DELETE /api/folders/f?contents=keep&to=p": refused(409, "access_would_change", WARNING),
    });
    await press(button(screen, "Move and delete folder"));
    await choose(radio(screen, "Delete the folder and everything inside"));
    expect(screen.textContent).not.toContain(WARNING);
    await choose(radio(screen, "Move what’s inside, then delete the folder"));
    expect(button(screen, "Move anyway and delete")).toBeNull();
    expect(button(screen, "Move and delete folder")).not.toBeNull();
  });
});

describe("a file's Move", () => {
  it("of several, some moved where the admin can't see them: Undo puts back only the others", async () => {
    const one = { id: "d1", display_name: "a.pdf", folder_id: "p" } as unknown as DocumentSummary;
    const two = { id: "d2", display_name: "b.pdf", folder_id: "p" } as unknown as DocumentSummary;
    serve({
      "PATCH /api/documents/d1": { id: "d1", hidden: true },
      "PATCH /api/documents/d2": { id: "d2", display_name: "b.pdf", folder_id: "s" },
    });
    const onSaved = vi.fn();
    const screen = await mount(createElement(MoveForm, { items: [one, two], tree: TREE, onSaved, onCancel: () => undefined }));
    await choose(radio(screen, "Suppliers"));
    await submit(button(screen, "Move"));
    const [message, moved, undo] = onSaved.mock.calls[0] as [string, boolean, () => Promise<void>];
    expect(message).toBe("Moved 2 documents to Suppliers. You can't see 1 of them there, so that one can't be undone here.");
    expect(moved).toBe(true);
    calls = [];
    await act(async () => undo());
    expect(calls).toEqual([{ url: "/api/documents/d2", method: "PATCH", body: { folder_id: "p" } }]);
  });

  const ITEM = { id: "d1", display_name: "deed.pdf", folder_id: "p", detected_type: "pdf" } as unknown as DocumentSummary;

  async function moveOne(routes: Record<string, unknown>) {
    serve(routes);
    const onSaved = vi.fn();
    const screen = await mount(createElement(MoveForm, { items: [ITEM], tree: TREE, onSaved, onCancel: () => undefined }));
    await choose(radio(screen, "Suppliers"));
    return { screen, onSaved };
  }

  it("tells the person it is held for how many will see it, never who", async () => {
    const { screen } = await moveOne({
      "GET /api/documents/d1/move-preview?folder_id=s": { losing: [], gaining: [], files: 1, losing_count: 0, gaining_count: 4 },
    });
    expect(screen.textContent).toContain("4 people will be able to see it.");
  });

  it("moved where the admin can't see it: said, and no Undo", async () => {
    const { screen, onSaved } = await moveOne({
      "GET /api/documents/d1/move-preview?folder_id=s": { losing: [ME], gaining: [], files: 1 },
      "PATCH /api/documents/d1": { id: "d1", hidden: true },
    });
    await submit(button(screen, "Move"));
    expect(onSaved).toHaveBeenCalledTimes(1);
    const [message, moved, undo] = onSaved.mock.calls[0] as [string, boolean, unknown];
    expect(message).toBe("Moved to Suppliers. You can't see it there, so it can't be undone here.");
    expect(moved).toBe(true);
    expect(undo).toBeUndefined();
  });
});

describe("a hidden move from the list's Move dialog", () => {
  const ADMIN: Me = {
    user: {
      id: "me",
      email: "asha@example.in",
      display_name: "Asha",
      role: "admin",
      must_change_password: false,
      totp_enabled: true,
      two_step_turned_off: false,
      can_delete_forever: false,
      email_confirmed: true,
    },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope: "admin" },
    providers: [],
  };
  const ROW = {
    id: "d1",
    display_name: "deed.pdf",
    detected_type: "pdf",
    size_bytes: 1000,
    status: "ready",
    failure_code: null,
    status_message: "Ready.",
    page_count: 1,
    created_at: "2026-11-15T10:00:00Z",
    uploaded_by_name: "Priya",
    folder_id: "p",
    tags: [],
    deleted_at: null,
    version_no: 1,
    family_id: "d1",
    can_delete: true,
    attempts: 1,
    max_attempts: 5,
    newer_version: null,
    progress: null,
    time_left: null,
    doc_type: null,
    languages: [],
    pages_unreadable: 0,
    pages_uncertain: 0,
    values_to_review: 0,
  } as unknown as DocumentSummary;

  it("reaches the screen as a move with no Undo, so it shows in the toast as the tree's drop does", async () => {
    vi.stubGlobal("self", { setTimeout, clearTimeout }); // next/link's prefetch waits on it
    serve({
      "GET /api/documents/d1/move-preview?folder_id=s": { losing: [ME], gaining: [], files: 1 },
      "PATCH /api/documents/d1": { id: "d1", hidden: true },
    });
    const onSaved = vi.fn();
    const props = {
      items: [ROW],
      inTrash: false,
      me: ADMIN,
      now: Date.parse("2026-11-15T10:00:00Z"),
      tree: TREE,
      tags: [],
      suggestions: [],
      movedLeaves: true,
      sorting: { sort: "name", order: "asc", onSort: () => undefined },
      onRemoved: () => undefined,
      onSaved,
      onFocusList: () => undefined,
      onTagCreated: () => undefined,
      onFolderCreated: () => undefined,
      onUploadVersion: () => undefined,
    } as unknown as Parameters<typeof DocumentsTable>[0];
    const screen = await mount(createElement(DocumentsTable, props));
    const labelled = (label: string) =>
      all(screen).find(
        (element) =>
          element.getAttribute("aria-label") === label || (element.tagName === "BUTTON" && element.textContent.trim() === label),
      ) ?? null;
    await press(labelled("More actions for deed.pdf"));
    await press(labelled("Move to folder"));
    await choose(radio(screen, "Suppliers"));
    await submit(button(screen, "Move"));
    expect(onSaved).toHaveBeenCalledWith(
      "Moved to Suppliers. You can't see it there, so it can't be undone here.",
      undefined,
      true,
    );
  });

  it("is toasted by the screen through savedToast (tested above), with no Undo", () => {
    const view = readFileSync(join(__dirname, "../components/documents/DocumentsView.tsx"), "utf8");
    expect(view).toContain("const toast = savedToast(message, undo, moved);");
  });
});
