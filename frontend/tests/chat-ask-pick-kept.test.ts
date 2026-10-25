// Tushar, 25 Oct 2026: Ask starts on Swift, and the Swift or Precision picked stays picked
// across tabs and reloads (this browser's storage), checked against what the organisation allows.

import { afterEach, describe, expect, it, vi } from "vitest";
import { choiceBody, defaultPick, loadPick, savePick, usablePick } from "@/components/chat/logic";
import type { ChatOptions } from "@/lib/types";

const FLASH = { id: "google/gemini-3.8-flash", name: "Google: Gemini 3.8 Flash", default: true };
const SONNET = { id: "anthropic/claude-sonnet-5.5", name: "Anthropic: Claude Sonnet 5.5", default: false };
// Changed on purpose (Vrushit UAT, 25 Oct 2026): the pick is the engine alone; the Ask bar's
// model picker is gone, so a model kept by it is ignored and no model is sent.

const options = (changes: Partial<ChatOptions> = {}): ChatOptions => ({
  agents: [FLASH, SONNET],
  levels: ["low", "medium", "high"],
  default_level: "high",
  ensemble: false,
  max_agents: 3,
  ...changes,
});

function stubStorage(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) },
  });
  return store;
}

afterEach(() => vi.unstubAllGlobals());

describe("Ask's engine", () => {
  it("starts on Swift even when the organisation's default is Precision, and sends it", () => {
    const allowed = options();
    expect(defaultPick(allowed)).toEqual({ level: "medium" });
    expect(choiceBody(allowed, defaultPick(allowed))).toEqual({ thinking: "medium" });
    expect(defaultPick(options({ levels: ["high"], default_level: "high" })).level).toBe("high"); // no Swift allowed
  });

  it("keeps the pick in this browser, so another tab or a reload starts from it", () => {
    stubStorage();
    savePick({ level: "high" });
    expect(usablePick(options(), loadPick())).toEqual({ level: "high" });
    // A pick an older screen kept with a model: its level stays, its model does not.
    expect(usablePick(options(), { agents: [SONNET.id], level: "high" })).toEqual({ level: "high" });
  });

  it("drops a kept pick the organisation no longer allows", () => {
    expect(usablePick(options({ levels: ["low", "medium"] }), { level: "high" })).toBeNull();
    expect(usablePick(options({ agents: [FLASH] }), { level: "medium" })).toEqual({ level: "medium" });
    expect(usablePick(options(), { level: "low" })).toBeNull(); // Low is not offered in Ask
    expect(usablePick(options(), "{nonsense")).toBeNull();
    expect(usablePick(options(), { level: 42 })).toBeNull();
  });

  it("reads nothing kept when the browser refuses storage, and saving then is harmless", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new Error("SecurityError");
      },
    });
    expect(loadPick()).toBeNull();
    expect(() => savePick({ level: "medium" })).not.toThrow();
  });
});
