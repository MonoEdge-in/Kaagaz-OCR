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

  // Critical #5: a client-side move keeps the tab's memory (cached PDFs, the
  // last screens). Signing out, a session ending and signing in are full page
  // loads instead. The one router.replace to a sign-in page left is the first
  // visit with no session, which has nothing in memory yet.
  it("never moves to or from a sign-in page with router.replace once a session existed", () => {
    const moves = all((p) => /\.(ts|tsx)$/.test(p)).flatMap((p) =>
      Array.from(readFileSync(p, "utf8").matchAll(/router\.(?:replace|push)\(([^)]*)\)/g), (m) => ({
        file: relative(ROOT, p).replaceAll("\\", "/"),
        to: m[1] ?? "",
      })),
    );
    const toSignIn = moves.filter((m) => /login|signInPath/.test(m.to));
    expect(toSignIn).toEqual([{ file: "lib/session.tsx", to: "signInPath(pathname" }]);
    const fromSignIn = moves.filter((m) => m.file.startsWith("app/login/") || m.file.startsWith("app/admin/login/"));
    expect(fromSignIn).toEqual([]);
  });
});
