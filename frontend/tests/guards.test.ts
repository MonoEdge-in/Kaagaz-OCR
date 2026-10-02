// Repository rules that no single component test would catch.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];

function files(dir: string, keep: (path: string) => boolean): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...files(path, keep));
    else if (keep(path)) out.push(path);
  }
  return out;
}

const all = (keep: (path: string) => boolean) =>
  SOURCE_DIRS.flatMap((dir) => files(join(ROOT, dir), keep));

describe("guards", () => {
  it("never renders markup from strings (document text is plain text only)", () => {
    const offenders = all((p) => /\.(ts|tsx)$/.test(p)).filter((p) =>
      /dangerouslySetInnerHTML|\.innerHTML\b|outerHTML|insertAdjacentHTML/.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("uses colour tokens: no hex colours in CSS outside app/globals.css", () => {
    const offenders = all((p) => p.endsWith(".css") && !p.endsWith("globals.css")).filter((p) =>
      /#[0-9a-fA-F]{3,8}\b/.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("uses SVG icons, not emoji", () => {
    const offenders = all((p) => p.endsWith(".tsx")).filter((p) =>
      /\p{Extended_Pictographic}/u.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("keeps Send focusable while waiting: aria-disabled, never disabled", () => {
    const box = readFileSync(join(ROOT, "components/chat/QuestionBox.tsx"), "utf8");
    const start = box.indexOf('label="Send question"');
    const send = box.slice(start, box.indexOf("/>", start));
    expect(send).toContain("aria-disabled={pending");
    expect(send).not.toMatch(/\sdisabled=/);
  });

  it("reads the chat out through one status line, not the whole log", () => {
    const chat = readFileSync(join(ROOT, "components/chat/ChatScreen.tsx"), "utf8");
    expect(chat).toMatch(/role="log"\s+aria-live="off"/);
    expect(chat.match(/role="status"/g)?.length).toBe(1);
  });

  it("has one polite region on the review page", () => {
    const queue = readFileSync(join(ROOT, "components/review/ReviewQueue.tsx"), "utf8");
    expect(queue.match(/aria-live=/g)?.length).toBe(1);
  });

  it("keeps a focused element clear of the sticky top bar (WCAG 2.4.11)", () => {
    const css = readFileSync(join(ROOT, "app/globals.css"), "utf8");
    expect(css).toMatch(/scroll-padding-top:\s*calc\(var\(--topbar-space\) \+ var\(--space-4\)\)/);
  });
});
