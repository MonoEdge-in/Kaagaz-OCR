// The handoff's tokens (app/styles/tokens.css): copied exactly from
// design/kaagaz-ui-handoff/styles/kaagaz.css, every colour in both themes, and
// the System choice getting the dark ones when the device is dark.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (...path: string[]) => readFileSync(join(__dirname, "..", ...path), "utf8");
const TOKENS = read("app", "styles", "tokens.css");
const HANDOFF = read("..", "design", "kaagaz-ui-handoff", "styles", "kaagaz.css");

/** The declarations of the first block that opens with `head {`, as name to value. */
function block(css: string, head: string): Map<string, string> {
  const start = css.indexOf(`${head} {`);
  expect(start, head).toBeGreaterThan(-1);
  const body = css.slice(start + head.length + 2, css.indexOf("}", start));
  return new Map(
    Array.from(body.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--k-[\w-]+|color-scheme):\s*([^;]+);/g), (m) => [
      m[1]!,
      m[2]!.trim(),
    ]),
  );
}

const LIGHT = ':root,\n[data-theme="light"]';
const DARK = '[data-theme="dark"]';
const SYSTEM = '  :root:not([data-theme="light"])';

describe("tokens.css", () => {
  it("copies the light and dark tokens exactly from the handoff", () => {
    expect(block(TOKENS, LIGHT)).toEqual(block(HANDOFF, LIGHT));
    expect(block(TOKENS, DARK)).toEqual(block(HANDOFF, DARK));
  });

  it("has every colour token in both themes", () => {
    expect([...block(TOKENS, DARK).keys()].sort()).toEqual([...block(TOKENS, LIGHT).keys()].sort());
  });

  it("gives the System choice the dark tokens when the device is dark", () => {
    expect(TOKENS).toContain('@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {');
    expect(block(TOKENS, SYSTEM)).toEqual(block(TOKENS, DARK));
  });

  it("copies type, space, radius and layout, with the fonts next/font serves first", () => {
    const ours = block(TOKENS, "/* ---------- 2. Type, space, radius ---------- */\n:root");
    const theirs = block(HANDOFF, "/* ---------- 2. Type, space, radius ---------- */\n:root");
    expect([...ours.keys()]).toEqual([...theirs.keys()]);
    for (const [name, value] of theirs) {
      if (name.startsWith("--k-font-")) continue;
      expect(ours.get(name), name).toBe(value);
    }
    expect(ours.get("--k-font-display")).toMatch(/^var\(--font-archivo\), "Archivo"/);
    expect(ours.get("--k-font-body")).toMatch(/^var\(--font-plex-sans\), var\(--font-plex-devanagari\), "IBM Plex Sans"/);
    expect(ours.get("--k-font-mono")).toMatch(/^var\(--font-plex-mono\), "IBM Plex Mono"/);
  });
});
