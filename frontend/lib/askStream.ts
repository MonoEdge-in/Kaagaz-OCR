// #535 (Tushar, 7 Nov 2026), PR 1: Ask's answer as an event stream (POST /api/chat/stream,
// read by askStream in lib/api.ts). This file reads the stream's text into events and checks
// each one before anything shows. The rule above all: no fake words. The server sends only
// real stages, the real sources and sentences that passed verification; anything else here
// is dropped, never shown. `done` and `error` are handled by askStream itself.

export type Stage = "understanding" | "searching" | "writing" | "checking";
export const STAGES: readonly Stage[] = ["understanding", "searching", "writing", "checking"];

export interface StreamSources {
  documents: { id: string; name: string }[];
  passages: number;
}

/** One verified sentence: its place in the answer, its words, the numbers of the sources it cites. */
export interface StreamSentence {
  index: number;
  text: string;
  citations: number[];
}

/** Where the server said the answer will land (`started`, sent before any work): its
 * conversation, and the question's id it is saved as. */
export interface Landing {
  conversationId: string;
  questionId: string;
}

export type StreamEvent =
  | { type: "started"; landing: Landing }
  | { type: "stage"; stage: Stage }
  | { type: "sources"; sources: StreamSources }
  | { type: "sentence"; sentence: StreamSentence };

/**
 * A server-sent events reader: push text as it comes (cut anywhere), and each complete event
 * (ended by a blank line) is handed on as its name and data. Comment lines (": ping") are
 * skipped; several data lines join with a newline; a line may end with CRLF, LF or CR, even
 * one split across two pushes. An event the stream cut off before its blank line is dropped.
 */
export function sseParser(onEvent: (name: string, data: string) => void): { push(text: string): void; end(): void } {
  let buffer = "";
  let name = "";
  let data: string[] = [];
  const line = (text: string) => {
    if (text === "") {
      if (data.length > 0) onEvent(name || "message", data.join("\n"));
      name = "";
      data = [];
      return;
    }
    if (text.startsWith(":")) return;
    const colon = text.indexOf(":");
    const field = colon < 0 ? text : text.slice(0, colon);
    const value = colon < 0 ? "" : text.slice(colon + 1).replace(/^ /, "");
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
  };
  return {
    push(text: string) {
      buffer += text;
      // A CR at the very end may be the first half of a CRLF: keep it until the next push.
      const held = buffer.endsWith("\r") ? 1 : 0;
      const lines = buffer.slice(0, buffer.length - held).split(/\r\n|\r|\n/);
      buffer = (lines.pop() ?? "") + (held ? "\r" : "");
      lines.forEach(line);
    },
    end() {
      if (buffer.endsWith("\r")) line(buffer.slice(0, -1));
      buffer = "";
    },
  };
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** A citation as its number, or as the final answer's citation object (its `n`). */
function citationNumber(value: unknown): number | null {
  const n = isRecord(value) ? value.n : value;
  return typeof n === "number" && Number.isInteger(n) && n > 0 ? n : null;
}

/** The progress event `name` carries, checked; null for anything else or anything malformed. */
export function streamEvent(name: string, data: unknown): StreamEvent | null {
  if (!isRecord(data)) return null;
  if (name === "started") {
    const { conversation_id, question_id } = data;
    if (typeof conversation_id !== "string" || typeof question_id !== "string") return null;
    return { type: "started", landing: { conversationId: conversation_id, questionId: question_id } };
  }
  if (name === "stage") {
    const stage = STAGES.find((each) => each === data.stage);
    return stage ? { type: "stage", stage } : null;
  }
  if (name === "sources") {
    const { documents, passages } = data;
    if (!Array.isArray(documents) || typeof passages !== "number" || passages < 0) return null;
    const named = documents.filter(
      (doc): doc is { id: string; name: string } =>
        isRecord(doc) && typeof doc.id === "string" && typeof doc.name === "string" && doc.name.trim() !== "",
    );
    if (named.length !== documents.length) return null;
    return { type: "sources", sources: { documents: named.map(({ id, name }) => ({ id, name })), passages } };
  }
  if (name === "sentence") {
    const { index, text, citations } = data;
    if (typeof index !== "number" || !Number.isInteger(index) || index < 0) return null;
    if (typeof text !== "string" || text.trim() === "") return null;
    const numbers = (Array.isArray(citations) ? citations : []).map(citationNumber);
    const unique = numbers.filter((n, at): n is number => n !== null && numbers.indexOf(n) === at);
    return { type: "sentence", sentence: { index, text, citations: unique } };
  }
  return null;
}
