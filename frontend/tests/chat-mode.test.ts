import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { chipScope, keptScope, startScope } from "@/components/chat/scope";
import { storyPlace, storySlot } from "@/components/chat/storySlot";
import type { StorySlot, StorySlotEvent } from "@/components/chat/storySlot";
import {
  MODE_KEY,
  MODE_TEXTS,
  modeFrom,
  modeLockedText,
  readMode,
  saveMode,
  sendPlan,
  startMode,
} from "@/lib/chatMode";
import type { StoryMatch } from "@/lib/types";

const A = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const FOLDER = "0b4f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";
const MATCHES: StoryMatch[] = [{ document_id: A, display_name: "7-12.pdf", doc_type: null, band: "match" }];

afterEach(() => {
  vi.useRealTimers();
});

function run(events: StorySlotEvent[], start: StorySlot | null = null): StorySlot | null {
  return events.reduce(storySlot, start);
}

describe("the chat mode", () => {
  it("is documents unless stories was kept; storage that is missing or refuses is documents", () => {
    for (const value of [null, undefined, "", "Stories", "answer", 1, {}]) expect(modeFrom(value)).toBe("documents");
    expect(modeFrom("stories")).toBe("stories");
    expect(readMode({ getItem: (key) => (key === MODE_KEY ? "stories" : null) })).toBe("stories");
    expect(readMode(null)).toBe("documents");
    const refusing = {
      getItem: (): string | null => {
        throw new Error("SecurityError");
      },
      setItem: (): void => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(readMode(refusing)).toBe("documents");
    expect(() => saveMode(refusing, "stories")).not.toThrow();
    const kept = new Map<string, string>();
    saveMode({ setItem: (key, value) => void kept.set(key, value) }, "stories");
    expect(kept.get(MODE_KEY)).toBe("stories");
  });

  it("asks the documents when opened from a document or a conversation", () => {
    expect(startMode("stories", A, null)).toBe("documents");
    expect(startMode("stories", null, A)).toBe("documents");
    expect(startMode("stories", null, null)).toBe("stories");
    expect(startMode("documents", null, null)).toBe("documents");
  });

  it("has its own words in each mode", () => {
    expect(MODE_TEXTS.documents).toEqual({
      option: "Ask documents",
      label: "Ask about your documents",
      button: "Send question",
      help: null,
    });
    expect(MODE_TEXTS.stories).toEqual({
      option: "Search my stories",
      label: "Describe the document as you remember it",
      button: "Search my stories",
      help: "Finds documents by the stories you wrote. No AI answer.",
    });
  });

  it("never asks for an answer in story mode", () => {
    expect(sendPlan("stories")).toEqual({ chat: false, sideCards: false, storySearch: true });
    expect(sendPlan("documents")).toEqual({ chat: true, sideCards: true, storySearch: false });
  });

  it("is locked, with a reason, only while an answer is on its way", () => {
    expect(modeLockedText(true)).toBe("You can switch once the answer arrives.");
    expect(modeLockedText(false)).toBeNull();
  });

  it("sends the chip's scope to story matches; a conversation still loading sends none", () => {
    expect(chipScope(startScope(null))).toEqual({});
    expect(chipScope(startScope(A))).toEqual({ document_ids: [A] });
    const folder = { ...startScope(null), scope: { kind: "folder" as const, folderId: FOLDER, subfolders: true } };
    expect(chipScope(folder)).toEqual({ folder_id: FOLDER, include_subfolders: true });
    expect(chipScope(keptScope())).toEqual({});
  });
});

describe("the story slot: the cards belong to the question, not to the answer", () => {
  const ask = { type: "ask", token: 1, question: "daregaon jamin" } as const;
  const arrive = { type: "matches", token: 1, matches: MATCHES } as const;
  const answered = { type: "answered", token: 1, answerId: "m2" } as const;
  const failed = { type: "failed", token: 1 } as const;

  it("shows them below the pending card when they come first, then below the answer", () => {
    expect(storyPlace(run([ask]))).toEqual({ at: "none" });
    expect(storyPlace(run([ask, arrive]))).toEqual({ at: "pending" });
    expect(storyPlace(run([ask, arrive, answered]))).toEqual({ at: "answer", answerId: "m2" });
  });

  it("shows them below the answer when they come after it", () => {
    expect(storyPlace(run([ask, answered]))).toEqual({ at: "none" });
    expect(storyPlace(run([ask, answered, arrive]))).toEqual({ at: "answer", answerId: "m2" });
  });

  it("keeps them, at the end with their question, when the answer fails before or after they come", () => {
    const end = { at: "end", question: "daregaon jamin" };
    expect(storyPlace(run([ask, failed, arrive]))).toEqual(end);
    expect(storyPlace(run([ask, arrive, failed]))).toEqual(end);
  });

  it("keeps them through a slow answer, past the old 30 s proxy cut", () => {
    vi.useFakeTimers();
    let state: StorySlot | null = storySlot(null, ask);
    setTimeout(() => (state = storySlot(state, arrive)), 800);
    setTimeout(() => (state = storySlot(state, answered)), 31_000);
    vi.advanceTimersByTime(30_000);
    expect(storyPlace(state)).toEqual({ at: "pending" });
    vi.advanceTimersByTime(1_000);
    expect(storyPlace(state)).toEqual({ at: "answer", answerId: "m2" });
  });

  it("drops an older question's late reply, and anything after open() or startNew()", () => {
    const newer = { type: "ask", token: 2, question: "gold loan" } as const;
    const state = run([ask, newer, arrive, answered, failed]);
    expect(state).toEqual({ token: 2, question: "gold loan", matches: null, answer: "pending", answerId: null });
    expect(run([ask, arrive, { type: "clear" }, arrive, answered])).toBeNull();
  });

  it("shows nothing for no matches", () => {
    expect(storyPlace(run([ask, { type: "matches", token: 1, matches: [] }, failed]))).toEqual({ at: "none" });
  });
});

describe("the sources of story mode", () => {
  const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");

  it("never looks like an answer: no answer card, sources, feedback or answer labels", () => {
    const view = read("../components/chat/StorySearchView.tsx");
    expect(view).not.toMatch(/AnswerView|SourceCard|FeedbackControls|verified|needs review/i);
    expect(view).toMatch(/<section[^>]*aria-labelledby=/);
    expect(view).toContain('role="alert"');
    expect(view).not.toMatch(/role="status"|aria-live/);
  });

  it("switches with native radios in a fieldset, named for screen readers", () => {
    const control = read("../components/chat/ModeSwitch.tsx");
    expect(control).toContain('type="radio"');
    expect(control).toMatch(/<legend className="visually-hidden">What to search<\/legend>/);
  });

  it("logs nothing and never writes markup", () => {
    for (const file of ["../lib/chatMode.ts", "../components/chat/storySlot.ts", "../components/chat/StorySearchView.tsx"]) {
      expect(read(file)).not.toMatch(/console\.|innerHTML|dangerouslySetInnerHTML/);
    }
  });
});
