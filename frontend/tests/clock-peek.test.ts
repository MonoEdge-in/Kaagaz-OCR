// The clock beside the brand (Tushar, 24 Oct 2026): every 30 seconds a small clock pops in
// next to "Kaagaz", its hands sweep to India time with the time beside it, and after about
// four and a half seconds it fades away. Decoration only: screen readers skip it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClockPeek, PEEK_EVERY_MS, PEEK_FOR_MS, clockHands, clockText, peeking } from "@/components/shell/ClockPeek";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const AT = new Date("2026-10-24T11:07:00Z"); // 4:37 pm in India

describe("the clock beside the brand", () => {
  it("comes every 30 seconds and stays about four and a half, on the shared tick", () => {
    expect(PEEK_EVERY_MS).toBe(30_000);
    expect(PEEK_FOR_MS).toBe(4_500);
    expect([0, 4_499, 4_500, 29_999, 30_000, 34_499].map(peeking)).toEqual([true, true, false, false, true, true]);
    const clock = read("components/shell/ClockPeek.tsx");
    expect(clock).toContain("useTick()");
    expect(clock).not.toMatch(/setInterval|setTimeout/);
  });

  it("says India time, and points its hands at it", () => {
    expect(clockText(AT)).toBe("4:37 pm");
    expect(clockHands(AT)).toEqual({ hour: 138.5, minute: 222 }); // 4:37 → 4×30 + 37×0.5, 37×6
  });

  it("starts hidden and is skipped by screen readers", () => {
    const html = renderToStaticMarkup(createElement(ClockPeek));
    expect(html).toMatch(/^<span class="[^"]*clock[^"]*" aria-hidden="true">/);
    expect(html).not.toContain("data-shown");
  });

  it("sits beside the sidebar's brand, never in the narrow header", () => {
    const shell = read("components/shell/AppShell.tsx");
    expect(shell).toMatch(/<div className=\{styles\.brand\}>\s*<Wordmark href="\/dashboard" \/>\s*<ClockPeek \/>/);
    expect(shell.match(/<ClockPeek \/>/g)).toHaveLength(1);
  });

  it("pops in and fades out by transform and opacity only, hands sweeping", () => {
    const text = css("components/shell/ClockPeek.module.css");
    expect(text).toMatch(/\.clock \{[^}]*opacity: 0;[^}]*transform: translateX\(-6px\) scale\(0\.9\);[^}]*pointer-events: none;/);
    expect(text).toMatch(/\.clock\[data-shown\] \{\s*opacity: 1;\s*transform: none;/);
    expect(text).toMatch(/\.hand \{[^}]*transform: rotate\(var\(--turn, 0deg\)\);/);
    const calm = text.slice(text.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(calm).toMatch(/\.clock \{\s*transition: opacity var\(--motion-calm\) var\(--ease-out\), transform var\(--motion-calm\) var\(--ease-out\);/);
  });
});
