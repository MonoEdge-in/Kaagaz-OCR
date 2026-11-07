// Stability pass (Tushar, 7 Nov 2026), the chat screen rendered with react-dom in the small fake
// DOM (tests/fake-dom.ts), its calls to the API faked:
// - B1: Stop after `started` in a new chat keeps the question on screen, with the visible line
//   "Stopped. The answer will appear in this conversation.", until the saved answer comes; the
//   welcome screen never shows again;
// - B2: the wait for that saved answer is cancelled by opening another conversation, New, or
//   leaving the screen;
// - B7: Stop works on a question picked up from before Ask was left;
// - B8: a picked-up question whose answer is still being saved opens its conversation, with the
//   words, instead of refilling the box;
// - B13: a question that took too long says Swift answers faster only when it was asked with
//   Precision.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Landing, StreamEvent } from "@/lib/askStream";
import type { Answer, ChatOptions, ChatResponse, Conversation, Message } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

const api = vi.hoisted(() => ({
  askStream: vi.fn(),
  savedAnswer: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  getChatOptions: vi.fn(),
  listDocuments: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), ...api }));
vi.mock("@/lib/aiSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/aiSettings")>()),
  useAiSettings: () => null, // unknown: nothing capped
}));
vi.mock("@/lib/storyMatches", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/storyMatches")>()),
  findStoryMatches: async () => [],
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1", role: "admin" } } }) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/chat" }));
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});
// Not under test here: drawn as nothing.
vi.mock("@/components/chat/ConversationList", () => ({ ConversationList: () => null }));
vi.mock("@/components/ui/Drawer", () => ({ Drawer: () => null }));
vi.mock("@/components/admin/Toast", () => ({ Toast: () => null }));
vi.mock("@/components/stories/StoryMatches", () => ({ StoryMatches: () => null }));
vi.mock("@/components/chat/ScopePicker", () => ({ ScopePicker: () => null }));
vi.mock("@/components/chat/ModeSwitch", () => ({ ModeSwitch: () => null }));
vi.mock("@/components/chat/AnswerSettings", () => ({ AnswerSettings: () => null }));

const WELCOME = "Get clear answers from your documents";
const STOPPED_LINE = "Stopped. The answer will appear in this conversation.";
const landing: Landing = { conversationId: "c9", questionId: "q9" };

const message = (id: string, role: "user" | "assistant", text: string, answer: Answer | null = null): Message => ({
  id,
  role,
  text,
  created_at: "2026-11-07T05:00:00Z",
  answer,
  feedback: null,
});
const answered = (texts: string[], more: Partial<Answer> = {}): Answer => ({
  status: "answered",
  label: "verified",
  sentences: texts.map((text) => ({ text, citations: [] })),
  citations: [],
  closest_documents: [],
  route: "search",
  normalized_question: "rent",
  model_used: true,
  ...more,
});
const reply = (answer: Answer, conversationId = "c9"): ChatResponse => ({
  conversation_id: conversationId,
  question: message("q9", "user", "Rent?"),
  answer: message("a9", "assistant", "", answer),
});

/** A streamed ask the test drives: its events, its reply, and Stop (the signal) as fetch fails on abort. */
interface Asked {
  send(event: StreamEvent): void;
  resolve(value: ChatResponse): void;
  reject(error: unknown): void;
  signal: AbortSignal;
  choice: unknown;
}
let asked: Asked[] = [];

let doc: FakeDocument;
let root: Root | null = null;
let replaced: string[] = [];
let stored = new Map<string, string>();

beforeEach(() => {
  doc = install();
  asked = [];
  replaced = [];
  stored = new Map();
  vi.useFakeTimers();
  const storage = {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => void stored.set(key, value),
    removeItem: (key: string) => void stored.delete(key),
  };
  Object.assign(window, {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
    matchMedia: () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }),
    history: { replaceState: (_state: unknown, _title: string, url: string) => replaced.push(url) },
    sessionStorage: storage,
    localStorage: storage,
    innerWidth: 1280,
    innerHeight: 800,
  });
  vi.stubGlobal("requestAnimationFrame", (callback: (now: number) => void) => setTimeout(() => callback(Date.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", (id: ReturnType<typeof setTimeout>) => clearTimeout(id));
  Object.assign(FakeElement.prototype, { scrollTo() {} });
  api.listConversations.mockResolvedValue({ items: [] });
  api.getChatOptions.mockResolvedValue({ agents: [], levels: [], default_level: "medium", ensemble: false, max_agents: 1 });
  api.listDocuments.mockResolvedValue({ items: [], total: 3 });
  api.askStream.mockImplementation(
    (_q: string, _c: unknown, _s: unknown, choice: unknown, _r: unknown, signal: AbortSignal, onEvent: (event: StreamEvent) => void) =>
      new Promise<ChatResponse>((resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        asked.push({ send: onEvent, resolve, reject, signal, choice });
      }),
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
  vi.resetModules(); // askStore.ts keeps the question on its way in its module
});

async function showChat(search: string) {
  const { ChatScreen } = await import("@/components/chat/ChatScreen");
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(ChatScreen, { initialSearch: search })));
  await flush();
  return container;
}

const flush = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
const text = () => doc.body.textContent;
const buttonByText = (words: string) =>
  doc.body.all().find((element) => element.tagName === "BUTTON" && element.textContent.trim() === words) ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));

describe("B1, B2: Stop after the answer started, in a new chat", () => {
  it("keeps the question with the visible line until the saved answer comes, never the welcome screen", async () => {
    let saved!: (value: ChatResponse) => void;
    api.savedAnswer.mockImplementation(() => new Promise<ChatResponse>((resolve) => (saved = resolve)));
    await showChat("?q=Rent%3F&send=1");
    expect(asked).toHaveLength(1);
    await act(async () => asked[0]!.send({ type: "started", landing }));
    await press(buttonByText("Stop"));
    await flush();
    expect(text()).not.toContain(WELCOME);
    expect(text()).toContain("Rent?");
    const line = doc.body.all().find((element) => element.textContent === STOPPED_LINE && element.getAttribute("role") !== "status");
    expect(line).toBeDefined();
    expect(line!.getAttribute("class") ?? "").not.toContain("visually-hidden");
    await act(async () => saved(reply(answered(["Rent is ₹12,000."]))));
    await flush();
    expect(text()).toContain("Rent is ₹12,000.");
    expect(doc.body.all().some((element) => element.textContent === STOPPED_LINE && element.getAttribute("role") !== "status")).toBe(false);
    expect(text()).not.toContain(WELCOME);
  });

  it("cancels the wait for the saved answer when New is pressed, and when the screen goes", async () => {
    const signals: AbortSignal[] = [];
    api.savedAnswer.mockImplementation((_landing: Landing, signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<ChatResponse>(() => undefined);
    });
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.send({ type: "started", landing }));
    await press(buttonByText("Stop"));
    await flush();
    expect(signals).toHaveLength(1);
    expect(signals[0]!.aborted).toBe(false);
    await press(buttonByText("New conversation"));
    expect(signals[0]!.aborted).toBe(true);
    expect(text()).not.toContain(STOPPED_LINE);
  });

  it("cancels the wait for the saved answer when the screen goes", async () => {
    const signals: AbortSignal[] = [];
    api.savedAnswer.mockImplementation((_landing: Landing, signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<ChatResponse>(() => undefined);
    });
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.send({ type: "started", landing }));
    await press(buttonByText("Stop"));
    await flush();
    await act(async () => root!.unmount());
    root = null;
    expect(signals[0]!.aborted).toBe(true);
  });
});

describe("B7, B8: a question picked up from before Ask was left", () => {
  async function leaveWhileAsking(conversationId: string | null) {
    const { startAsk } = await import("@/components/chat/askStore");
    let send!: (event: StreamEvent) => void;
    let fail!: (error: unknown) => void;
    const entry = startAsk("Rent?", conversationId, (signal, onEvent) => {
      send = onEvent;
      return new Promise<ChatResponse>((_resolve, reject) => {
        fail = reject;
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    });
    entry.done.catch(() => undefined);
    return { entry, send: (event: StreamEvent) => send(event), fail: (error: unknown) => fail(error) };
  }

  it("Stop stops it", async () => {
    const picked = await leaveWhileAsking(null);
    await showChat("");
    expect(buttonByText("Stop")).not.toBeNull();
    await press(buttonByText("Stop"));
    expect(picked.entry.stopped).toBe(true);
    expect(picked.entry.controller.signal.aborted).toBe(true);
  });

  it("whose answer is still being saved opens its conversation instead of refilling the box", async () => {
    const { ApiError } = await import("@/lib/api");
    api.getConversation.mockResolvedValue({ id: "c9", title: "Rent", messages: [] } as unknown as Conversation);
    const picked = await leaveWhileAsking(null);
    await showChat("");
    picked.send({ type: "started", landing });
    await act(async () => picked.fail(new ApiError(503, "answer_pending", "Your answer is still being saved. It will show in this conversation.")));
    await flush();
    expect(replaced.at(-1)).toContain("c9");
    expect(api.getConversation).toHaveBeenCalledWith("c9");
    const box = doc.body.all().find((element) => element.tagName === "TEXTAREA")!;
    expect(box.value).toBe("");
    expect(text()).toContain("Your answer is still being saved.");
  });
});

describe("B13: a question that took too long", () => {
  const options: ChatOptions = { agents: [], levels: ["medium", "high"], default_level: "medium", ensemble: false, max_agents: 1 };
  const timedOut = answered([], { version: 2, status: "not_found", label: null, outage: true, outage_reason: "timeout" } as Partial<Answer>);
  const NEUTRAL = "This question took too long for the AI. Ask about fewer documents, then try again.";

  async function askWith(level: "medium" | "high") {
    stored.set("kaagaz.ask.pick", JSON.stringify({ level }));
    api.getChatOptions.mockResolvedValue(options);
    await showChat("");
    const box = doc.body.all().find((element) => element.tagName === "TEXTAREA")!;
    box.value = "Rent?";
    await act(async () => void fire(box, "input"));
    const send = box.closest("form")!.all().find((element) => /btn-primary/.test(element.getAttribute("class") ?? ""));
    await press(send ?? null);
    await act(async () => asked[0]!.resolve(reply(timedOut)));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000))); // the card settles
  }

  it("asked with Precision, says Swift answers faster", async () => {
    await askWith("high");
    expect(asked[0]!.choice).toEqual({ thinking: "high" });
    expect(text()).toContain(`${NEUTRAL} Swift answers faster.`);
  });

  it("asked with Swift, says only the neutral sentence", async () => {
    await askWith("medium");
    expect(text()).toContain(NEUTRAL);
    expect(text()).not.toContain("Swift answers faster");
  });
});
