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

  // Critical #5: a module-level Map is a cache that outlives the screen, and
  // grows without end. The viewer keeps PDFs in its LruCache instead.
  it("keeps no module-level Map in components", () => {
    // documentCache.ts is deleted by the citation-page change (critical #5, part 2).
    const offenders = files(join(ROOT, "components"), (p) => /\.(ts|tsx)$/.test(p))
      .filter((p) => !p.endsWith("documentCache.ts"))
      .filter((p) => /^(export )?(const|let|var) \w+(: [^=]+)? = new Map\b/m.test(readFileSync(p, "utf8")));
    expect(offenders.map((p) => relative(ROOT, p).replaceAll("\\", "/"))).toEqual([]);
  });
});
