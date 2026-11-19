// #700: the folder's "Who can see this" button (FolderActions, beside Rename and Move) reads "Make
// private", with a short help text, for a person who may make the folder private when it is not
// private yet and nothing refuses it (privateFirst); it opens the Audience panel straight in its Make
// private step (start: "private"), with the step's preview and password step as before. On a private
// folder, or for a Head (who may only see the audience), it stays "Who can see this". No new access
// rule: abilities.ts and places.ts decide, as before. The button rendered, the panel pressed in the
// small fake DOM (tests/fake-dom.ts) against a fake fetch, and the screen's wiring read from its source.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { privateFirst } from "@/components/access/abilities";
import type { AccessAbilities } from "@/components/access/abilities";
import { AudiencePanel } from "@/components/access/AudiencePanel";
import type { FolderPlace } from "@/components/access/places";
import { FolderActions } from "@/components/documents/FolderActions";
import { buildTree } from "@/components/documents/folderTree";
import type { Folder } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const ADMIN: AccessAbilities = { audience: true, makePrivate: true, editNamed: true, giveFile: true, keptStrip: true };
const HEAD: AccessAbilities = { audience: true, makePrivate: false, editNamed: true, giveFile: false, keptStrip: false };
const FINANCE = { id: "fin", name: "Finance" };
const open: FolderPlace = { isPrivate: false, department: FINANCE, refusal: null };
const HELP = "Only the people you choose can see the files in this folder.";

describe("which folders' button reads Make private (#700)", () => {
  it("one who may make it private, on a folder not private yet: Make private", () => {
    expect(privateFirst(ADMIN, open)).toBe(true);
  });

  it("a folder already private, or one that can't be made private: Who can see this", () => {
    expect(privateFirst(ADMIN, { ...open, isPrivate: true })).toBe(false);
    expect(privateFirst(ADMIN, { ...open, refusal: "department" })).toBe(false); // a department itself
    expect(privateFirst(ADMIN, { ...open, refusal: "inside_private" })).toBe(false);
    expect(privateFirst(ADMIN, null)).toBe(false);
  });

  it("a Head, who may only see the audience: Who can see this", () => {
    expect(privateFirst(HEAD, open)).toBe(false);
  });
});

describe("the folder's button (#700)", () => {
  const folder = { id: "sal", name: "Salaries", parent_id: null, kind: "folder" } as unknown as Folder;
  const tree = buildTree([folder]);
  const html = (makePrivate: boolean) =>
    renderToStaticMarkup(
      createElement(FolderActions, {
        tree,
        current: tree.byId.get("sal") ?? null,
        onChanged: () => undefined,
        onAudience: () => undefined,
        makePrivate,
      }),
    );

  it("reads Make private, with its help text, where it opens the Make private step", () => {
    const shown = html(true);
    expect(shown).toContain(`title="${HELP}">Make private</button>`);
    expect(shown).not.toContain("Who can see this");
  });

  it("reads Who can see this otherwise, as today, with no help text", () => {
    const shown = html(false);
    expect(shown).toContain(">Who can see this</button>");
    expect(shown).not.toContain("Make private");
    expect(shown).not.toContain(HELP);
  });
});

// --- the panel, opened at its Make private step ------------------------------------------------

let doc: FakeDocument;
let root: Root | null = null;
let calls: { url: string; method: string; body: unknown }[] = [];

function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const reply = routes[`${method} ${url}`];
    if (reply === undefined) throw new Error(`no route for ${method} ${url}`);
    return new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const button = (text: string) =>
  doc.body.all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text) ?? null;

beforeEach(() => {
  doc = install();
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("Make private opens the panel at its Make private step (#700)", () => {
  async function panel(start?: "private") {
    serve({
      "GET /api/folders/sal/audience": { groups: [], everyone_else: 3 },
      "GET /api/folders/sal/private/preview?on=true": { losing: [], gaining: [], files: 3, named_before: [] },
      "PUT /api/folders/sal/private": { private: true, named: [], you_lose_access: false },
    });
    const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    const { createRoot } = await import("react-dom/client");
    root = createRoot(container as unknown as Element);
    const props = {
      target: { kind: "folder" as const, id: "sal", name: "Salaries" },
      myId: "me",
      abilities: ADMIN,
      place: open,
      start,
      onClose: () => undefined,
      onChanged: vi.fn(),
    };
    await act(async () => root!.render(createElement(AudiencePanel, props)));
    await settle();
    return props.onChanged;
  }

  it("shows the Make private step first, with its preview, and makes it private from there", async () => {
    const onChanged = await panel("private");
    expect(doc.body.textContent).toContain("Make “Salaries” private?");
    expect(calls.map((call) => `${call.method} ${call.url}`)).toContain("GET /api/folders/sal/private/preview?on=true");
    await act(async () => void fire(button("Make private")!, "click", { button: 0 }));
    await settle();
    expect(calls.find((call) => call.method === "PUT")).toEqual({
      url: "/api/folders/sal/private",
      method: "PUT",
      body: { on: true, named: [] },
    });
    expect(onChanged).toHaveBeenCalledWith("Salaries is now private.");
  });

  it("without start, opens on who can see it, as before", async () => {
    await panel();
    expect(doc.body.textContent).not.toContain("Make “Salaries” private?");
    expect(calls.map((call) => call.url)).not.toContain("/api/folders/sal/private/preview?on=true");
  });
});

describe("the documents screen's wiring (#700)", () => {
  const view = readFileSync(join(__dirname, "../components/documents/DocumentsView.tsx"), "utf8");

  it("names the button and starts the panel by privateFirst, and passes the start to the panel", () => {
    expect(view).toContain("const opensPrivate = current !== null && abilities.audience && privateFirst(abilities, place);");
    expect(view).toContain("makePrivate={opensPrivate}");
    expect(view).toContain('start: opensPrivate ? "private" : undefined');
    expect(view).toContain("start={audienceOf.start}");
  });
});
