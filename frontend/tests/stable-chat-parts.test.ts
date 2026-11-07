// Stability pass (Tushar, 7 Nov 2026), parts of Ask rendered with react-dom in the small fake DOM
// (tests/fake-dom.ts):
// - B9: screen readers hear a streamed answer once: the live region says the stages, what was
//   found and "Still looking", never the sentences (the settled card, focused, reads them);
// - B10: a sentence already shown is not drawn again while the next one is revealed, and the
//   thread follows the stream once a frame;
// - B14: a stream's error never says "Something went wrong";
// - B3: a conversation can't be renamed or deleted while an answer is being written.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamEvent } from "@/lib/askStream";
import type { ConversationSummary } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const counted = vi.hoisted(() => ({ words: [] as string[] }));
vi.mock("@/components/chat/stream", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/chat/stream")>();
  return {
    ...real,
    wordsOf: (text: string) => {
      counted.words.push(text);
      return real.wordsOf(text);
    },
  };
});

let doc: FakeDocument;
let root: Root | null = null;
let frames = new Map<number, (now: number) => void>();
let frameId = 0;

beforeEach(() => {
  doc = install();
  frames = new Map();
  counted.words = [];
  vi.useFakeTimers();
  vi.stubGlobal("requestAnimationFrame", (callback: (now: number) => void) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  Object.assign(window, {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
    matchMedia: () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }),
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function mount(element: ReturnType<typeof createElement>) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(element));
  return container;
}

async function frame(now: number) {
  const waiting = [...frames.values()];
  frames.clear();
  await act(async () => waiting.forEach((callback) => callback(now)));
}

async function streaming(...events: StreamEvent[]) {
  const { StreamingAnswer } = await import("@/components/chat/StreamingAnswer");
  const { NO_PROGRESS, progressAfter } = await import("@/components/chat/stream");
  const progress = events.reduce(progressAfter, NO_PROGRESS);
  return mount(createElement(StreamingAnswer, { progress, final: null }));
}

const live = (card: FakeElement) => card.querySelector('[aria-live="polite"]')!;
const sentence = (index: number, text: string): StreamEvent => ({ type: "sentence", sentence: { index, text, citations: [index + 1] } });

describe("B9: the live region while an answer streams", () => {
  it("says the stage and what was found, never the sentences or the opening", async () => {
    const card = await streaming(
      { type: "stage", stage: "writing" },
      { type: "sources", sources: { documents: [{ id: "d1", name: "Lease.pdf" }], passages: 4 } },
      sentence(0, "Rent is ₹12,000."),
    );
    await frame(0);
    await frame(1000);
    expect(live(card).childNodes.map((node) => node.textContent)).toEqual(["Writing the answer…", "Found 4 passages in 1 document."]);
    expect(live(card).textContent).not.toContain("Rent is");
  });

  it("says Still looking after ten quiet seconds", async () => {
    const { STILL_LOOKING } = await import("@/components/chat/StreamingAnswer");
    const card = await streaming({ type: "stage", stage: "searching" });
    await act(async () => vi.advanceTimersByTime(10_000));
    expect(live(card).textContent).toContain(STILL_LOOKING);
  });
});

describe("B10: drawing the stream", () => {
  it("does not draw a sentence already shown again while the next one is revealed", async () => {
    const card = await streaming(sentence(0, "One two."), sentence(1, "Three four five six seven eight nine ten."));
    await frame(0);
    await frame(100); // the first is whole; the second starts
    counted.words = [];
    for (const now of [110, 130, 150, 170]) await frame(now);
    expect(card.querySelector("[data-preview]")?.textContent).toContain("One two.");
    expect(counted.words.filter((text) => text === "One two.")).toEqual([]);
  });

  it("follows the stream at most once a frame, reading the thread's height in that frame only", () => {
    const screen = readFileSync(join(__dirname, "../components/chat/ChatScreen.tsx"), "utf8");
    const follow = screen.slice(screen.indexOf("const follow = () => {"), screen.indexOf("const settled = "));
    expect(follow).toMatch(/if \(followFrame\.current !== 0\) return;/);
    expect(follow).toMatch(/followFrame\.current = requestAnimationFrame\(\(\) => \{/);
    // The one read of scrollHeight is inside the frame.
    expect(follow.split("scrollHeight")).toHaveLength(2);
    expect(follow.indexOf("scrollHeight")).toBeGreaterThan(follow.indexOf("requestAnimationFrame"));
    expect(screen).toContain("cancelAnimationFrame(followFrame.current)");
  });
});

describe("B14: a stream's error in plain words", () => {
  it("never shows the server's Something went wrong", async () => {
    const { askErrorText } = await import("@/components/chat/logic");
    for (const message of ["Something went wrong.", "Something went wrong on our side."]) {
      for (const status of [400, 409, 422]) {
        const said = askErrorText({ status, code: "conflict", message });
        expect(said).not.toMatch(/something went wrong/i);
        expect(said).toBe("Could not get an answer right now. Try again in a minute.");
      }
    }
    // The server's own specific words still show.
    expect(askErrorText({ status: 400, code: "answer_timeout", message: "This answer is taking too long." })).toBe(
      "This answer is taking too long.",
    );
  });
});

describe("B3: the conversation list while an answer is being written", () => {
  const item: ConversationSummary = {
    id: "c1",
    title: "Rent for March",
    created_at: new Date().toISOString(),
  } as ConversationSummary;

  async function list(disabled: boolean) {
    const { ConversationList } = await import("@/components/chat/ConversationList");
    const removed: string[] = [];
    const container = await mount(
      createElement(ConversationList, {
        items: [item],
        failed: false,
        activeId: "c1",
        disabled,
        onOpen: () => undefined,
        onNew: () => undefined,
        onRetry: () => undefined,
        onRename: async () => true,
        onDelete: async (id: string) => {
          removed.push(id);
          return true;
        },
      }),
    );
    return { container, removed };
  }

  const menuItem = (label: string) =>
    doc.body.all().find((element) => element.getAttribute("role") === "menuitem" && element.getAttribute("aria-label") === label)!;

  it("offers Rename and Delete as not usable yet, saying why, and they do nothing", async () => {
    const { container } = await list(true);
    for (const label of ["Rename", "Delete"]) {
      const action = menuItem(label);
      expect(action.getAttribute("aria-disabled")).toBe("true");
      expect(action.textContent).toContain("Wait for the answer to finish.");
      Object.assign(action, { closest: () => null }); // the fake DOM has no popover to hide
      await act(async () => void fire(action, "click", { button: 0 }));
    }
    expect(container.querySelector("dialog")).toBeNull();
    expect(container.querySelector("form")).toBeNull();
  });

  it("offers them as before once no answer is being written", async () => {
    await list(false);
    expect(menuItem("Delete").getAttribute("aria-disabled")).toBeNull();
  });
});
