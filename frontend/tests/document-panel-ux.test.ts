// The document panel UX (Tushar, 25 Oct 2026): what is this (the AI summary at the top, once,
// cut after about five lines, Devanagari marked; only the card's own facts), can I trust it
// (one status line), what can I do (one primary). No all-caps buttons, at most four tabs,
// Needs attention only above 0, read-only details, no sideways scroll at 320px, and motion
// only where reduced motion lets it run.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { statusLine } from "@/components/review/wordReview";
import type { ReviewStatus } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

describe("the panel's words and controls", () => {
  it("has no all-caps button labels: every button in the panel is sentence case", () => {
    const rule = /\.panels :global\(\.btn\) \{([^}]*)\}/.exec(css(VIEW_CSS))?.[1] ?? "";
    expect(rule).toMatch(/text-transform:\s*none;/);
    expect(rule).toMatch(/letter-spacing:\s*normal;/);
    expect(rule).toMatch(/min-height:\s*var\(--h-lg\);/);
  });

  it("never puts a count of words to check beside Everything is checked", () => {
    const base = { status: "ready", flagged: 3, reviewed: 0, resolved: 0, pages: 1, words: 395 } as ReviewStatus;
    expect(statusLine({ ...base, remaining: 1 }, "").text).toBe("1 word needs a look");
    expect(statusLine({ ...base, remaining: 0 }, "")).toEqual({ text: "Everything is checked", clear: true });
    expect(statusLine({ ...base, flagged: 0, remaining: 0 }, "")).toEqual({ text: "No words need a look", clear: true });
    expect(statusLine({ ...base, status: "received", remaining: 0 }, "Scanning page 1 of 1").clear).toBe(false);
  });
});

describe("motion", () => {
  const TOUCHED = [
    VIEW_CSS,
    "app/(app)/documents/[id]/WordReview.module.css",
    "components/files/Files.module.css",
    "components/viewer/ReadClearly.module.css",
  ];

  it("moves only where reduced motion allows it", () => {
    const found: string[] = [];
    for (const file of TOUCHED) {
      const text = css(file);
      const stack: string[] = [];
      let start = 0;
      for (let i = 0; i < text.length; i += 1) {
        if (text[i] === "{") {
          const prelude = text.slice(start, i).trim();
          if (prelude.startsWith("@")) stack.push(prelude);
          else {
            const end = text.indexOf("}", i);
            const body = text.slice(i + 1, end);
            const moving = /(?:^|;)\s*(animation|transition)\s*:/.test(body) && !/(animation|transition):\s*none/.test(body);
            if (moving && !stack.some((at) => at.includes("prefers-reduced-motion: no-preference"))) found.push(`${file}: ${prelude}`);
            i = end;
          }
          start = i + 1;
        } else if (text[i] === "}") {
          stack.pop();
          start = i + 1;
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("draws the check in only when the line has just turned clear, not on load", () => {
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).toContain("setChanged(seen === false && line.clear);");
    expect(css("app/(app)/documents/[id]/WordReview.module.css")).toMatch(
      /\.status\[data-changed\] \.statusIcon path \{[^}]*animation: draw-check var\(--motion-draw\) var\(--ease-out\) both;/,
    );
  });
});
