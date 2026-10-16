import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { askInFlight, resumable, resumedConversation, settleAsk, startAsk } from "@/components/chat/askStore";
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
