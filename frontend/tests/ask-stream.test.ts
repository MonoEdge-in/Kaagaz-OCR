// #535 (Tushar, 7 Nov 2026), PR 1: Ask streams its answer from POST /api/chat/stream. The
// event stream read from fetch's body (split anywhere, even inside a character), the events
// checked before anything shows, and the fallback: any network or stream failure before
// `done` asks POST /api/chat once instead, with no error when that works. Stop aborts both.

import { afterEach, describe, expect, it, vi } from "vitest";
import { askErrorText } from "@/components/chat/logic";
import { ApiError, askStream, IDLE_MS, RECOVER_FOR_MS, RECOVER_STEPS_MS, STILL_SAVING } from "@/lib/api";
import { sseParser, streamEvent } from "@/lib/askStream";
import type { StreamEvent } from "@/lib/askStream";
import type { ChatResponse } from "@/lib/types";

afterEach(() => vi.unstubAllGlobals());

const REPLY = {
  conversation_id: "c1",
  question: { id: "q1", role: "user", text: "Total?", created_at: "2026-11-07T05:00:00Z", answer: null, feedback: null },
  answer: { id: "a1", role: "assistant", text: "", created_at: "2026-11-07T05:00:01Z", answer: null, feedback: null },
} as unknown as ChatResponse;

const sse = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const FULL = [
  ": ping\n\n",
  sse("stage", { stage: "understanding" }),
  sse("stage", { stage: "searching" }),
  sse("sources", { documents: [{ id: "d1", name: "दस्त १२.pdf" }], passages: 12 }),
  sse("stage", { stage: "writing" }),
  sse("sentence", { index: 0, text: "एकूण रक्कम ₹1,180 आहे.", citations: [1] }),
  ": ping\n\n",
  sse("done", { reply: REPLY }),
].join("");

/** A body that sends `text` as UTF-8 in chunks of `size` bytes (so cut mid-event and mid-character). */
function body(text: string, size: number, { endEarly = false, failAfter = -1 } = {}) {
  const bytes = new TextEncoder().encode(text);
  let at = 0;
  let sent = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (failAfter >= 0 && sent === failAfter) {
        controller.error(new TypeError("network error"));
        return;
      }
      if (at >= bytes.length || (endEarly && at >= bytes.length / 2)) {
        controller.close();
        return;
      }
      controller.enqueue(bytes.slice(at, at + size));
      at += size;
      sent += 1;
    },
  });
}

type Call = { url: string; init: RequestInit };

/** fetch answers each call with the next of `replies` (a Response, or an error to throw). */
function server(...replies: (Response | Error | ((init: RequestInit) => Promise<Response>))[]) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const next = replies.shift();
    if (next === undefined) throw new Error("no more replies");
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(init);
    return next;
  });
  return calls;
}

const streamed = (stream: ReadableStream<Uint8Array>, status = 200) =>
  new Response(stream, { status, headers: { "Content-Type": "text/event-stream" } });
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

async function run(signal?: AbortSignal) {
  const events: StreamEvent[] = [];
  const reply = await askStream("Total?", "c1", { document_ids: ["d1"] }, undefined, undefined, signal, (event) =>
    events.push(event),
  );
  return { reply, events };
}

describe("reading the event stream (lib/askStream.ts)", () => {
  it("parses events split anywhere: mid-line, mid-event, CRLF across chunks, comments and multi-line data", () => {
    const seen: [string, string][] = [];
    const parser = sseParser((name, data) => seen.push([name, data]));
    const text = ': ping\r\nevent: stage\r\ndata: {"stage":\r\ndata: "writing"}\r\n\r\nevent: x\ndata:1\n\ndata: plain\n\n';
    for (const char of text) parser.push(char); // one character at a time
    parser.end();
    expect(seen).toEqual([
      ["stage", '{"stage":\n"writing"}'],
      ["x", "1"],
      ["message", "plain"],
    ]);
  });

  it("dispatches nothing for an event the stream cut off before its blank line", () => {
    const seen: string[] = [];
    const parser = sseParser((name) => seen.push(name));
    parser.push("event: stage\ndata: {}\n");
    parser.end();
    expect(seen).toEqual([]);
  });

  it("keeps only well-formed events: the four stages, real sources, sentences with their citation numbers", () => {
    expect(streamEvent("stage", { stage: "checking" })).toEqual({ type: "stage", stage: "checking" });
    expect(streamEvent("stage", { stage: "thinking" })).toBeNull();
    expect(streamEvent("sources", { documents: [{ id: "d1", name: "Lease.pdf" }], passages: 3 })).toEqual({
      type: "sources",
      sources: { documents: [{ id: "d1", name: "Lease.pdf" }], passages: 3 },
    });
    expect(streamEvent("sources", { documents: [{ id: 1 }], passages: 3 })).toBeNull();
    // Citations as numbers, or as citation objects with their number.
    expect(streamEvent("sentence", { index: 2, text: "Rent is ₹12,000.", citations: [1, { n: 3 }, 1] })).toEqual({
      type: "sentence",
      sentence: { index: 2, text: "Rent is ₹12,000.", citations: [1, 3] },
    });
    expect(streamEvent("sentence", { index: 0, text: "   ", citations: [] })).toBeNull();
    expect(streamEvent("sentence", { text: "No index." })).toBeNull();
    expect(streamEvent("ping", {})).toBeNull();
    expect(streamEvent("started", { conversation_id: "c1", question_id: "q1" })).toEqual({
      type: "started",
      landing: { conversationId: "c1", questionId: "q1" },
    });
    expect(streamEvent("started", { conversation_id: "c1" })).toBeNull();
  });
});

describe("askStream (lib/api.ts)", () => {
  it("POSTs the same body as /api/chat to /api/chat/stream, with the CSRF header and the session cookie", async () => {
    const calls = server(streamed(body(FULL, 4096)));
    await run();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/chat/stream");
    expect(calls[0]?.init.method).toBe("POST");
    expect(calls[0]?.init.credentials).toBe("same-origin");
    expect(calls[0]?.init.headers).toMatchObject({ "X-Requested-With": "kaagaz", "Content-Type": "application/json" });
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      question: "Total?",
      conversation_id: "c1",
      scope: { document_ids: ["d1"] },
      question_id: expect.stringMatching(UUID),
    });
  });

  it("names the question's id itself and sends the same one with the /api/chat fallback (never saved twice)", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const calls = server(new TypeError("Failed to fetch"), json(REPLY));
    await run();
    const [streamBody, fallbackBody] = calls.map((call) => JSON.parse(String(call.init.body)) as { question_id: string });
    expect(streamBody?.question_id).toMatch(UUID);
    expect(fallbackBody?.question_id).toBe(streamBody?.question_id);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("asking POST /api/chat"), "no_connection");
    warn.mockRestore();
  });

  it.each([1, 3, 7, 64])("hands on every event in order and returns done's reply, %i bytes at a time", async (size) => {
    server(streamed(body(FULL, size)));
    const { reply, events } = await run();
    expect(reply).toEqual(REPLY);
    expect(events).toEqual([
      { type: "stage", stage: "understanding" },
      { type: "stage", stage: "searching" },
      { type: "sources", sources: { documents: [{ id: "d1", name: "दस्त १२.pdf" }], passages: 12 } },
      { type: "stage", stage: "writing" },
      { type: "sentence", sentence: { index: 0, text: "एकूण रक्कम ₹1,180 आहे.", citations: [1] } },
    ]);
  });

  it.each([
    ["the connection fails", () => new TypeError("Failed to fetch")],
    ["the route is not there", () => json({ error: { code: "not_found" } }, 404)],
    ["the server fails", () => json({ error: { code: "internal_error" } }, 500)],
    ["the stream breaks mid-way", () => streamed(body(FULL, 16, { failAfter: 5 }))],
    ["the stream ends before done", () => streamed(body(FULL, 16, { endEarly: true }))],
    ["done is not a reply", () => streamed(body(sse("done", { reply: { nope: 1 } }), 64))],
    ["an event is not JSON", () => streamed(body("event: stage\ndata: {oops\n\n", 64))],
  ])("asks POST /api/chat once instead when %s, and shows its reply with no error", async (_why, first) => {
    const calls = server(first(), json(REPLY));
    const { reply } = await run();
    expect(reply).toEqual(REPLY);
    expect(calls.map((call) => call.url)).toEqual(["/api/chat/stream", "/api/chat"]);
    expect(calls[1]?.init.body).toBe(calls[0]?.init.body);
    expect(calls[1]?.init.headers).toMatchObject({ "X-Requested-With": "kaagaz" });
  });

  it("fails as /api/chat would when the fallback fails too", async () => {
    server(new TypeError("Failed to fetch"), json({ error: { code: "backend_unavailable", message: "Busy." } }, 503));
    await expect(run()).rejects.toMatchObject({ status: 503, code: "backend_unavailable" });
  });

  it("does not ask again when the server refuses the question (a 429, a 403): its own error", async () => {
    const calls = server(json({ error: { code: "rate_limited", message: "Too many questions." } }, 429));
    await expect(run()).rejects.toMatchObject({ status: 429, code: "rate_limited" });
    expect(calls).toHaveLength(1);
  });

  it.each([
    [400, "bad_request", "Type a question first."],
    [401, "not_signed_in", "Sign in again."],
    [403, "csrf_failed", "Refresh the page, then try again."],
    [422, "validation_error", "Choose fewer documents or tags."],
  ])("an HTTP %i JSON error before the stream (validation, auth, CSRF) is /chat's error, never a second ask", async (status, code, message) => {
    const calls = server(json({ error: { code, message } }, status));
    const caught = await run().catch((error: unknown) => error);
    expect(caught).toMatchObject({ status, code, message });
    expect(askErrorText(caught as ApiError)).toBe(message);
    expect(calls).toHaveLength(1);
  });

  it("answers with done alone (a Home ready answer, small talk), no stage before it", async () => {
    const calls = server(streamed(body(sse("done", { reply: REPLY }), 16)));
    const { reply, events } = await run();
    expect(reply).toEqual(REPLY);
    expect(events).toEqual([]);
    expect(calls).toHaveLength(1);
  });

  it("a fixed calculation: understanding, searching, then done; no sources, no sentence", async () => {
    server(streamed(body(sse("stage", { stage: "understanding" }) + sse("stage", { stage: "searching" }) + sse("done", { reply: REPLY }), 16)));
    const { events } = await run();
    expect(events).toEqual([
      { type: "stage", stage: "understanding" },
      { type: "stage", stage: "searching" },
    ]);
  });

  it("takes a sentence's citations as the full Citation objects the server sends, keeping their numbers", async () => {
    const citation = {
      n: 1,
      kind: "piece",
      ref_id: "p1",
      document_id: "d1",
      document_name: "bill.pdf",
      detected_type: "pdf",
      page_no: 1,
      regions: [{ kind: "box", page: 1, box: [0.1, 0.1, 0.5, 0.2] }],
      snippet: "Grand total Rs. 1,20,000",
      label: "verified",
      page_from: 1,
      page_to: 1,
      label_page_no: null,
      label_regions: [],
      hard_to_read: false,
      rank: null,
    };
    const text =
      sse("sentence", { index: 0, text: "The grand total of INV-1042 is Rs. 1,20,000.", citations: [citation] }) +
      sse("sentence", { index: 1, text: "It was paid in March.", citations: [{ ...citation, n: 2, ref_id: "p2" }, citation] }) +
      sse("done", { reply: REPLY });
    server(streamed(body(text, 50)));
    const { events } = await run();
    expect(events).toEqual([
      { type: "sentence", sentence: { index: 0, text: "The grand total of INV-1042 is Rs. 1,20,000.", citations: [1] } },
      { type: "sentence", sentence: { index: 1, text: "It was paid in March.", citations: [2, 1] } },
    ]);
  });

  it("turns an error event into the same error as /api/chat, with no second ask", async () => {
    const text =
      sse("stage", { stage: "writing" }) +
      sse("error", { code: "answer_timeout", message: "This answer is taking too long. Try a narrower question or Swift." });
    const calls = server(streamed(body(text, 32)));
    const caught = await run().catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ code: "answer_timeout" });
    // Said in plain words, as the server put them; no fallback, which would only wait again.
    expect(askErrorText(caught as ApiError)).toBe("This answer is taking too long. Try a narrower question or Swift.");
    expect(calls).toHaveLength(1);
    server(streamed(body(sse("error", { code: "rate_limited", message: "Too many." }), 32)));
    await expect(run()).rejects.toMatchObject({ status: 429 });
    server(streamed(body(sse("error", { code: "backend_unavailable", message: "Busy." }), 32)));
    await expect(run()).rejects.toMatchObject({ status: 503 });
  });

  it("Stop aborts the stream and never falls back", async () => {
    const controller = new AbortController();
    const calls = server(
      (init) =>
        new Promise<Response>((_, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const pending = run(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toHaveLength(1);
  });

  it("Stop mid-stream, after events, still never falls back", async () => {
    const controller = new AbortController();
    let push!: (text: string) => void;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        push = (text) => c.enqueue(new TextEncoder().encode(text));
        controller.signal.addEventListener("abort", () => c.error(new DOMException("Aborted", "AbortError")));
      },
    });
    const calls = server(streamed(stream));
    const events: StreamEvent[] = [];
    const pending = askStream("Q", undefined, undefined, undefined, undefined, controller.signal, (e) => events.push(e));
    push(sse("stage", { stage: "searching" }));
    await vi.waitFor(() => expect(events).toHaveLength(1));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toHaveLength(1);
  });
});

describe("a broken stream after started (Tushar, 16 Oct 2026: the answer is always saved)", () => {
  const STARTED = sse("started", { conversation_id: "c9", question_id: "q9" });
  const QUESTION = { id: "q9", role: "user", text: "Total?", created_at: "t", answer: null, feedback: null };
  const ANSWER = { id: "a9", role: "assistant", text: "It is 1,200.", created_at: "t", answer: null, feedback: null };
  const conversation = (...messages: unknown[]) => json({ id: "c9", title: "Total?", messages });

  afterEach(() => vi.useRealTimers());

  it("hands on where the answer will land, then fetches it from that conversation, never asking again", async () => {
    vi.useFakeTimers();
    const calls = server(
      streamed(body(STARTED + sse("stage", { stage: "writing" }), 4096, { failAfter: 1 })),
      json({ error: { code: "not_found" } }, 404), // the new conversation is not saved yet
      conversation(QUESTION), // the question, its answer still on its way
      conversation({ ...ANSWER, id: "older", text: "An older answer." }, QUESTION, ANSWER),
    );
    const pending = run();
    await vi.advanceTimersByTimeAsync(RECOVER_STEPS_MS[0]! + RECOVER_STEPS_MS[1]!);
    const { reply, events } = await pending;
    expect(reply).toEqual({ conversation_id: "c9", question: QUESTION, answer: ANSWER });
    expect(events[0]).toEqual({ type: "started", landing: { conversationId: "c9", questionId: "q9" } });
    expect(calls.map((call) => [call.init.method, call.url])).toEqual([
      ["POST", "/api/chat/stream"],
      ["GET", "/api/conversations/c9"],
      ["GET", "/api/conversations/c9"],
      ["GET", "/api/conversations/c9"],
    ]);
  });

  it("looks again with backoff (2, 4, 8, 16, 30, 30... s) for up to 190 s, then says so honestly", async () => {
    vi.useFakeTimers();
    expect(RECOVER_STEPS_MS).toEqual([2_000, 4_000, 8_000, 16_000, 30_000]); // the last again and again
    expect(RECOVER_FOR_MS).toBe(190_000);
    const at: number[] = [];
    const calls = server(streamed(body(STARTED, 4096, { failAfter: 1 })));
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/chat/stream") return streamed(body(STARTED, 4096, { failAfter: 1 }));
      at.push(Date.now());
      return conversation(QUESTION);
    });
    const caught = run().catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(RECOVER_FOR_MS + 30_000);
    const error = await caught;
    expect(error).toMatchObject({ code: "answer_pending", message: STILL_SAVING, details: { conversation_id: "c9" } });
    expect(STILL_SAVING).toBe("Couldn't get the answer yet. Open this conversation again in a minute.");
    expect(askErrorText(error as ApiError)).toBe(STILL_SAVING); // its code first, never "busy" for its 503
    const gaps = at.slice(1).map((time, i) => time - at[i]!);
    expect(gaps.slice(0, 6)).toEqual([2_000, 4_000, 8_000, 16_000, 30_000, 30_000]);
    expect(at.at(-1)! - at[0]!).toBeLessThanOrEqual(RECOVER_FOR_MS);
    expect(calls.filter((call) => call.url === "/api/chat")).toHaveLength(0);
  });

  it("Stop while looking stops looking, with no second ask", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const calls = server(streamed(body(STARTED, 4096, { failAfter: 1 })), conversation(QUESTION), conversation(QUESTION));
    const caught = run(controller.signal).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(RECOVER_STEPS_MS[0]! / 2);
    controller.abort();
    expect(await caught).toMatchObject({ name: "AbortError" });
    expect(calls.map((call) => call.url)).toEqual(["/api/chat/stream", "/api/conversations/c9"]);
  });

  it("a stream cut before started asks POST /api/chat once instead, and says why", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const calls = server(streamed(body(sse("stage", { stage: "understanding" }), 16, { failAfter: 1 })), json(REPLY));
    const { reply } = await run();
    expect(reply).toEqual(REPLY);
    expect(calls.map((call) => call.url)).toEqual(["/api/chat/stream", "/api/chat"]);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("asking POST /api/chat"), "stream_broken");
    warn.mockRestore();
  });

  it.each([
    ["after started: it looks in the conversation", STARTED, "/api/conversations/c9"],
    ["before started: it asks POST /api/chat", "", "/api/chat"],
  ])("a stream with no byte for IDLE_MS is given up (the server pings every 10 s); %s", async (_why, first, next) => {
    vi.useFakeTimers();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    let cancelled = false;
    const quiet = new ReadableStream<Uint8Array>({
      start(c) {
        if (first) c.enqueue(new TextEncoder().encode(first));
      },
      cancel() {
        cancelled = true;
      },
    });
    const calls = server(streamed(quiet), next === "/api/chat" ? json(REPLY) : conversation(QUESTION, ANSWER));
    const pending = run();
    await vi.advanceTimersByTimeAsync(IDLE_MS);
    await pending;
    expect(IDLE_MS).toBe(30_000);
    expect(cancelled).toBe(true);
    expect(calls.map((call) => call.url)).toEqual(["/api/chat/stream", next]);
  });
});
