// Readable type (25 Oct 2026; the product owner: people "will not be able to understand"
// the screens). Buttons, labels and table headers were 11-12px mono capitals spaced apart,
// the hardest text to read. Now they are sentence case in the body face, 14px or more.
// These guards read the CSS source, so the capitals cannot come back.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const noComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** The body of the first top-level rule with exactly this selector. */
function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return noComments(css).match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

const STYLESHEETS = [
  "app/globals.css",
  ...readdirSync(join(ROOT, "app/styles"), { encoding: "utf8" })
    .filter((file) => file.endsWith(".css"))
    .map((file) => `app/styles/${file}`),
  ...["app", "components"].flatMap((dir) =>
    readdirSync(join(ROOT, dir), { encoding: "utf8", recursive: true })
      .filter((file) => file.endsWith(".module.css"))
      .map((file) => `${dir}/${file.replaceAll("\\", "/")}`),
  ),
];

/** Capitals that stay, each with its reason ("file: selector"). */
const ALLOWED = new Map<string, string>([
  ["components/shell/Wordmark.module.css: .by", "\"by MonoEdge\" in the logo, a brand mark, not words to read"],
  ["app/login/login.module.css: .by", "the same brand mark on the sign-in page"],
]);

describe("readable type", () => {
  it("sets nothing in capitals by CSS, outside the short allow-list", () => {
    expect(STYLESHEETS.length).toBeGreaterThan(20);
    const found = STYLESHEETS.flatMap((file) =>
      Array.from(noComments(read(file)).matchAll(/([^{}]+)\{[^}]*text-transform:\s*uppercase/g), (m) => `${file}: ${(m[1] ?? "").trim()}`),
    );
    expect(found.filter((name) => !ALLOWED.has(name))).toEqual([]);
  });

  it("writes buttons in the body face, 15px or more, 16px on the primary, normal spacing", () => {
    const css = read("app/globals.css");
    const btn = rule(css, ".btn");
    expect(btn).toMatch(/font-family: var\(--font-sans\);/);
    expect(btn).toMatch(/font-size: 0\.9375rem;/);
    expect(btn).toMatch(/font-weight: 500;/);
    expect(btn).not.toMatch(/letter-spacing|text-transform/);
    expect(rule(css, ".btn-primary")).toMatch(/font-size: 1rem;/);
    const kit = read("components/ui/Button/Button.module.css");
    expect(rule(kit, ".btn")).toMatch(/font: 500 0\.9375rem\/1\.2 var\(--k-font-body\);/);
    expect(rule(kit, ".btn")).not.toMatch(/letter-spacing/);
    // A small button keeps the 15px words; it is only shorter and narrower.
    expect(rule(kit, ".sm")).not.toMatch(/font-size/);
  });

  it("writes .caps labels and table headers at 14px in the secondary text colour", () => {
    const css = read("app/globals.css");
    for (const selector of [".caps", ".table th"]) {
      const body = rule(css, selector);
      expect(body, selector).toMatch(/font-family: var\(--font-sans\);/);
      expect(body, selector).toMatch(/font-size: var\(--fs-base\);/);
      expect(body, selector).toMatch(/font-weight: 500;/);
      expect(body, selector).toMatch(/color: var\(--text-secondary\);/);
      expect(body, selector).not.toMatch(/letter-spacing|text-transform/);
    }
    expect(css).toMatch(/--fs-base: 0\.875rem;/);
  });
});
