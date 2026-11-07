// #535 (Tushar, 7 Nov 2026), PR 1: the streamed answer on screen. The progress kept with the
// question on its way (askStore.ts), the reveal's timing (stream.ts), and the card itself
// (StreamingAnswer.tsx) rendered with react-dom in the small fake DOM (tests/fake-dom.ts),
// its frames driven by hand: real stages and sources, sentences word by word in order, done
// handing over to the final card, reduced motion showing it all at once, and the live region
// saying the stages (never the sentences: the settled card reads them, stability pass B9).
// Then how the chat screen and the answer card use them.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerView, OUTAGE_TEXT, TIMEOUT_TEXT } from "@/components/chat/AnswerView";
import { askErrorText } from "@/components/chat/logic";
import { ApiError } from "@/lib/api";
import { listenAsk, settleAsk, startAsk } from "@/components/chat/askStore";
import {
  advance,
  foundText,
  NO_PROGRESS,
  previewSentences,
  progressAfter,
  REVEAL_START,
  SETTLE_PAUSE_MS,
  STAGE_TEXT,
  wordMs,
  wordsOf,
} from "@/components/chat/stream";
import type { StreamProgress } from "@/components/chat/stream";
import { STILL_LOOKING, StreamingAnswer } from "@/components/chat/StreamingAnswer";
import type { StreamEvent } from "@/lib/askStream";
import type { Answer, ChatResponse, Message } from "@/lib/types";
import { FakeDocument, FakeElement, install } from "./fake-dom";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

const progressOf = (...events: StreamEvent[]): StreamProgress => events.reduce(progressAfter, NO_PROGRESS);
const stage = (name: "understanding" | "searching" | "writing" | "checking"): StreamEvent => ({ type: "stage", stage: name });
const sentence = (index: number, text: string, citations: number[] = []): StreamEvent => ({
  type: "sentence",
  sentence: { index, text, citations },
});
const sources = (names: string[], passages: number): StreamEvent => ({
  type: "sources",
  sources: { documents: names.map((name, i) => ({ id: `d${i}`, name })), passages },
});

function finalReply(texts: string[], overrides: Partial<Answer> = {}): ChatResponse {
  const answer: Answer = {
    status: "answered",
    label: "verified",
    sentences: texts.map((text, i) => ({ text, citations: [i + 1] })),
    citations: [],
    closest_documents: [],
    route: "search",
    normalized_question: "rent",
    model_used: true,
    ...overrides,
  };
  const message = (id: string, role: "user" | "assistant", body: Answer | null): Message => ({
    id,
    role,
    text: "",
    created_at: "2026-11-07T05:00:00Z",
    answer: body,
    feedback: null,
  });
  return { conversation_id: "c1", question: message("q1", "user", null), answer: message("a1", "assistant", answer) };
}

describe("the progress kept with the question on its way (askStore.ts, stream.ts)", () => {
  it("follows each event and tells whoever listens", async () => {
    let send!: (event: StreamEvent) => void;
    let finish!: (reply: ChatResponse) => void;
    const entry = startAsk("Rent?", null, (_signal, onEvent) => {
      send = onEvent;
      return new Promise<ChatResponse>((resolve) => (finish = resolve));
    });
    const heard = vi.fn();
    const stop = listenAsk(entry, heard);
    expect(entry.progress).toEqual(NO_PROGRESS);
    send(stage("understanding"));
    send(stage("searching"));
    send(sources(["Lease.pdf"], 4));
    send(sentence(0, "Rent is ₹12,000.", [1]));
    expect(heard).toHaveBeenCalledTimes(4);
    expect(entry.progress).toEqual({
      stage: "searching",
      reached: ["understanding", "searching"],
      sources: { documents: [{ id: "d0", name: "Lease.pdf" }], passages: 4 },
      sentences: [{ index: 0, text: "Rent is ₹12,000.", citations: [1] }],
    });
    stop();
    send(stage("writing"));
    expect(heard).toHaveBeenCalledTimes(4);
    finish(finalReply(["Rent is ₹12,000."]));
    await entry.done;
    settleAsk(entry);
  });

  it("keeps sentences in order: one out of order or sent twice is dropped (done has it if it belongs)", () => {
    const progress = progressOf(sentence(0, "One."), sentence(2, "Three."), sentence(1, "Two."), sentence(2, "Again."));
    expect(progress.sentences.map((s) => s.text)).toEqual(["One.", "Three."]);
  });

  it("lists a stage once, even when the server goes back to one (the bad-JSON fallback's checking)", () => {
    const progress = progressOf(stage("understanding"), stage("checking"), stage("writing"), stage("checking"));
    expect(progress.reached).toEqual(["understanding", "checking", "writing"]);
    expect(progress.stage).toBe("checking");
  });

  it("says what was found in plain words, singular or plural, and nothing when nothing was", () => {
    expect(foundText(progressOf(sources(["a", "b", "c"], 12)).sources)).toBe("Found 12 passages in 3 documents");
    expect(foundText(progressOf(sources(["a"], 1)).sources)).toBe("Found 1 passage in 1 document");
    expect(foundText(progressOf(sources([], 0)).sources)).toBeNull();
    expect(foundText(null)).toBeNull();
  });
});

describe("the reveal's timing (stream.ts)", () => {
  it("shows about 20ms a word, but never more than 600ms a sentence", () => {
    expect(wordMs(5)).toBe(20);
    expect(wordMs(30)).toBe(20);
    expect(wordMs(60)).toBe(10); // 60 words in 600ms
    expect(wordsOf("  Rent is ₹12,000  a month. ")).toEqual(["Rent ", "is ", "₹12,000  ", "a ", "month."]);
    expect(wordsOf("Rent is ₹12,000  a month.").join("")).toBe("Rent is ₹12,000  a month.");
  });

  it("reveals one sentence after another, the next starting where the last ended", () => {
    const counts = [3, 2];
    let reveal = advance(REVEAL_START, counts, 1000);
    expect(reveal).toEqual({ sentence: 0, words: 1, since: 1000 }); // the first word at once
    reveal = advance(reveal, counts, 1039);
    expect(reveal.words).toBe(2);
    reveal = advance(reveal, counts, 1060); // the first sentence ends at 1060: the second starts there
    expect(reveal).toEqual({ sentence: 1, words: 1, since: 1060 });
    reveal = advance(reveal, counts, 1500);
    expect(reveal).toEqual({ sentence: 2, words: 0, since: null }); // all shown; waiting for more
    // A sentence arriving later starts when it arrives.
    reveal = advance(reveal, [3, 2, 4], 9000);
    expect(reveal).toEqual({ sentence: 2, words: 1, since: 9000 });
  });

  it("finishes a long sentence in 600ms", () => {
    const at = advance(REVEAL_START, [100], 0);
    expect(advance(at, [100], 590).sentence).toBe(0);
    expect(advance(at, [100], 600).sentence).toBe(1);
  });

  it("after done: reveals the final sentences when the preview is their start, else keeps the preview and says it changed", () => {
    const streamed = progressOf(sentence(0, "Rent is ₹12,000."));
    const final = finalReply(["Rent is ₹12,000.", "It is due on the 5th."]);
    expect(previewSentences(streamed, null).sentences.map((s) => s.text)).toEqual(["Rent is ₹12,000."]);
    expect(previewSentences(streamed, final)).toMatchObject({ changed: false, sentences: [{ text: "Rent is ₹12,000." }, { text: "It is due on the 5th." }] });
    // A fixed calculation streams no sentence: all of the final ones reveal.
    expect(previewSentences(NO_PROGRESS, final).sentences).toHaveLength(2);
    // The final answer dropped the streamed sentence: the card settles at once, faded.
    expect(previewSentences(streamed, finalReply(["It is due on the 5th."])).changed).toBe(true);
    // Not found, small talk: their card shows no sentences to reveal.
    expect(previewSentences(NO_PROGRESS, finalReply([], { status: "not_found" }))).toEqual({ sentences: [], changed: false });
    expect(previewSentences(NO_PROGRESS, finalReply(["Hello!"], { route: "small_talk" })).sentences).toEqual([]);
  });
});

// --- The card, rendered ------------------------------------------------------------

let doc: FakeDocument;
let root: Root | null = null;
let frames = new Map<number, (now: number) => void>();
let frameId = 0;
let reduced = false;

beforeEach(() => {
  doc = install();
  frames = new Map();
  reduced = false;
  vi.useFakeTimers();
  vi.stubGlobal("requestAnimationFrame", (callback: (now: number) => void) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  Object.assign(window, {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    matchMedia: (query: string) => ({
      matches: query.includes("reduce") && reduced,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }),
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type Props = Parameters<typeof StreamingAnswer>[0];

async function show(props: Props) {
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  await act(async () => root!.render(createElement(StreamingAnswer, props)));
  return container;
}

const update = (props: Props) => act(async () => root!.render(createElement(StreamingAnswer, props)));

/** Run the frames waiting, at time `now` (they may ask for more). */
async function frame(now: number) {
  const waiting = [...frames.values()];
  frames.clear();
  await act(async () => waiting.forEach((callback) => callback(now)));
}

const find = (within: FakeElement, selector: string) => within.querySelector(selector);
const preview = (card: FakeElement) => find(card, "[data-preview]")?.textContent ?? "";
const live = (card: FakeElement) => find(card, '[aria-live="polite"]')!;
const stages = (card: FakeElement) =>
  card.all().filter((element) => element.getAttribute("data-state") !== null).map((element) => [element.textContent, element.getAttribute("data-state")]);

describe("the streamed answer card (StreamingAnswer.tsx)", () => {
  it("before any event: the server's first stage, running; no skeleton, no made-up text", async () => {
    const card = await show({ progress: NO_PROGRESS, final: null });
    expect(stages(card)).toEqual([["Understanding your question…", "active"]]);
    expect(find(card, "[data-preview]")).toBeNull();
    expect(card.all().some((element) => /skeleton/.test(element.getAttribute("class") ?? ""))).toBe(false);
  });

  it("lists the real stages: a check on each one done, the pulse on the one running", async () => {
    const card = await show({ progress: progressOf(stage("understanding"), stage("searching"), stage("writing")), final: null });
    expect(stages(card)).toEqual([
      ["Understanding your question…", "done"],
      ["Searching your documents…", "done"],
      ["Writing the answer…", "active"],
    ]);
    expect(Object.values(STAGE_TEXT)).toEqual([
      "Understanding your question…",
      "Searching your documents…",
      "Writing the answer…",
      "Checking the answer…",
    ]);
    // The done ones carry the small check; the running one none (its pulse is CSS).
    const hasCheck = (li: FakeElement) => li.all().some((element) => element.tagName === "svg");
    const items = card.all().filter((element) => element.getAttribute("data-state") !== null);
    expect(items.map(hasCheck)).toEqual([true, true, false]);
  });

  it("shows what was found and the documents' names: at most 6, then +N more", async () => {
    const names = ["Lease.pdf", "दस्त १२.pdf", "Bill 1.pdf", "Bill 2.pdf", "Bill 3.pdf", "Bill 4.pdf", "Bill 5.pdf", "Bill 6.pdf"];
    const card = await show({ progress: progressOf(stage("searching"), sources(names, 31)), final: null });
    expect(card.textContent).toContain("Found 31 passages in 8 documents");
    const chips = card.all().filter((element) => element.tagName === "LI" && /foundChip/.test(element.getAttribute("class") ?? ""));
    expect(chips.map((chip) => chip.textContent)).toEqual([...names.slice(0, 6), "+2 more"]);
    expect(live(card).textContent).toContain("Found 31 passages in 8 documents.");
  });

  it("reveals each sentence word by word, in order, its citation number landing at its end", async () => {
    const progress = progressOf(stage("writing"), sentence(0, "Rent is ₹12,000.", [1]), sentence(1, "Due on the 5th.", [2]));
    const card = await show({ progress, final: null });
    await frame(1000);
    expect(preview(card)).toBe("Rent ");
    await frame(1020);
    expect(preview(card)).toBe("Rent is ");
    await frame(1040); // the last word, and its number lands with it
    expect(preview(card)).toBe("Rent is ₹12,000.1");
    await frame(1060); // the second begins where the first ended
    expect(preview(card)).toBe("Rent is ₹12,000.1 Due ");
    await frame(5000);
    expect(preview(card)).toBe("Rent is ₹12,000.1 Due on the 5th.2");
    expect(frames.size).toBe(0); // nothing left to reveal: no frame asked for
  });

  it("queues a sentence that arrives mid-reveal after the one revealing", async () => {
    let progress = progressOf(sentence(0, "One two three four.", [1]));
    const card = await show({ progress, final: null });
    await frame(0);
    progress = progressAfter(progress, sentence(1, "Five.", [2]));
    await update({ progress, final: null });
    await frame(30);
    expect(preview(card)).toBe("One two ");
    await frame(80);
    expect(preview(card)).toBe("One two three four.1 Five.2");
  });

  it("with reduced motion, shows every sentence at once, with no frame loop", async () => {
    reduced = true;
    const progress = progressOf(sentence(0, "Rent is ₹12,000.", [1]), sentence(1, "Due on the 5th.", [2]));
    const card = await show({ progress, final: null });
    expect(preview(card)).toBe("Rent is ₹12,000.1 Due on the 5th.2");
    expect(frames.size).toBe(0);
  });

  it("says the stage in a polite live region, never the sentences; the moving words are hidden from it", async () => {
    let progress = progressOf(stage("writing"), sentence(0, "Rent is ₹12,000.", [1]));
    const card = await show({ progress, final: null });
    await frame(0);
    expect(find(card, "[data-preview]")?.getAttribute("aria-hidden")).toBe("true");
    const region = live(card);
    expect(region.childNodes.map((p) => p.textContent)).toEqual(["Writing the answer…"]);
    progress = progressAfter(progressAfter(progress, stage("checking")), sentence(1, "Due on the 5th."));
    await update({ progress, final: null });
    await frame(10);
    await frame(20);
    // B9: the answer is heard once, from the settled card, which takes focus.
    expect(live(card).childNodes.map((p) => p.textContent)).toEqual(["Checking the answer…"]);
  });

  it("on done: finishes the reveal, every stage checked, then a beat, then settles into the final card", async () => {
    const onSettle = vi.fn();
    const progress = progressOf(stage("checking"), sentence(0, "Rent is ₹12,000.", [1]));
    const card = await show({ progress, final: null, onSettle });
    await frame(0);
    await frame(100);
    const final = finalReply(["Rent is ₹12,000.", "Due on the 5th."]);
    await update({ progress, final, onSettle });
    expect(stages(card).every(([, state]) => state === "done")).toBe(true);
    await frame(200);
    await frame(400);
    expect(preview(card)).toBe("Rent is ₹12,000.1 Due on the 5th.2");
    expect(onSettle).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(SETTLE_PAUSE_MS));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("a fixed calculation (stages, then done): reveals the final sentences with the same animation", async () => {
    const onSettle = vi.fn();
    const progress = progressOf(stage("understanding"), stage("searching"));
    const card = await show({ progress, final: finalReply(["The total is ₹3,540."]), onSettle });
    await frame(0);
    expect(preview(card)).toBe("The ");
    await frame(500);
    expect(preview(card)).toBe("The total is ₹3,540.1");
    await act(async () => vi.advanceTimersByTime(SETTLE_PAUSE_MS));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("puts a v2 answer's opening line over the sentences it reveals, so the settled card does not move", async () => {
    const final = finalReply(["The total is ₹3,540."], { version: 2, opening: "Here's what I found." });
    const card = await show({ progress: NO_PROGRESS, final });
    await frame(0);
    expect(card.textContent).toMatch(/^Here's what I found\.The /);
    expect(live(card).textContent).not.toContain("Here's what I found."); // B9: the settled card says it
  });

  it("settles at once, as changed, when the final answer dropped a streamed sentence", async () => {
    const onSettle = vi.fn();
    const progress = progressOf(sentence(0, "Rent is ₹12,000.", [1]));
    await show({ progress, final: finalReply(["Due on the 5th."]), onSettle });
    await act(async () => vi.advanceTimersByTime(0));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("done alone (a Home ready answer, small talk): nothing to reveal, it settles at once", async () => {
    const onSettle = vi.fn();
    const card = await show({ progress: NO_PROGRESS, final: finalReply(["Hello!"], { route: "small_talk" }), onSettle });
    await act(async () => vi.advanceTimersByTime(0));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(false);
    expect(find(card, "[data-preview]")).toBeNull();
  });

  it("an ensemble or quoted answer (no sentence events): its final sentences reveal, then it settles", async () => {
    const onSettle = vi.fn();
    const final = finalReply(["Rent is ₹12,000."], { model_used: false });
    const card = await show({ progress: progressOf(stage("understanding"), stage("searching")), final, onSettle });
    await frame(0);
    await frame(500);
    expect(preview(card)).toBe("Rent is ₹12,000.1");
    await act(async () => vi.advanceTimersByTime(SETTLE_PAUSE_MS));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("settles with no pause under reduced motion", async () => {
    reduced = true;
    const onSettle = vi.fn();
    await show({ progress: NO_PROGRESS, final: finalReply(["The total is ₹3,540."]), onSettle });
    await act(async () => vi.advanceTimersByTime(0));
    expect(onSettle).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("after ten quiet seconds, says a long register takes longer (no sentence yet)", async () => {
    const card = await show({ progress: progressOf(stage("writing")), final: null });
    expect(card.textContent).not.toContain(STILL_LOOKING);
    await act(async () => vi.advanceTimersByTime(10_000));
    expect(card.textContent).toContain(STILL_LOOKING);
  });

  it("tells the screen it grew, so the thread can follow", async () => {
    const onGrow = vi.fn();
    const progress = progressOf(sentence(0, "Rent is ₹12,000.", [1]));
    await show({ progress, final: null, onGrow });
    const before = onGrow.mock.calls.length;
    await frame(0);
    await frame(100);
    expect(onGrow.mock.calls.length).toBeGreaterThan(before);
  });
});

describe("the chat screen and the final card", () => {
  const screen = read("components/chat/ChatScreen.tsx");

  it("asks through the stream, kept in askStore, and draws the streamed card in the old spinner's place", () => {
    expect(screen).toContain("startAsk(question, activeId, (signal, onEvent) =>");
    expect(screen).toMatch(/askStream\(\s*question,[\s\S]{0,200}signal,\s*onEvent,\s*\)/);
    expect(screen).toContain("<StreamingAnswer");
    expect(screen).not.toMatch(/skeletonLine|PendingAnswer/);
  });

  it("holds the final answer until the reveal settles, and Stop skips the wait", () => {
    expect(screen).toMatch(/const reply = await entry\.done;[\s\S]{0,200}await settled\(reply, entry\.controller\.signal\);/);
    expect(screen).toContain("onStop={busy ? () => asking_.current && stopAsk(asking_.current) : undefined}");
  });

  it("follows the stream only while the person is at the bottom", () => {
    expect(screen).toMatch(/if \(!nearBottom\.current \|\| !thread\) return;\s*thread\.scrollTop = thread\.scrollHeight;/);
  });

  it("the final card settles in place of the preview: no rise, the label and the rest fade in", () => {
    const message: Message = {
      id: "a1",
      role: "assistant",
      text: "",
      created_at: "2026-11-07T05:00:00Z",
      answer: finalReply(["Rent is ₹12,000."], { version: 2 }).answer.answer,
      feedback: null,
    };
    const card = (settle?: "same" | "changed") =>
      renderToStaticMarkup(createElement(AnswerView, { message, question: "Rent?", settle }));
    expect(card()).toMatch(/^<div id="answer-a1" tabindex="-1" class="motion-rise /);
    expect(card("same")).toMatch(/^<div id="answer-a1" tabindex="-1" class="[^"]*settled[^"]*"/);
    expect(card("same")).not.toContain("motion-rise");
    expect(card("changed")).toMatch(/^<div id="answer-a1" tabindex="-1" class="[^"]*settledChanged[^"]*"/);
    // The same words as today's card, the Verified label beside the figure.
    expect(card("same")).toContain("Rent is ₹12,000.");
    expect(card("same")).toContain("label-mark-verified");
  });

  it("a done reply whose outage is a timeout settles into TIMEOUT_TEXT; the stream's own cap keeps its words", () => {
    const timedOut = { version: 2, status: "not_found", label: null, outage: true, outage_reason: "timeout" } as const;
    const answer = finalReply([], timedOut).answer.answer;
    const message: Message = {
      id: "a1",
      role: "assistant",
      text: "",
      created_at: "2026-11-07T05:00:00Z",
      answer,
      feedback: null,
    };
    const card = renderToStaticMarkup(createElement(AnswerView, { message, question: "Rent?", settle: "same" }));
    expect(card).toContain(TIMEOUT_TEXT);
    expect(card).not.toContain(OUTAGE_TEXT);
    // An `error` answer_timeout event is not a reply: it shows the server's words as they are.
    const capped = new ApiError(400, "answer_timeout", "This answer is taking too long. Try a narrower question or Swift.");
    expect(askErrorText(capped)).toBe("This answer is taking too long. Try a narrower question or Swift.");
  });
});

