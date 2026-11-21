// #737: Move everyone asks for a fresh code, says what it does to the people moved, moves 50 at a
// time, and keeps Enter from moving anyone (the Close button has the focus).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MOVE_AT_A_TIME, MOVE_EFFECT, moveButtonText, moveSummaryText } from "@/components/operator/logic";
import { moveEveryone } from "@/lib/api";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

afterEach(() => vi.unstubAllGlobals());

describe("Move everyone", () => {
  it("names the people it moves, and never more than a run holds", () => {
    expect(moveButtonText(12)).toBe("Move 12 people");
    expect(moveButtonText(1)).toBe("Move 1 person");
    expect(MOVE_AT_A_TIME).toBe(50);
    expect(moveSummaryText({ connected: 3, to_move: 5 })).toBe("3 people are on company sign-in. 5 still sign in the old way.");
  });

  it("says that everyone moved is signed out and can't use the old password", () => {
    expect(MOVE_EFFECT).toBe(
      "Everyone moved is signed out now and can't use their old password. They sign in again after setting up from the email.",
    );
    expect(read("components/operator/MoveEveryone.tsx")).toContain("{MOVE_EFFECT}");
  });

  it("sends the fresh code, and asks for it before anything is sent", async () => {
    const sent: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, body: JSON.parse(String(init.body)) });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await moveEveryone("org-1", "123456");
    expect(sent).toEqual([{ url: "/api/operator/orgs/org-1/move-everyone", body: { code: "123456" } }]);
    const dialog = read("components/operator/MoveEveryone.tsx");
    expect(dialog).toContain("codeError(code)");
    expect(dialog).toContain("A new 6-digit code from your app");
  });

  it("focuses Close on open, and shows the other-email advice only for taken emails", () => {
    const dialog = read("components/operator/MoveEveryone.tsx");
    expect(dialog).toMatch(/onClick=\{onClose\} disabled=\{busy\} data-autofocus>\s*Close/);
    expect(dialog).not.toMatch(/btn-primary"[^>]*data-autofocus/);
    expect(dialog).toContain('entry.kind === "exists"');
    expect(dialog).toContain("taken.length > 0");
  });
});
