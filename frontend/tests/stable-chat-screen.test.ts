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
// - Ask hotfix (production, 15 Nov 2026): a question the service could not finish (503) is sent
//   once more after 2 s; failing again, it says sorry in the answer's place with Try again, never
//   the red "Kaagaz is busy".

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
  listFolders: vi.fn(),
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
// Changed on purpose (Ask hotfix, 15 Nov 2026): a name, for the note of a question not finished.
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: { user: { id: "u1", role: "admin", display_name: "Priya Shah" } } }),
}));
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
// #725: drawn as nothing, its state kept to read the scope the screen opened with.
const picker = vi.hoisted(() => ({ state: null as unknown }));
vi.mock("@/components/chat/ScopePicker", () => ({
  ScopePicker: ({ state }: { state: unknown }) => {
    picker.state = state;
    return null;
  },
}));
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
  api.listFolders.mockResolvedValue({ items: [] });
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
const log = () => doc.body.all().find((element) => element.getAttribute("role") === "log")!.textContent;
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

// --- The reviews' findings ---

describe("Stop on a picked-up question shows what Stop shows, never an error (review)", () => {
  async function pickUp() {
    const { startAsk } = await import("@/components/chat/askStore");
    let send!: (event: StreamEvent) => void;
    const entry = startAsk("Rent?", null, (signal, onEvent) => {
      send = onEvent;
      return new Promise<ChatResponse>((_resolve, reject) =>
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))),
      );
    });
    entry.done.catch(() => undefined);
    return { entry, send: (event: StreamEvent) => send(event) };
  }

  it("before the answer started: the question is back in the box, with no error", async () => {
    await pickUp();
    await showChat("");
    await press(buttonByText("Stop"));
    await flush();
    expect(text()).not.toContain("Could not reach Kaagaz");
    expect(doc.body.all().find((element) => element.tagName === "TEXTAREA")!.value).toBe("Rent?");
  });

  it("after it started: the question stays with the stopped line until the saved answer comes", async () => {
    api.savedAnswer.mockImplementation(() => new Promise<ChatResponse>(() => undefined));
    const picked = await pickUp();
    await showChat("");
    picked.send({ type: "started", landing });
    await press(buttonByText("Stop"));
    await flush();
    expect(text()).not.toContain("Could not reach Kaagaz");
    expect(text()).toContain("Rent?");
    expect(text()).toContain(STOPPED_LINE);
    expect(text()).not.toContain(WELCOME);
    expect(api.savedAnswer).toHaveBeenCalledWith(landing, expect.anything());
  });
});

describe("after Stop, the stopped question's answer is never lost (review)", () => {
  async function typeAndSend(words: string) {
    const box = doc.body.all().find((element) => element.tagName === "TEXTAREA")!;
    box.value = words;
    await act(async () => void fire(box, "input"));
    const send = box.closest("form")!.all().find((element) => /btn-primary/.test(element.getAttribute("class") ?? ""));
    await press(send ?? null);
  }

  it("a question asked meanwhile keeps the wait, and the answer lands under its own question", async () => {
    let saved!: (value: ChatResponse) => void;
    api.savedAnswer.mockImplementation(
      (_landing: Landing, signal: AbortSignal) =>
        new Promise<ChatResponse>((resolve, reject) => {
          saved = resolve;
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.send({ type: "started", landing }));
    await press(buttonByText("Stop"));
    await flush();
    await typeAndSend("Deposit?");
    const second: ChatResponse = {
      conversation_id: "c9",
      question: message("q10", "user", "Deposit?"),
      answer: message("a10", "assistant", "", answered(["The deposit is ₹50,000."])),
    };
    await act(async () => asked[1]!.resolve(second));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));
    await act(async () => saved(reply(answered(["Rent is ₹12,000."]))));
    await flush();
    const all = log();
    expect(all).toContain("The deposit is ₹50,000.");
    expect(all).toContain("Rent is ₹12,000.");
    expect(all.indexOf("Rent?")).toBeLessThan(all.indexOf("Rent is ₹12,000."));
    expect(all.indexOf("Rent is ₹12,000.")).toBeLessThan(all.indexOf("Deposit?"));
    expect(all).not.toContain(STOPPED_LINE);
  });

  it("if the saved answer never comes, the question stays and the box says why: no welcome screen", async () => {
    const { ApiError } = await import("@/lib/api");
    let fail!: (error: unknown) => void;
    api.savedAnswer.mockImplementation(() => new Promise<ChatResponse>((_resolve, reject) => (fail = reject)));
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.send({ type: "started", landing }));
    await press(buttonByText("Stop"));
    await flush();
    await act(async () => fail(new ApiError(503, "answer_pending", "Your answer is still being saved.")));
    await flush();
    expect(text()).not.toContain(WELCOME);
    expect(log()).toContain("Rent?");
    expect(text()).toContain("Your answer is still being saved.");
    expect(text()).not.toContain(STOPPED_LINE); // nor in the status line
  });

  it("a picked-up question whose conversation is not there after all goes back in the box", async () => {
    const { ApiError } = await import("@/lib/api");
    api.getConversation.mockRejectedValue(new ApiError(404, "not_found", "We couldn't find that."));
    const { startAsk } = await import("@/components/chat/askStore");
    let send!: (event: StreamEvent) => void;
    let fail!: (error: unknown) => void;
    const entry = startAsk("Rent?", null, (_signal, onEvent) => {
      send = onEvent;
      return new Promise<ChatResponse>((_resolve, reject) => (fail = reject));
    });
    entry.done.catch(() => undefined);
    await showChat("");
    send({ type: "started", landing });
    await act(async () => fail(new ApiError(503, "answer_pending", "Your answer is still being saved.")));
    await flush();
    expect(doc.body.all().find((element) => element.tagName === "TEXTAREA")!.value).toBe("Rent?");
  });

  it("puts a saved answer in its question's place, before anything asked since (withSavedAnswer)", async () => {
    const { stoppedQuestion, withSavedAnswer } = await import("@/components/chat/logic");
    const later = [message("q10", "user", "Deposit?"), message("a10", "assistant", "")];
    const thread = [stoppedQuestion("q9", "Rent?"), ...later];
    const saved = reply(answered(["Rent is ₹12,000."]));
    expect(withSavedAnswer(thread, saved).map((each) => each.id)).toEqual(["q9", "a9", "q10", "a10"]);
    expect(withSavedAnswer(later, saved).map((each) => each.id)).toEqual(["q10", "a10", "q9", "a9"]);
    const once = withSavedAnswer(thread, saved);
    expect(withSavedAnswer(once, saved).map((each) => each.id)).toEqual(["q9", "a9", "q10", "a10"]);
  });
});

describe("Ask hotfix: a question the service could not finish", () => {
  const SORRY = "Sorry, Priya, I couldn't finish that just now. Please try again in a moment.";
  const unfinished = async () => new (await import("@/lib/api")).ApiError(503, "backend_unavailable", "x");
  const alerts = () => doc.body.all().filter((element) => element.getAttribute("role") === "alert");

  it("is sent once more after 2 s, still looking through the documents, and its answer shows", async () => {
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(await unfinished()));
    await flush();
    expect(asked).toHaveLength(1);
    expect(log()).toContain("Rent?"); // the pending card stays
    expect(text()).not.toContain(SORRY);
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));
    expect(asked).toHaveLength(2);
    // Review: one question id for both tries, so a first try the server saved answers the second.
    const [first, second] = api.askStream.mock.calls.map((call) => call[7] as unknown);
    expect(typeof first).toBe("string");
    expect(second).toBe(first);
    await act(async () => asked[1]!.resolve(reply(answered(["Rent is ₹12,000."]))));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000))); // the card settles
    expect(text()).toContain("Rent is ₹12,000.");
    expect(text()).not.toContain(SORRY);
  });

  it("failing again, says sorry in the answer's place with Try again, never busy, error or server", async () => {
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(await unfinished()));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));
    await act(async () => asked[1]!.reject(await unfinished()));
    await flush();

    const note = doc.body.all().find((element) => element.tagName === "P" && element.textContent === SORRY);
    expect(note).toBeDefined();
    expect(log()).toContain("Rent?");
    expect(log()).toContain(SORRY); // in the thread, where the answer would be
    expect(note!.getAttribute("class") ?? "").not.toContain("error");
    expect(alerts().map((element) => element.textContent.trim()).filter(Boolean)).toEqual([]);
    expect(text()).not.toMatch(/busy|error|server/i);
    expect(doc.body.all().find((element) => element.tagName === "TEXTAREA")!.value).toBe("Rent?");
    // Principal review: the hidden status line says it too, for a screen reader.
    const status = doc.body.all().find((element) => element.getAttribute("role") === "status");
    expect(status!.textContent).toBe(SORRY);

    await press(buttonByText("Try again"));
    await flush();
    expect(asked).toHaveLength(3);
    expect(api.askStream.mock.calls[2]![0]).toBe("Rent?");
    expect(text()).not.toContain(SORRY);
  });

  it("Stop during the wait: no second try, the stopped line, and the question back in the box", async () => {
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(await unfinished()));
    await flush();
    await press(buttonByText("Stop"));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));

    expect(asked).toHaveLength(1);
    expect(text()).toContain("Stopped. Your question is back in the box.");
    expect(text()).not.toContain(SORRY);
    expect(doc.body.all().find((element) => element.tagName === "TEXTAREA")!.value).toBe("Rent?");
  });

  it("a 429 is not retried and says so", async () => {
    const { ApiError } = await import("@/lib/api");
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(new ApiError(429, "rate_limited", "Too many.")));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));

    expect(asked).toHaveLength(1);
    expect(text()).toContain("Too many questions at once. Wait a minute, then try again.");
    expect(text()).not.toContain(SORRY);
  });

  it("leaving the screen during the wait sends no second try", async () => {
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(await unfinished()));
    await flush();
    await act(async () => root!.unmount());
    root = null;
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));

    expect(asked).toHaveLength(1);
  });

  it("is not sent again while its answer is still being saved", async () => {
    const { ApiError } = await import("@/lib/api");
    await showChat("?q=Rent%3F&send=1");
    await act(async () => asked[0]!.reject(new ApiError(503, "answer_pending", "Your answer is still being saved.")));
    await act(async () => void (await vi.advanceTimersByTimeAsync(2000)));
    expect(asked).toHaveLength(1);
    expect(text()).toContain("Your answer is still being saved.");
  });
});

describe("#725: Ask in this folder", () => {
  const FOLDER_ID = "0b6f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";

  it("opens limited to the folder and its subfolders, sent with the next question", async () => {
    api.listFolders.mockResolvedValue({ items: [{ id: FOLDER_ID, name: "Land records", parent_id: null, created_at: "2026-11-01" }] });
    await showChat(`?folder=${FOLDER_ID}`);
    expect(picker.state).toMatchObject({ scope: { kind: "folder", folderId: FOLDER_ID, subfolders: true }, changed: true });
  });

  it("asks in every document instead when the folder is not one this person can see", async () => {
    api.listFolders.mockResolvedValue({ items: [] }); // hidden, gone, or another organisation's
    await showChat(`?folder=${FOLDER_ID}`);
    expect(picker.state).toMatchObject({ scope: { kind: "all" }, changed: false });
    expect(text()).not.toContain(FOLDER_ID);
  });

  it("never reads a malformed folder id", async () => {
    await showChat("?folder=not-an-id");
    expect(picker.state).toMatchObject({ scope: { kind: "all" } });
    expect(api.listFolders).not.toHaveBeenCalled();
  });
});
