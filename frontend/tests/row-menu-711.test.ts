// Row menus smaller, and scrolling inside instead of running off the window (#711, Tushar,
// 19 Nov 2026: a document's ⋯ menu with 9 actions ran off the screen). Both row menus: the
// shared one (components/admin/RowMenu.tsx) and a document's (components/documents/DocumentActions.tsx).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RowMenu, moveFocus } from "@/components/admin/RowMenu";
import { menuPlace } from "@/components/documents/library";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
/** The first rule body for `selector` (exactly), after `from`. */
const rule = (text: string, selector: string, from = 0) => {
  const at = text.indexOf(`\n${selector} {`, from);
  expect(at, selector).toBeGreaterThanOrEqual(0);
  return text.slice(at, text.indexOf("}", at));
};
const ONE_LINE = /white-space: nowrap;[\s\S]*overflow: hidden;[\s\S]*text-overflow: ellipsis;/;

describe("the shared row menu (admin/RowMenu)", () => {
  const admin = css("components/admin/Admin.module.css");

  it("is never taller than the window, scrolls inside, and never scrolls the page", () => {
    const menu = rule(admin, ".rowMenu");
    expect(menu).toMatch(/max-block-size: calc\(100dvh - 2 \* var\(--space-2\)\);/);
    expect(menu).toMatch(/overflow-y: auto;/);
    expect(menu).toMatch(/overscroll-behavior: contain;/);
  });

  it("with anchor positioning, takes the side with more room and only the room there", () => {
    const tryOrder = admin.slice(admin.indexOf("@supports (position-try-order: most-block-size)"));
    expect(tryOrder.length).toBeLessThan(admin.length);
    const inner = rule(tryOrder, "    .rowMenu");
    expect(inner).toMatch(/max-block-size: calc\(100% - 2 \* var\(--space-1\)\);/);
    expect(inner).toMatch(/position-try-order: most-block-size;/);
  });

  it("is smaller: narrower, tighter items still a control's least height, each hint one line", () => {
    expect(rule(admin, ".rowMenu")).toMatch(/max-width: min\(16rem, /);
    const item = rule(admin, ".rowMenuItem");
    expect(item).toMatch(/min-height: var\(--control-min\);/);
    expect(item).toMatch(/padding: var\(--space-1\) var\(--space-2\);/);
    expect(rule(admin, ".rowMenuHint")).toMatch(ONE_LINE);
    expect(rule(admin, ".rowMenuHint")).toMatch(/max-width: 100%;/);
    expect(rule(admin, ".rowMenuDanger")).toMatch(/margin-top: var\(--space-2\);/);
  });

  it("gives each item its full hint as a tooltip, still read as its description", () => {
    const html = renderToStaticMarkup(
      createElement(RowMenu, {
        name: "Ravi",
        items: [
          { label: "Enable", description: "Lets them sign in again, with the password they had.", onSelect: () => undefined },
          { label: "Allow Delete forever", disabledReason: "Turn on your two-step sign-in first.", onSelect: () => undefined },
          { label: "Plain", onSelect: () => undefined },
        ],
      }),
    );
    for (const hint of ["Lets them sign in again, with the password they had.", "Turn on your two-step sign-in first."]) {
      const button = new RegExp(`<button[^>]*title="${hint}"[^>]*>`).exec(html)?.[0] ?? "";
      const described = /aria-describedby="([^"]+)"/.exec(button)?.[1];
      expect(described, hint).toBeTruthy();
      expect(html).toContain(`<span id="${described}" class="`);
      expect(html).toContain(`>${hint}</span>`);
    }
    expect(/<button[^>]*role="menuitem"[^>]*>Plain</.exec(html)?.[0]).not.toContain("title=");
  });

  it("keeps the focused item in view as Up, Down, Home and End move", () => {
    const items = [0, 1, 2].map(() => ({ focus: vi.fn(), scrollIntoView: vi.fn() }));
    const preventDefault = vi.fn();
    const press = (key: string, at: number) =>
      moveFocus({ key, target: items[at], preventDefault, currentTarget: { querySelectorAll: () => items } } as never);
    press("End", 0);
    expect(items[2]?.focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(items[2]?.scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    press("ArrowDown", 2);
    expect(items[0]?.scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    press("a", 0);
    expect(preventDefault).toHaveBeenCalledTimes(2);
  });
});

describe("a document's menu (documents/DocumentActions)", () => {
  const library = css("components/documents/Library.module.css");
  const actions = read("components/documents/DocumentActions.tsx");
  const view = { width: 1200, height: 800 };

  it("is never taller than the window, scrolls inside, and never scrolls the page", () => {
    const menu = rule(library, ".menuList");
    expect(menu).toMatch(/max-height: calc\(100dvh - 2 \* var\(--space-2\)\);/);
    expect(menu).toMatch(/overflow-y: auto;/);
    expect(menu).toMatch(/overscroll-behavior: contain;/);
  });

  it("opens below when it fits, else on the side with more room, and only as tall as that room", () => {
    expect(menuPlace({ top: 100, bottom: 136, right: 900 }, { width: 208, height: 200 }, view).maxHeight).toBe(652);
    // 9 actions, taller than either side: above (room 480) beats below (room 260), cut to fit.
    const up = menuPlace({ top: 492, bottom: 528, right: 900 }, { width: 208, height: 700 }, view);
    expect(up).toEqual({ top: 8, left: 692, up: true, maxHeight: 480 });
    const down = menuPlace({ top: 200, bottom: 236, right: 900 }, { width: 208, height: 700 }, view);
    expect(down).toEqual({ top: 240, left: 692, up: false, maxHeight: 552 });
    expect(actions).toMatch(/setAt\(\{ top: spot\.top, left: spot\.left, maxHeight: spot\.maxHeight \}\)/);
    expect(actions).toContain("style={at ?? { top: 0, left: 0 }}");
  });

  it("is smaller: narrower, tighter items still a control's least height, each hint one line", () => {
    expect(rule(library, ".menuList")).toMatch(/max-width: min\(18rem, /);
    const item = rule(library, ".menuItem");
    expect(item).toMatch(/min-height: var\(--control-min\);/);
    expect(item).toMatch(/padding: var\(--space-1\) var\(--space-2\);/);
    expect(rule(library, ".menuHint")).toMatch(ONE_LINE);
  });

  it("gives each item its full hint as a tooltip, and keeps the focused item in view", () => {
    expect(actions).toContain("title={extra.disabledReason ?? extra.hint}");
    expect(actions).toContain("title={ACTION_HELP.download}");
    expect(actions).toContain("title={ACTION_HELP[entry.action]}");
    expect(actions).toMatch(/items\[next\]\?\.focus\(\{ preventScroll: true \}\);\s*items\[next\]\?\.scrollIntoView\(\{ block: "nearest" \}\);/);
  });
});
