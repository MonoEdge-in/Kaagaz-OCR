// Tushar, 25 Oct 2026: Ask starts on Swift, and the Swift or Precision picked stays picked
// across tabs and reloads (this browser's storage), checked against what the organisation allows.

import { afterEach, describe, expect, it, vi } from "vitest";
import { choiceBody, defaultPick, loadPick, savePick, usablePick } from "@/components/chat/logic";
import type { ChatOptions } from "@/lib/types";

const FLASH = { id: "google/gemini-3.8-flash", name: "Google: Gemini 3.8 Flash", default: true };
const SONNET = { id: "anthropic/claude-sonnet-5.5", name: "Anthropic: Claude Sonnet 5.5", default: false };

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
    expect(defaultPick(allowed)).toEqual({ agents: [FLASH.id], level: "medium" });
    expect(choiceBody(allowed, defaultPick(allowed))).toEqual({ agents: [FLASH.id], thinking: "medium" });
    expect(defaultPick(options({ levels: ["high"], default_level: "high" })).level).toBe("high"); // no Swift allowed
  });

  it("keeps the pick in this browser, so another tab or a reload starts from it", () => {
    stubStorage();
    savePick({ agents: [SONNET.id], level: "high" });
    expect(usablePick(options(), loadPick())).toEqual({ agents: [SONNET.id], level: "high" });
  });

  it("drops a kept pick the organisation no longer allows", () => {
    expect(usablePick(options({ levels: ["low", "medium"] }), { agents: [FLASH.id], level: "high" })).toBeNull();
    expect(usablePick(options({ agents: [FLASH] }), { agents: [SONNET.id], level: "medium" })).toBeNull();
    expect(usablePick(options(), { agents: [FLASH.id], level: "low" })).toBeNull(); // Low is not offered in Ask
    expect(usablePick(options(), "{nonsense")).toBeNull();
    expect(usablePick(options(), { agents: [42], level: "high" })).toBeNull();
  });

  it("reads nothing kept when the browser refuses storage, and saving then is harmless", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new Error("SecurityError");
      },
    });
    expect(loadPick()).toBeNull();
    expect(() => savePick({ agents: [], level: "medium" })).not.toThrow();
  });
});
