// Issue 378 (27 Oct 2026): a signed-out visit to / or /documents logged the expected 401 from
// GET /api/auth/me as a browser console error before going to sign in. The app's first look is
// GET /api/auth/session, a 200 either way, so nothing is logged.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { currentSession } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the app's first look at who is signed in", () => {
  it("asks /api/auth/session and reads a signed-out answer as a 200", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      asked.push(url);
      return new Response(JSON.stringify({ signed_in: false }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    expect(await currentSession()).toEqual({ signed_in: false });
    expect(asked).toEqual(["/api/auth/session"]);
  });

  it("is what the session provider loads, never /auth/me", () => {
    const provider = readFileSync(join(__dirname, "..", "lib/session.tsx"), "utf8");
    expect(provider).toContain("const found = await currentSession();");
    expect(provider).toContain('found.signed_in ? { kind: "signed_in", me: found.me } : { kind: "signed_out" }');
    expect(readFileSync(join(__dirname, "..", "lib/api.ts"), "utf8")).not.toContain('"/auth/me"');
  });
});
