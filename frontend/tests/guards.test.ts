// Repository rules that no single component test would catch.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
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

const LOCAL_IMPORT = /(?:from\s+|import\s*\(\s*)"(@\/[^"]+|\.{1,2}\/[^"]+)"/g;

/** The .ts and .tsx files `start` imports, directly or through other local files. */
function reachable(start: string[]): string[] {
  const seen = new Set<string>();
  const queue = [...start];
  for (let path = queue.pop(); path !== undefined; path = queue.pop()) {
    if (seen.has(path)) continue;
    seen.add(path);
    for (const match of readFileSync(path, "utf8").matchAll(LOCAL_IMPORT)) {
      const spec = match[1] ?? "";
      const base = spec.startsWith("@/") ? join(ROOT, spec.slice(2)) : join(dirname(path), spec);
      const found = [".ts", ".tsx", "/index.ts", "/index.tsx"].map((end) => base + end).find(existsSync);
      if (found) queue.push(found);
    }
  }
  return [...seen];
}

describe("guards", () => {
  it("never renders markup from strings (document text is plain text only)", () => {
    const offenders = all((p) => /\.(ts|tsx)$/.test(p)).filter((p) =>
      /dangerouslySetInnerHTML|\.innerHTML\b|outerHTML|insertAdjacentHTML/.test(readFileSync(p, "utf8")),
    );
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("chat never fetches a whole document for a source preview: each citation carries its page", () => {
    const chat = reachable(files(join(ROOT, "components", "chat"), (p) => /\.(ts|tsx)$/.test(p)));
    expect(chat.map((p) => relative(ROOT, p))).toContain(join("components", "viewer", "PageViewer.tsx"));
    const offenders = chat.filter((p) =>
      /import\s*\{[^}]*\bgetDocument\b[^}]*\}\s*from\s*"@\/lib\/api"/.test(readFileSync(p, "utf8")),
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
});
