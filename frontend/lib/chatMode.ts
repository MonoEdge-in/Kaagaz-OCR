// "Ask documents" or "Search my stories" in chat (council 3 Oct 2026, "story
// search mode"). Pure, unit tested in node (tests/chat-mode.test.ts): the mode
// kept for the tab, the texts of each mode, and which calls a question makes.
// Story mode never asks for an answer and saves nothing: it only finds the
// user's own documents by the stories they wrote.

export type ChatMode = "documents" | "stories";

export const CHAT_MODES: readonly ChatMode[] = ["documents", "stories"];
export const MODE_KEY = "kaagaz.chat-mode";
export const STORY_SEARCH_LIMIT = 10; // the route's most

export interface ModeTexts {
  option: string; // the switch's words
  label: string; // the question box's label and placeholder
  button: string; // the send button's name
  help: string | null; // a line under the box
}

export const MODE_TEXTS: Record<ChatMode, ModeTexts> = {
  documents: {
    option: "Ask documents",
    label: "Ask about your documents",
    button: "Send question",
    help: null,
  },
  stories: {
    option: "Search my stories",
    label: "Describe the document as you remember it",
    button: "Search my stories",
    help: "Finds documents by the stories you wrote. No AI answer.",
  },
};

/** A stored value as a mode: anything but "stories" is "documents". */
export function modeFrom(value: unknown): ChatMode {
  return value === "stories" ? "stories" : "documents";
}

/** The tab's mode; "documents" when storage is missing or refuses. */
export function readMode(storage: Pick<Storage, "getItem"> | null | undefined): ChatMode {
  try {
    return modeFrom(storage?.getItem(MODE_KEY));
  } catch {
    return "documents";
  }
}

/** Keep the mode for this tab; a refusing storage just forgets it. */
export function saveMode(storage: Pick<Storage, "setItem"> | null | undefined, mode: ChatMode): void {
  try {
    storage?.setItem(MODE_KEY, mode);
  } catch {
    // Private windows and full storage: the mode is not kept, nothing else changes.
  }
}

/**
 * The mode chat opens in: an "Ask about this document" link or a conversation
 * in the address always asks the documents; otherwise the tab's last mode.
 */
export function startMode(stored: ChatMode, documentLink: string | null, conversation: string | null): ChatMode {
  return documentLink !== null || conversation !== null ? "documents" : stored;
}

/** Which calls one question makes. Story mode never calls POST /api/chat. */
export interface SendPlan {
  chat: boolean; // POST /api/chat: an answer, saved in the conversation
  sideCards: boolean; // POST /api/me/story-matches, mode "cards", beside the answer
  storySearch: boolean; // POST /api/me/story-matches, mode "stories", instead of an answer
}

export function sendPlan(mode: ChatMode): SendPlan {
  return mode === "stories"
    ? { chat: false, sideCards: false, storySearch: true }
    : { chat: true, sideCards: true, storySearch: false };
}

/** Why the switch can't be used right now, or null when it can. */
export function modeLockedText(answerPending: boolean): string | null {
  return answerPending ? "You can switch once the answer arrives." : null;
}
