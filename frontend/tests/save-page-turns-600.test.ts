// Page turns saved for everyone (#600): usePageTurns and "Save for everyone" (SavePageTurns.tsx),
// rendered with react-dom in the small fake DOM (tests/fake-dom.ts) and pressed. A file with saved
// turns opens with them and drops this browser's own; Save sends only the changed pages and takes
// the reply as the saved set; a failure says why and keeps the turns; back as scanned asks first.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OFFLINE, SAVED, SaveForEveryone, usePageTurns } from "@/components/viewer/SavePageTurns";
import { pageTurnsKey } from "@/components/viewer/viewer";
import type { PageTurnSaved } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

let doc: FakeDocument;
let root: Root | null = null;
let store: Map<string, string>;
let sent: { method: string; url: string; body: unknown }[];

/** fetch answers PATCH and DELETE with `reply` (a body, or an Error thrown as a lost connection). */
function serve(reply: unknown) {
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(String(init.body)) : null });
    if (reply instanceof Error) throw new TypeError("Failed to fetch");
    const failed = (reply as { error?: unknown }).error !== undefined;
    return new Response(JSON.stringify(reply), { status: failed ? 409 : 200, headers: { "Content-Type": "application/json" } });
  });
}

/** The hook and the button, with the turns shown and a button that turns a page as the menu does. */
function Harness({ saved, turn }: { saved: PageTurnSaved[]; turn: Map<number, number> }) {
  const turns = usePageTurns("d1", saved, "u1");
  return createElement(
    "div",
    null,
    createElement("output", { id: "shown" }, JSON.stringify([...turns.shown])),
    createElement("output", { id: "saved" }, JSON.stringify([...turns.saved])),
    createElement("button", { type: "button", onClick: () => turns.show(turn) }, "Turn"),
    createElement(SaveForEveryone, {
      documentId: "d1",
      userId: "u1",
      saved: turns.saved,
      shown: turns.shown,
      onSaved: turns.setSaved,
    }),
  );
}

let screen: FakeElement;
async function open(saved: PageTurnSaved[], turn: Map<number, number>) {
  screen = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(screen as unknown as Element);
  await act(async () => root!.render(createElement(Harness, { saved, turn })));
}
const byId = (id: string) => screen.all().find((element) => element.getAttribute("id") === id)?.textContent;
const button = (text: string) =>
  screen.all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text) ?? null;
const status = () => screen.all().find((element) => element.getAttribute("role") === "status")?.textContent;
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
};

beforeEach(() => {
  doc = install();
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.unstubAllGlobals();
});

describe("opening a file", () => {
  it("with saved turns: shows them and drops this browser's own", async () => {
    store.set(pageTurnsKey("u1", "d1"), JSON.stringify({ 4: 2 }));
    await open([{ page_no: 2, turn: 90 }], new Map());
    expect(byId("shown")).toBe("[[1,1]]");
    expect(store.has(pageTurnsKey("u1", "d1"))).toBe(false);
    expect(button("Save for everyone")).toBeNull(); // nothing to save
  });

  it("with none saved: shows this browser's own and keeps each change in it", async () => {
    store.set(pageTurnsKey("u1", "d1"), JSON.stringify({ 4: 2 }));
    await open([], new Map([[0, 3]]));
    expect(byId("shown")).toBe("[[4,2]]");
    await press(button("Turn"));
    expect(store.get(pageTurnsKey("u1", "d1"))).toBe(JSON.stringify({ 0: 3 }));
  });
});

describe("Save for everyone", () => {
  it("sends only the changed pages, takes the reply as saved, and stops keeping turns in the browser", async () => {
    serve({ page_turns: [{ page_no: 1, turn: 270 }, { page_no: 2, turn: 90 }, { page_no: 9, turn: 180 }] });
    await open([{ page_no: 2, turn: 90 }], new Map([[0, 3], [1, 1]]));
    await press(button("Turn"));
    expect(button("Save for everyone")?.getAttribute("title")).toBe(
      "Your turns are only on this computer until you save them.",
    );
    await press(button("Save for everyone"));
    expect(sent).toEqual([
      { method: "PATCH", url: "/api/documents/d1/page-turns", body: { changes: [{ page_no: 1, turn: 270 }], user_id: "u1" } },
    ]);
    expect(byId("saved")).toBe("[[0,3],[1,1],[8,2]]"); // someone else's page 9 too
    expect(status()).toBe(SAVED);
    expect(store.size).toBe(0);
  });

  it("a lost connection says so and keeps the turns on the screen; the server's refusal in its words", async () => {
    serve(new Error("down"));
    await open([], new Map([[0, 1]]));
    await press(button("Turn"));
    await press(button("Save for everyone"));
    expect(status()).toBe(OFFLINE);
    expect(byId("shown")).toBe("[[0,1]]");
    expect(button("Save for everyone")).not.toBeNull();
    serve({ error: { code: "in_trash", message: "This document is in the Trash. Restore it first." } });
    await press(button("Save for everyone"));
    expect(status()).toBe("This document is in the Trash. Restore it first.");
  });

  it("with every turn taken off saved turns: asks, then puts the pages back as scanned for everyone", async () => {
    serve({ page_turns: [] });
    await open([{ page_no: 1, turn: 90 }], new Map());
    await press(button("Turn"));
    await press(button("Save for everyone: back as scanned"));
    expect(sent).toEqual([]);
    expect(screen.all().some((e) => e.textContent === "Put every page back as it was scanned, for everyone?")).toBe(true);
    await press(button("Put back"));
    expect(sent).toEqual([{ method: "DELETE", url: "/api/documents/d1/page-turns?user_id=u1", body: null }]);
    expect(byId("saved")).toBe("[]");
  });
});
