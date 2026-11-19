// Page turns with AI (#709): the server's orientation check (POST /documents/{id}/straighten)
// combined with the turns shown (turnPagesUpright, page-turns.ts), the words said after it
// (turnedWords, SavePageTurns.tsx), and the photo turn menu (PageTurnMenu.tsx) pressed in the small
// fake DOM: "Straighten all pages with AI" turns and saves only the pages it turned for everyone,
// Undo puts them back (and saves that), an error changes nothing, a failed save keeps the turns
// here; "Make all pages wide" and "tall" use the check and stay this person's own.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { turnPagesUpright } from "@/components/extend/lib/page-turns";
import type { PageTurns } from "@/components/extend/lib/page-turns";
import { PageTurnMenu } from "@/components/viewer/PageTurnMenu";
import { turnedWords, usePageTurns } from "@/components/viewer/SavePageTurns";
import type { PageSummary } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const turns = (entries: [number, number][]) => new Map(entries) as PageTurns;
const checked = (pages: [number, number][], unsure: number[] = []) => ({
  pages: pages.map(([page_no, turn]) => ({ page_no, turn })),
  unsure,
});
const tall = { size: { width: 595, height: 842 }, rotation: 0 };
const wide = { size: { width: 842, height: 595 }, rotation: 0 };
const tallTurnedInFile = { size: { width: 595, height: 842 }, rotation: 1 }; // /Rotate 90: shown wide
const square = { size: { width: 600, height: 600 }, rotation: 0 };

describe("turnPagesUpright", () => {
  it("Straighten: each page it could tell gets its upright turn, upright ones none; unsure ones keep theirs", () => {
    const now = turns([[0, 1], [1, 3], [2, 2]]);
    expect(turnPagesUpright(now, [tall, tall, tall, tall], checked([[1, 180]], [3]))).toEqual(
      new Map([[0, 2], [2, 2]]),
    );
  });

  it("wide: a page whose writing is upright only when wide turns that way, not always right", () => {
    expect(turnPagesUpright(turns([]), [tall, tall], checked([[1, 270], [2, 90]]), "wide")).toEqual(
      new Map([[0, 3], [1, 1]]),
    );
  });

  it("wide or tall: an upside-down page of the right shape is put upright", () => {
    expect(turnPagesUpright(turns([]), [wide], checked([[1, 180]]), "wide")).toEqual(new Map([[0, 2]]));
    expect(turnPagesUpright(turns([[0, 2]]), [tall], checked([]), "tall")).toEqual(new Map());
  });

  it("a page upright in the other shape turns a quarter right of upright, its file's own turn counted", () => {
    expect(turnPagesUpright(turns([[0, 2]]), [tall], checked([]), "wide")).toEqual(new Map([[0, 1]]));
    expect(turnPagesUpright(turns([]), [tallTurnedInFile], checked([]), "tall")).toEqual(new Map([[0, 1]]));
    expect(turnPagesUpright(turns([]), [square], checked([[1, 90]]), "tall")).toEqual(new Map([[0, 1]]));
  });

  it("a page it couldn't tell gets today's shape rule", () => {
    expect(turnPagesUpright(turns([]), [tall, wide], checked([], [1, 2]), "wide")).toEqual(new Map([[0, 1]]));
    expect(turnPagesUpright(turns([[1, 2]]), [tall, wide], checked([], [1, 2]), "tall")).toEqual(
      new Map([[1, 3]]),
    );
  });
});

describe("turnedWords", () => {
  it("says what was turned, for whom, and the pages it couldn't tell", () => {
    expect(turnedWords("straighten", 3, [], null)).toBe("Turned 3 pages upright for everyone.");
    expect(turnedWords("straighten", 1, [], null)).toBe("Turned 1 page upright for everyone.");
    expect(turnedWords("straighten", 3, [], "This document is in the Trash. Restore it first.")).toBe(
      "Turned 3 pages upright for you. Couldn't save them for everyone: This document is in the Trash. Restore it first.",
    );
    expect(turnedWords("straighten", 0, [])).toBe("All pages are already upright.");
    expect(turnedWords("straighten", 0, [7])).toBe("Couldn't tell which way up for page 7.");
    expect(turnedWords("straighten", 2, [3, 7, 9], null)).toBe(
      "Turned 2 pages upright for everyone. Couldn't tell which way up for pages 3, 7 and 9.",
    );
    expect(turnedWords("straighten", 0, [1, 2, 3, 4, 5, 6])).toBe("Couldn't tell which way up for 6 pages.");
    expect(turnedWords("wide", 2, [])).toBe("Turned 2 pages.");
    expect(turnedWords("tall", 0, [])).toBe("All pages are already tall.");
  });
});

describe("the PDF viewer's turn menu", () => {
  it("offers Straighten all pages with AI above Reset, and its status where the choices are", () => {
    const viewer = readFileSync(join(__dirname, "..", "components/extend/pdf-viewer.tsx"), "utf8");
    const menu = viewer.slice(viewer.indexOf("function PDFViewerTurnMenu"), viewer.indexOf("function PDFViewerToolbar"));
    const order = ["Make all pages wide", "Make all pages tall", "Straighten all pages with AI", "Reset to original"];
    const at = order.map((label) => menu.indexOf(label));
    expect(at.every((index) => index > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(menu).toContain("turnWithAi?.status");
    expect(viewer).toContain('"ai" in change');
  });
});

// --- The photo turn menu, pressed -------------------------------------------------------------

let doc: FakeDocument;
let root: Root | null = null;
let sent: { method: string; url: string; body: unknown }[];
type Reply = unknown | Error | Promise<Response>;
let replies: Record<string, Reply>;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** fetch answers each method with its reply: a body, an error body ({status, error}), an Error (a
 * lost connection), or a promise of a Response still on its way. */
function serve(next: Record<string, Reply>) {
  replies = next;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const method = init.method ?? "GET";
    sent.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : null });
    const reply = replies[method];
    if (reply instanceof Error) throw new TypeError("Failed to fetch");
    if (reply instanceof Promise) return reply as Promise<Response>;
    const status = (reply as { status?: number }).status;
    return json(reply, status ?? 200);
  });
}

const photo = (page_no: number, width: number, height: number) => ({ page_no, width, height }) as PageSummary;
const pages = [photo(1, 3000, 4000), photo(2, 3000, 4000)]; // two tall photos

function Menu() {
  const state = usePageTurns("d1", [], "u1");
  return createElement(
    "div",
    null,
    createElement("output", { id: "shown" }, JSON.stringify([...state.shown])),
    createElement(PageTurnMenu, { turns: state, pages, pageNo: 1, documentId: "d1", userId: "u1" }),
  );
}

let screen: FakeElement;
async function openMenu() {
  screen = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(screen as unknown as Element);
  await act(async () => root!.render(createElement(Menu)));
}
const shown = () => screen.all().find((element) => element.getAttribute("id") === "shown")?.textContent;
const buttons = () => screen.all().filter((element) => element.tagName === "BUTTON");
const button = (text: string) => buttons().find((element) => element.textContent.trim() === text) ?? null;
const status = () =>
  screen
    .all()
    .filter((element) => element.getAttribute("role") === "status")
    .map((element) => element.textContent)
    .join("");
const settle = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));
const press = async (element: FakeElement | null) => {
  expect(element).not.toBeNull();
  await act(async () => void fire(element!, "click", { button: 0 }));
  await settle();
};
const STRAIGHTEN = "Straighten all pages with AI";
const LOCKED = "Type your password to open this locked file first.";

beforeEach(() => {
  doc = install();
  sent = [];
  const store = new Map<string, string>();
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

describe("Straighten all pages with AI", () => {
  it("sits above Reset, says it is checking with the choices waiting, then turns and saves only the turned pages", async () => {
    let answer: (response: Response) => void = () => undefined;
    serve({
      POST: new Promise<Response>((resolve) => (answer = resolve)),
      PATCH: { page_turns: [{ page_no: 2, turn: 180 }] },
    });
    await openMenu();
    const labels = buttons().map((element) => element.textContent.trim());
    expect(labels.indexOf(STRAIGHTEN)).toBe(labels.indexOf("Reset to original") - 1);
    await press(button(STRAIGHTEN));
    expect(status()).toBe("Checking 2 pages…");
    for (const label of [STRAIGHTEN, "Make all pages wide", "Turn left", "Reset to original"]) {
      expect(button(label)?.getAttribute("disabled")).not.toBeNull();
    }
    await act(async () => answer(json({ pages: [{ page_no: 2, turn: 180 }], unsure: [] })));
    await settle();
    expect(shown()).toBe("[[1,2]]");
    expect(sent).toEqual([
      { method: "POST", url: "/api/documents/d1/straighten", body: null },
      {
        method: "PATCH",
        url: "/api/documents/d1/page-turns",
        body: { changes: [{ page_no: 2, turn: 180 }], user_id: "u1" },
      },
    ]);
    expect(status()).toBe("Turned 1 page upright for everyone.");
    expect(button(STRAIGHTEN)?.getAttribute("disabled")).toBeNull();
    expect(button("Save for everyone")?.getAttribute("disabled")).not.toBeNull(); // nothing left to save
  });

  it("Undo puts those pages back as they were, here and for everyone; a page it couldn't tell keeps its own turn", async () => {
    serve({ POST: { pages: [{ page_no: 1, turn: 90 }], unsure: [2] }, PATCH: { page_turns: [{ page_no: 1, turn: 90 }] } });
    await openMenu();
    await press(buttons().filter((element) => element.textContent.trim() === "Turn left")[1] ?? null); // all pages
    expect(shown()).toBe("[[0,3],[1,3]]");
    await press(button(STRAIGHTEN));
    expect(shown()).toBe("[[0,1],[1,3]]"); // page 2 unsure: its turn, not saved, stays here
    serve({ PATCH: { page_turns: [{ page_no: 1, turn: 270 }] } });
    sent = [];
    await press(button("Undo"));
    expect(shown()).toBe("[[0,3],[1,3]]");
    expect(sent).toEqual([
      {
        method: "PATCH",
        url: "/api/documents/d1/page-turns",
        body: { changes: [{ page_no: 1, turn: 270 }], user_id: "u1" },
      },
    ]);
    expect(status()).toBe("Put 1 page back as it was, for everyone.");
    expect(button("Undo")).toBeNull();
  });

  it("an error says so in plain words and leaves the turns as they were", async () => {
    const message = "Kaagaz can check up to 50 pages at a time. This file has 64.";
    serve({ POST: { status: 409, error: { code: "too_many_pages", message } } });
    await openMenu();
    await press(button("Turn right"));
    await press(button(STRAIGHTEN));
    expect(shown()).toBe("[[0,1]]");
    expect(status()).toBe(`Couldn't check the pages. ${message}`);
    expect(sent.map((each) => each.method)).toEqual(["POST"]);
    serve({ POST: new Error("down") });
    await press(button(STRAIGHTEN));
    expect(status()).toBe("Couldn't check the pages. Check your connection, then try again.");
    expect(shown()).toBe("[[0,1]]");
  });

  it("a failed save keeps the turns here, says why, and leaves Save for everyone to try again", async () => {
    serve({
      POST: { pages: [{ page_no: 1, turn: 180 }, { page_no: 2, turn: 270 }], unsure: [] },
      PATCH: { status: 403, error: { code: "locked_open_required", message: LOCKED } },
    });
    await openMenu();
    await press(button(STRAIGHTEN));
    expect(shown()).toBe("[[0,2],[1,3]]");
    expect(status()).toBe(`Turned 2 pages upright for you. Couldn't save them for everyone: ${LOCKED}`);
    expect(button("Save for everyone")?.getAttribute("disabled")).toBeNull();
    sent = [];
    await press(button("Undo")); // nothing was saved, so nothing to save back
    expect(shown()).toBe("[]");
    expect(sent).toEqual([]);
    expect(status()).toBe("Put 2 pages back as they were.");
  });
});

describe("Make all pages wide and tall, with the check", () => {
  it("turn each page the way its writing reads, stay this person's own, and Undo here only", async () => {
    serve({ POST: { pages: [{ page_no: 1, turn: 270 }], unsure: [2] } });
    await openMenu();
    await press(button("Make all pages wide"));
    expect(shown()).toBe("[[0,3],[1,1]]"); // page 1 to the left, its writing upright; page 2 by its shape
    expect(sent.map((each) => each.method)).toEqual(["POST"]);
    expect(status()).toBe("Turned 2 pages. Couldn't tell which way up for page 2.");
    sent = [];
    await press(button("Undo"));
    expect(shown()).toBe("[]");
    expect(sent).toEqual([]);
  });
});
