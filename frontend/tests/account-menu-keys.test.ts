// Issue 373 (27 Oct 2026): the arrow keys move into the account menu from the avatar and
// between its actions (AI settings, Sign out, Sign out everywhere), as a row's menu does.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { menuKeyTarget } from "@/components/shell/AccountMenu";

const ITEMS = ["AI settings", "Sign out", "Sign out everywhere"];

describe("the account menu's keys", () => {
  it("go into the menu from the avatar: Down to the first action, Up to the last", () => {
    expect(menuKeyTarget(ITEMS, null, "ArrowDown")).toBe("AI settings");
    expect(menuKeyTarget(ITEMS, null, "ArrowUp")).toBe("Sign out everywhere");
  });

  it("move between the actions, round the ends, with Home and End", () => {
    expect(menuKeyTarget(ITEMS, "AI settings", "ArrowDown")).toBe("Sign out");
    expect(menuKeyTarget(ITEMS, "Sign out everywhere", "ArrowDown")).toBe("AI settings");
    expect(menuKeyTarget(ITEMS, "AI settings", "ArrowUp")).toBe("Sign out everywhere");
    expect(menuKeyTarget(ITEMS, "Sign out", "Home")).toBe("AI settings");
    expect(menuKeyTarget(ITEMS, "Sign out", "End")).toBe("Sign out everywhere");
  });

  it("leave every other key alone, and an empty menu", () => {
    expect(menuKeyTarget(ITEMS, "Sign out", "Enter")).toBeNull();
    expect(menuKeyTarget([], null, "ArrowDown")).toBeNull();
  });

  it("are wired to the avatar and the menu, and leave a radio's own arrows to it", () => {
    const source = readFileSync(join(__dirname, "..", "components/shell/AccountMenu.tsx"), "utf8");
    expect(source).toMatch(/popoverTarget=\{MENU_ID\}\s*onKeyDown=\{onKey\}/);
    expect(source).toMatch(/popover="auto" className=\{`motion-pop \$\{styles\.account\}`\} onKeyDown=\{onKey\}/);
    expect(source).toContain('if (from instanceof HTMLInputElement && from.type === "radio") return;');
    expect(source).toContain("menu.showPopover();");
  });
});
