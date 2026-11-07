import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { askInFlight, resumable, resumedConversation, settleAsk, startAsk, stopAsk } from "@/components/chat/askStore";
import { STOPPED_LANDING_STATUS, STOPPED_STATUS } from "@/components/chat/answer";
import type { StreamEvent } from "@/lib/askStream";
import { STOP_TEXT } from "@/components/chat/QuestionBox";
import type { ChatResponse } from "@/lib/types";

// Tushar, 16 Oct 2026: a question sent, then Documents opened and Ask again, had
// vanished. The request now lives outside the chat screen.

const C = "11111111-1111-4111-8111-111111111111";
const D = "22222222-2222-4222-8222-222222222222";

function later<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const reply = (conversationId: string) => ({ conversation_id: conversationId }) as ChatResponse;

describe("the question on its way (askStore.ts)", () => {
  it("is still there after the screen that sent it is gone, and the next one gets the answer", async () => {
    const answer = later<ChatResponse>();
    startAsk("What is the total?", C, () => answer.promise);
    // The screen unmounts; nothing here belongs to it. Back on Ask (/chat):
    const picked = resumable(askInFlight(), "");
    expect(picked?.question).toBe("What is the total?");
    expect(picked?.outcome).toBeUndefined(); // still pending
    expect(resumedConversation(picked!)).toBe(C);
    answer.resolve(reply(C));
    await expect(picked!.done).resolves.toEqual(reply(C));
    expect(picked!.outcome).toEqual({ reply: reply(C) });
    settleAsk(picked!);
    expect(askInFlight()).toBeNull();
  });

  it("answered while away in a new chat: the screen opens the conversation the answer made", async () => {
    const entry = startAsk("First question", null, () => Promise.resolve(reply(D)));
    await entry.done;
    await Promise.resolve();
    expect(resumedConversation(resumable(askInFlight(), "")!)).toBe(D);
    settleAsk(entry);
  });

  it("keeps a failure, so the question is not dropped silently", async () => {
    const entry = startAsk("Q", C, () => Promise.reject(new Error("offline")));
    await entry.done.catch(() => undefined);
    expect(entry.outcome && "error" in entry.outcome).toBe(true);
    settleAsk(entry);
  });

  it("is picked up only where it belongs", () => {
    const entry = startAsk("Q", C, () => new Promise<ChatResponse>(() => undefined));
    expect(resumable(entry, `c=${C}`)).toBe(entry);
    expect(resumable(entry, `c=${D}`)).toBeNull(); // another conversation named
    expect(resumable(entry, `document=${D}`)).toBeNull(); // "Ask about this document" starts a new chat
    expect(resumable(null, "")).toBeNull();
    settleAsk(entry);
  });

  it("a newer question replaces it; settling the older one leaves the newer", () => {
    const older = startAsk("one", C, () => new Promise<ChatResponse>(() => undefined));
    const newer = startAsk("two", C, () => new Promise<ChatResponse>(() => undefined));
    settleAsk(older);
    expect(askInFlight()).toBe(newer);
    settleAsk(newer);
  });

  it("the chat screen sends through the store and picks it up at mount", () => {
    const screen = readFileSync("components/chat/ChatScreen.tsx", "utf8");
    expect(screen).toContain("startAsk(question, activeId");
    expect(screen).toContain("resumable(askInFlight(), initialSearch)");
    expect(screen).not.toMatch(/await ask\(/);
  });
});

// Stop (Tushar, 24 Oct 2026): while an answer is on its way, Send becomes Stop; pressed, the
// request is cancelled in the browser, and it ends as stopped, never as an error.
describe("stopping a question on its way", () => {
  it("cancels the request and marks it stopped", async () => {
    let seen: AbortSignal | null = null;
    const entry = startAsk("Q", C, (signal) => {
      seen = signal;
      return new Promise<ChatResponse>((_, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))));
    });
    stopAsk(entry);
    await expect(entry.done).rejects.toThrow("aborted");
    expect(entry.stopped).toBe(true);
    expect((seen as AbortSignal | null)?.aborted).toBe(true);
  });

  it("shows Stop in Send's place only while the answer is on its way, and says it stopped", () => {
    const box = readFileSync(join(__dirname, "..", "components/chat/QuestionBox.tsx"), "utf8");
    expect(box).toMatch(/\{pending && onStop \? \(\s*<button type="button" className=\{`btn \$\{styles\.send\} \$\{styles\.stop\}`\} onClick=\{onStop\}>/);
    expect(STOP_TEXT).toBe("Stop");
    const screen = readFileSync(join(__dirname, "..", "components/chat/ChatScreen.tsx"), "utf8");
    expect(screen).toContain("onStop={busy ? () => asking_.current && stopAsk(asking_.current) : undefined}");
    expect(screen).toContain("if (entry.stopped) {");
    expect(STOPPED_STATUS).toBe("Stopped. Your question is back in the box.");
    expect(STOPPED_LANDING_STATUS).toBe("Stopped. The answer will appear in this conversation.");
  });

  it("knows where a stopped answer will land once the stream said so (started)", () => {
    let send!: (event: StreamEvent) => void;
    const entry = startAsk("Q", C, (_signal, onEvent) => {
      send = onEvent;
      return new Promise<ChatResponse>(() => undefined);
    });
    expect(entry.landing).toBeNull();
    send({ type: "started", landing: { conversationId: D, questionId: "q1" } });
    expect(entry.landing).toEqual({ conversationId: D, questionId: "q1" });
    settleAsk(entry);
  });

  it("Stop with a landing opens that conversation, clears the box and fetches the answer there", () => {
    const screen = readFileSync(join(__dirname, "..", "components/chat/ChatScreen.tsx"), "utf8");
    expect(screen).toContain("if (entry.landing) {");
    expect(screen).toContain("waitForSaved(question, entry.landing, precise);");
    expect(screen).toContain("showLanding(landing);");
    expect(screen).toContain("setStatus(STOPPED_LANDING_STATUS);");
    expect(screen).toContain("savedAnswer(landing, wait.signal)"); // B2: cancelled by open, New and leaving
  });
});
