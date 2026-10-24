// The question on its way, kept outside the chat screen (Tushar, 16 Oct 2026).
// Opening Documents or Review unmounts the chat screen, and the answer used to
// land on a screen that was gone: back on Ask, the question had vanished. The
// request lives here instead, so the next chat screen picks it up: still out, it
// shows as pending again; answered meanwhile, the conversation opens with it
// (the server saves both messages even if nobody is waiting, a plain `def` route);
// failed, the error shows and the question is back in the box.
// Stop (Tushar, 24 Oct 2026): the asker can stop waiting; the request is cancelled in the
// browser (stopAsk) and the question goes back in the box, with no error.

import type { ChatResponse } from "@/lib/types";
import { conversationFromSearch, documentFromSearch } from "./logic";

export interface InFlight {
  question: string;
  /** The conversation it was asked in; null for a new one. */
  conversationId: string | null;
  done: Promise<ChatResponse>;
  /** Set once `done` settles. */
  outcome?: { reply: ChatResponse } | { error: unknown };
  /** Cancels the request (stopAsk). */
  controller: AbortController;
  /** The asker pressed Stop: its failure is no error. */
  stopped?: boolean;
}

let current: InFlight | null = null;

/** Send a question and keep it here until a screen has shown what came of it. One at a time: a newer one replaces it. */
export function startAsk(
  question: string,
  conversationId: string | null,
  send: (signal: AbortSignal) => Promise<ChatResponse>,
): InFlight {
  const controller = new AbortController();
  const entry: InFlight = { question, conversationId, done: send(controller.signal), controller };
  entry.done.then(
    (reply) => {
      entry.outcome = { reply };
    },
    (error: unknown) => {
      entry.outcome = { error };
    },
  );
  current = entry;
  return entry;
}

export const askInFlight = (): InFlight | null => current;

/** Stop waiting for this question: the request is cancelled and `done` fails as stopped. */
export function stopAsk(entry: InFlight): void {
  entry.stopped = true;
  entry.controller.abort();
}

/** A screen has shown it: forget it, unless a newer question replaced it. */
export function settleAsk(entry: InFlight): void {
  if (current === entry) current = null;
}

/**
 * The question a chat screen opened at `search` should pick up, or null: the
 * address names its conversation, or names none and no document ("Ask about
 * this document" starts a new chat).
 */
export function resumable(entry: InFlight | null, search: string): InFlight | null {
  if (entry === null) return null;
  const named = conversationFromSearch(search);
  if (named !== null) return named === entry.conversationId ? entry : null;
  return documentFromSearch(search) === null ? entry : null;
}

/** The conversation to show for a picked-up question: its own, or the new one its answer made. */
export function resumedConversation(entry: InFlight): string | null {
  if (entry.conversationId !== null) return entry.conversationId;
  return entry.outcome && "reply" in entry.outcome ? entry.outcome.reply.conversation_id : null;
}
