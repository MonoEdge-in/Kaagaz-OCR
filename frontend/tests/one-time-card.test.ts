// PRD 7 PR 11a (#604): Home's one-time card. Only for an admin session, only while nothing is kept to
// some people, only until Not now; dismissed is per person; blocked storage never throws. Only the
// dismissed flag is stored.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OneTimeCardView, cardDismissed, dismissCard, showCard } from "@/components/access/OneTimeCard";
import type { Me, Role } from "@/lib/types";

const me = (id: string, role: Role = "admin", scope: "app" | "admin" = "admin") =>
  ({ user: { id, role }, session: { scope } }) as Me;

function storage() {
  const kept = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
  });
  return kept;
}

afterEach(() => vi.unstubAllGlobals());

describe("the one-time card", () => {
  it("shows only to an admin session, once the server says nothing is kept yet", () => {
    storage();
    expect(showCard(me("a1"), false)).toBe(true);
    expect(showCard(me("a1"), true)).toBe(false);
    expect(showCard(me("a1"), null)).toBe(false); // loading, or the call failed
    expect(showCard(me("a1", "admin", "app"), false)).toBe(false);
    expect(showCard(me("m1", "member", "app"), false)).toBe(false);
  });

  it("stays hidden after Not now, for that person only, keeping only the flag", () => {
    const kept = storage();
    dismissCard("a1");
    expect(cardDismissed("a1")).toBe(true);
    expect(showCard(me("a1"), false)).toBe(false);
    expect(showCard(me("a2"), false)).toBe(true);
    expect([...kept.entries()]).toEqual([["kaagaz:access-card:a1", "1"]]);
  });

  it("never throws when storage is blocked", () => {
    const blocked = () => {
      throw new Error("SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked });
    expect(() => dismissCard("a1")).not.toThrow();
    expect(cardDismissed("a1")).toBe(false);
  });

  it("says the words, with Set up access to the Departments tab, then Not now", () => {
    const html = renderToStaticMarkup(createElement(OneTimeCardView, { onNotNow: () => undefined }));
    expect(html).toContain("Everyone can see every file today.");
    expect(html).toContain("Want to keep some files to some people?");
    expect(html).toContain(
      "<ol><li>Name a head for each department.</li><li>Keep a department to its people.</li><li>Make private folders.</li></ol>",
    );
    expect(html).toMatch(/<a class="k-btn k-btn--primary" href="\/admin\?tab=departments">Set up access<\/a>/);
    expect(html.indexOf("Set up access")).toBeLessThan(html.indexOf(">Not now</button>"));
    expect(html).not.toMatch(/lock/i);
  });

  it("sits on Home above the upload actions", () => {
    const home = readFileSync(join(__dirname, "..", "components/dashboard/Dashboard.tsx"), "utf8");
    expect(home.indexOf("<OneTimeCard />")).toBeGreaterThan(0);
    expect(home.indexOf("<OneTimeCard />")).toBeLessThan(home.indexOf("<HomeActions"));
  });
});
