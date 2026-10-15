// Security checklist (15 Oct 2026, GHSA-hq66-cqwq-w95j): PDF.js stays on 6.x,
// and the app never loads the PDF.js parts that run a PDF's own JavaScript
// (its viewer and its scripting sandbox).

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = join(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx|mjs)$/.test(name) ? [path] : [];
  });
}

describe("PDF.js", () => {
  it("is version 6 or later", () => {
    const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
    const installed = JSON.parse(readFileSync(join(root, "node_modules/pdfjs-dist/package.json"), "utf8"));
    expect(pkg.dependencies["pdfjs-dist"]).toMatch(/^\^([6-9]|\d\d)\./);
    expect(Number(installed.version.split(".")[0])).toBeGreaterThanOrEqual(6);
  });

  it("is loaded without its viewer or scripting sandbox", () => {
    const files = ["app", "components", "lib"].flatMap((dir) => sources(join(root, dir)));
    for (const file of files) {
      expect(readFileSync(file, "utf8"), file).not.toMatch(/pdfjs-dist\/(web|build\/pdf\.sandbox)|enableScripting/);
    }
  });
});
