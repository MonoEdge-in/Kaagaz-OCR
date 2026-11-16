// Page turns saved for everyone (#600): usePageTurns, "Save for everyone" (SavePageTurns.tsx) and
// the photo turn menu (PageTurnMenu.tsx), rendered with react-dom in the small fake DOM
// (tests/fake-dom.ts) and pressed. A file with saved turns opens with them and drops this
// browser's own; every save is a PATCH of the changed pages only (back as scanned too, after
// asking), takes the reply as the saved set and tells the screen; a failure says why until the
// turns change; the button stays in place, disabled, with nothing to save; the menu waits while a
// save is on its way.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageTurnMenu } from "@/components/viewer/PageTurnMenu";
import { ASK_PUT_BACK, OFFLINE, PUT_BACK, SAVED, SaveForEveryone, usePageTurns } from "@/components/viewer/SavePageTurns";
import { pageTurnsKey } from "@/components/viewer/viewer";
import type { PageSummary, PageTurnSaved } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

let doc: FakeDocument;
let root: Root | null = null;
let store: Map<string, string>;
let sent: { method: string; url: string; body: unknown }[];
let savedCalls: string[];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** fetch answers with `reply`: a body, an error body (409), an Error (a lost connection), or a
 * promise of a Response still on its way. */
function serve(reply: unknown) {
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(String(init.body)) : null });
    if (reply instanceof Error) throw new TypeError("Failed to fetch");
    if (reply instanceof Promise) return reply as Promise<Response>;
    return json(reply, (reply as { error?: unknown }).error !== undefined ? 409 : 200);
  });
}

/** The hook and the button, with the turns shown and a button that turns pages as the menu does. */
function Harness({ saved, turn }: { saved: PageTurnSaved[]; turn: Map<number, number> }) {
  const turns = usePageTurns("d1", saved, "u1");
  return createElement(
    "div",
    null,
    createElement("output", { id: "shown" }, JSON.stringify([...turns.shown])),
    createElement("output", { id: "saved" }, JSON.stringify([...turns.saved])),
    createElement("button", { type: "button", onClick: () => turns.show(new Map(turn)) }, "Turn"),
    createElement(SaveForEveryone, {
      documentId: "d1",
      userId: "u1",
      turns,
      onSaved: (next: ReadonlyMap<number, number>) => savedCalls.push(JSON.stringify([...next])),
    }),
  );
}

let screen: FakeElement;
async function render(element: ReturnType<typeof createElement>) {
  screen = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(screen as unknown as Element);
  await act(async () => root!.render(element));
}
const open = (saved: PageTurnSaved[], turn: Map<number, number>) => render(createElement(Harness, { saved, turn }));
const byId = (id: string) => screen.all().find((element) => element.getAttribute("id") === id)?.textContent;
const button = (text: string) =>
  screen.all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text) ?? null;
const status = () => screen.all().find((element) => element.getAttribute("role") === "status")?.textContent;
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const press = async (element: FakeElement | null) => {
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
};

beforeEach(() => {
  doc = install();
  store = new Map();
  savedCalls = [];
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
  it("with saved turns: shows them, drops this browser's own, and keeps the Save button, disabled", async () => {
    store.set(pageTurnsKey("u1", "d1"), JSON.stringify({ 4: 2 }));
    await open([{ page_no: 2, turn: 90 }], new Map());
    expect(byId("shown")).toBe("[[1,1]]");
    expect(store.has(pageTurnsKey("u1", "d1"))).toBe(false);
    expect(button("Save for everyone")?.getAttribute("disabled")).not.toBeNull(); // nothing to save
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
  it("sends only the changed pages, takes the reply as saved, tells the screen, and stops keeping turns here", async () => {
    serve({ page_turns: [{ page_no: 1, turn: 270 }, { page_no: 2, turn: 90 }, { page_no: 9, turn: 180 }] });
    await open([{ page_no: 2, turn: 90 }], new Map([[0, 3], [1, 1]]));
    await press(button("Turn"));
    expect(button("Save for everyone")?.getAttribute("disabled")).toBeNull();
    expect(button("Save for everyone")?.getAttribute("title")).toBe(
      "Your turns are only on this computer until you save them.",
    );
    await press(button("Save for everyone"));
    expect(sent).toEqual([
      { method: "PATCH", url: "/api/documents/d1/page-turns", body: { changes: [{ page_no: 1, turn: 270 }], user_id: "u1" } },
    ]);
    expect(byId("saved")).toBe("[[0,3],[1,1],[8,2]]"); // someone else's page 9 too
    expect(byId("shown")).toBe("[[0,3],[1,1],[8,2]]");
    expect(savedCalls).toEqual(["[[0,3],[1,1],[8,2]]"]); // the screen reloads its detail
    expect(status()).toBe(SAVED);
    expect(store.size).toBe(0);
    expect(button("Save for everyone")?.getAttribute("disabled")).not.toBeNull(); // still there, for focus
  });

  it("a lost connection says so and keeps the turns; the server's refusal in its words; a new turn clears it", async () => {
    serve(new Error("down"));
    await open([], new Map([[0, 1]]));
    await press(button("Turn"));
    await press(button("Save for everyone"));
    expect(status()).toBe(OFFLINE);
    expect(byId("shown")).toBe("[[0,1]]");
    expect(savedCalls).toEqual([]);
    serve({ error: { code: "in_trash", message: "This document is in the Trash. Restore it first." } });
    await press(button("Save for everyone"));
    expect(status()).toBe("This document is in the Trash. Restore it first.");
    await press(button("Turn"));
    expect(status()).toBe("");
  });

  it("taking every turn off asks, then saves turn 0 for those pages only: never a whole-file delete", async () => {
    serve({ page_turns: [{ page_no: 7, turn: 90 }] }); // someone else's page 7, saved meanwhile, stays
    await open([{ page_no: 1, turn: 90 }, { page_no: 3, turn: 180 }], new Map());
    await press(button("Turn"));
    await press(button("Save for everyone: back as scanned"));
    expect(sent).toEqual([]);
    expect(screen.all().some((e) => e.textContent === ASK_PUT_BACK)).toBe(true);
    expect(ASK_PUT_BACK).toBe("Put these pages back as they were scanned, for everyone?");
    await press(button("Put back"));
    expect(sent).toEqual([
      {
        method: "PATCH",
        url: "/api/documents/d1/page-turns",
        body: { changes: [{ page_no: 1, turn: 0 }, { page_no: 3, turn: 0 }], user_id: "u1" },
      },
    ]);
    expect(byId("saved")).toBe("[[6,1]]");
    expect(status()).toBe(PUT_BACK);
  });
});

describe("the photo turn menu", () => {
  const photo = (page_no: number, width: number, height: number) => ({ page_no, width, height }) as PageSummary;
  const pages = [photo(1, 3000, 4000), photo(2, 4000, 3000)]; // a tall photo, then a wide one

  function Menu({ saved, of }: { saved: PageTurnSaved[]; of: PageSummary[] }) {
    const turns = usePageTurns("d1", saved, "u1");
    return createElement(
      "div",
      null,
      createElement("output", { id: "shown" }, JSON.stringify([...turns.shown])),
      createElement(PageTurnMenu, { turns, pages: of, pageNo: of.length, documentId: "d1", userId: "u1" }),
    );
  }
  const openMenu = (of: PageSummary[] = pages) => render(createElement(Menu, { saved: [], of }));
  const inGroup = (group: string, text: string) =>
    screen
      .all()
      .find((element) => element.getAttribute("aria-label") === group)
      ?.all()
      .find((element) => element.tagName === "BUTTON" && element.textContent.trim() === text) ?? null;

  it("is a dialog popover that turns this page or all of them, makes them wide, resets, and offers the save", async () => {
    await openMenu();
    const trigger = screen.all().find((e) => e.getAttribute("aria-label") === "Turn pages" && e.tagName === "BUTTON");
    expect(trigger?.getAttribute("aria-haspopup")).toBe("dialog");
    expect(screen.all().find((e) => e.getAttribute("popover") === "auto")?.getAttribute("role")).toBe("dialog");
    await press(inGroup("This page", "Turn right"));
    expect(byId("shown")).toBe("[[1,1]]"); // page 2, the page on screen
    await press(inGroup("All pages", "Turn left"));
    expect(byId("shown")).toBe("[[0,3]]");
    await press(button("Reset to original"));
    await press(button("Make all pages wide"));
    expect(byId("shown")).toBe("[[0,1]]"); // only the tall photo
    expect(button("Save for everyone")).not.toBeNull();
  });

  it("for a file of one page, shows only its own row and Reset", async () => {
    await openMenu([photo(1, 3000, 4000)]);
    expect(inGroup("This page", "Turn right")).not.toBeNull();
    expect(inGroup("All pages", "Turn left")).toBeNull();
    expect(button("Make all pages wide")).toBeNull();
    expect(button("Make all pages tall")).toBeNull();
    expect(button("Reset to original")).not.toBeNull();
  });

  it("waits while a save is on its way", async () => {
    let answer: (response: Response) => void = () => undefined;
    serve(new Promise<Response>((resolve) => (answer = resolve)));
    await openMenu();
    await press(inGroup("This page", "Turn right"));
    await press(button("Save for everyone"));
    expect(inGroup("This page", "Turn left")?.getAttribute("disabled")).not.toBeNull();
    expect(button("Reset to original")?.getAttribute("disabled")).not.toBeNull();
    await act(async () => answer(json({ page_turns: [{ page_no: 2, turn: 90 }] })));
    await settle();
    expect(inGroup("This page", "Turn left")?.getAttribute("disabled")).toBeNull();
    expect(byId("shown")).toBe("[[1,1]]");
  });
});
