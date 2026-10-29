// A quiet button (.btn-quiet) never shows an outlined pill on hover or press: .btn:hover and
// .btn:active set a border, which showed under the pointer on "Fix text" and "Add meaning"
// (owner's reports, 27–29 Oct 2026).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const rule = (selector: string) =>
  [...css.matchAll(new RegExp(`(?:^|\\n)\\s*${selector.replace(/[.:]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "g"))]
    .map((match) => match[1])
    .join("\n");

describe("quiet buttons keep no outline", () => {
  it("the general hover and press rules still set a border, so quiet must undo it", () => {
    expect(rule(".btn:hover")).toMatch(/border-color:/);
    expect(rule(".btn:active")).toMatch(/border-color:/);
  });

  it("a quiet button's hover and press keep the border transparent", () => {
    expect(rule(".btn-quiet:hover")).toMatch(/border-color:\s*transparent;/);
    expect(rule(".btn-quiet:active")).toMatch(/border-color:\s*transparent;/);
  });

  it("the quiet rules come after the general ones, so they win at equal specificity", () => {
    expect(css.indexOf(".btn-quiet:hover")).toBeGreaterThan(css.indexOf(".btn:hover"));
    expect(css.indexOf(".btn-quiet:active")).toBeGreaterThan(css.indexOf(".btn:active"));
  });
});
