// PRD 7 PR 11b (#604, review): the Audience panel pressed, in the small fake DOM (tests/fake-dom.ts)
// against a fake fetch. A Head naming themselves sends no password and is never asked for one (the
// server asks it of an Admin only); an Admin naming themselves is asked first. Make open again
// takes the server's reply as the folder's state at once, and says what changed inside the dialog.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccessAbilities } from "@/components/access/abilities";
import { AudiencePanel } from "@/components/access/AudiencePanel";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const HEAD: AccessAbilities = { audience: true, makePrivate: false, editNamed: true, giveFile: false, keptStrip: false };
const ADMIN: AccessAbilities = { audience: true, makePrivate: true, editNamed: true, giveFile: true, keptStrip: true };
const FINANCE = { id: "fin", name: "Finance" };
const NAMED = { folder: { id: "sal", name: "Salaries", path: "Finance / Salaries" }, named: [], heads: [], can_edit: true };

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
const all = (within: FakeElement) => within.all();
const button = (within: FakeElement, text: string) =>
  all(within).find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text) ?? null;
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
};
async function tick(within: FakeElement, name: string) {
  const label = all(within).find((element) => element.tagName === "LABEL" && element.textContent.trim() === name);
  const box = label!.all().find((element) => element.tagName === "INPUT")!;
  box.checked = true;
  await act(async () => void fire(box, "click", { button: 0 }));
}

async function open(abilities: AccessAbilities, routes: Record<string, unknown>) {
  serve({
    "GET /api/folders/sal/audience": { groups: [], everyone_else: 3 },
    "GET /api/folders/sal/named": NAMED,
    "GET /api/people": { items: [{ id: "me", name: "Asha" }, { id: "u2", name: "Ravi" }] },
    ...routes,
  });
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = {
    target: { kind: "folder" as const, id: "sal", name: "Salaries" },
    myId: "me",
    abilities,
    place: { isPrivate: true, department: FINANCE, refusal: null },
    onClose: () => undefined,
    onChanged: vi.fn(),
  };
  await act(async () => root!.render(createElement(AudiencePanel, props)));
  await settle();
  return { screen: doc.body as unknown as FakeElement, onChanged: props.onChanged };
}

beforeEach(() => {
  doc = install();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("naming yourself on a private folder", () => {
  it("as its Head: sent with no password, and no password step", async () => {
    const { screen } = await open(HEAD, { "PUT /api/folders/sal/named/me": { changed: true } });
    await press(button(screen, "Add people"));
    await tick(screen, "Asha");
    await press(button(screen, "Add people to “Salaries”"));
    const put = calls.find((call) => call.method === "PUT");
    expect(put).toEqual({ url: "/api/folders/sal/named/me", method: "PUT", body: {} });
    expect(screen.textContent).not.toContain("Confirm it's you");
    expect(screen.textContent).toContain("Asha can now see Salaries.");
  });

  it("as an Admin: asked for the password first, nothing sent yet", async () => {
    const { screen } = await open(ADMIN, {});
    await press(button(screen, "Add people"));
    await tick(screen, "Asha");
    await press(button(screen, "Add people to “Salaries”"));
    expect(calls.filter((call) => call.method === "PUT")).toEqual([]);
    expect(screen.textContent).toContain("Confirm it's you");
  });
});

describe("Make open again", () => {
  it("takes the server's reply as the folder's state at once, and says it inside the dialog", async () => {
    const { screen, onChanged } = await open(ADMIN, {
      "GET /api/folders/sal/private/preview?on=false": { losing: [], gaining: [], files: 3 },
      "PUT /api/folders/sal/private": { private: false, named: [], you_lose_access: false },
    });
    await press(button(screen, "Make open again")); // the panel's
    expect(screen.textContent).toContain("Everyone who can see Finance will see these 3 files.");
    await press(button(screen, "Make open again")); // the form's
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ on: false, named: [] });
    expect(onChanged).toHaveBeenCalledWith("Salaries is open again.");
    // The tree still says private (the screen hasn't reloaded); the panel goes by the reply.
    expect(button(screen, "Make private")).not.toBeNull();
    expect(screen.textContent).not.toContain("Named on this folder");
    const status = all(screen).find((element) => element.getAttribute("role") === "status" && element.textContent === "Salaries is open again.");
    expect(status).toBeDefined();
  });
});
