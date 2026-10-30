// The shell and global fixes from the owner's UI audit (16 Oct 2026, items G1 to G12 and
// section 8): the status bar row only with content, no dead gutter, the header's search,
// Upload and account menu, the sign-outs in that menu, one current-link style, the filled
// Review count, control minimums, the scrolled edge, the workspace label, and the shared
// Toast, Skeleton, ConfirmDialog and IconButton.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountMenu } from "@/components/shell/AccountMenu";
import { initials, isSearchShortcut } from "@/components/shell/nav";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { ConfirmDialogProps } from "@/components/ui/ConfirmDialog";
import { IconButton } from "@/components/ui/IconButton";
import { PaletteProvider } from "@/components/ui/PaletteProvider";
import { Skeleton } from "@/components/ui/Skeleton";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { Toast } from "@/components/ui/Toast";
import type { ThemeChoice } from "@/lib/theme";
import type { Me } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const code = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const globals = code("app/globals.css");
const shellCss = code("components/shell/Shell.module.css");
const shell = read("components/shell/AppShell.tsx");
/** The body of the first rule whose selector is exactly `selector`. */
const rule = (css: string, selector: string) => {
  const at = css.indexOf(`\n${selector} {`);
  expect(at, selector).toBeGreaterThanOrEqual(0);
  return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
};

const me: Me = {
  user: {
    id: "u1",
    email: "asha@sharma.in",
    display_name: "Asha Patil",
    role: "admin",
    must_change_password: false,
    totp_enabled: false,
    can_delete_forever: false,
    email_confirmed: true, // #457
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: "admin" },
};
const noop = () => undefined;
/** The account menu as the server renders it, inside the page's theme and palette (children as the third argument). */
const account = (initial: ThemeChoice) =>
  renderToStaticMarkup(
    createElement(
      ThemeProvider,
      { initial } as Parameters<typeof ThemeProvider>[0],
      createElement(
        PaletteProvider,
        { initial: "kaagaz" } as Parameters<typeof PaletteProvider>[0],
        createElement(AccountMenu, { me }),
      ),
    ),
  );

describe("G1: the status bar row only when it has content", () => {
  it("sets the row to 0 while nothing in the bar has content, and drops its edge", () => {
    expect(globals).toMatch(
      /:root:has\(\.app-shell\):not\(:has\(\.app-statusbar > :not\(:empty\)\)\) \{\s*--statusbar-h: 0px;\s*\}/,
    );
    expect(rule(shellCss, ".statusbar")).toMatch(/height: var\(--statusbar-h\);/);
    expect(rule(shellCss, ".statusbar:not(:has(> :not(:empty)))")).toMatch(/border-top: 0;/);
    expect(shell).toContain("className={`app-statusbar ${styles.statusbar}`}");
    // <main>'s height follows the same token, so it gains the 32px back.
    expect(globals).toMatch(/--main-h: calc\([^;]*- var\(--statusbar-h\)/);
  });
});

describe("G2: no dead scrollbar gutter", () => {
  it("keeps scrollbar-gutter off the page and off <main>", () => {
    expect(globals).not.toMatch(/scrollbar-gutter/);
    expect(shellCss).not.toMatch(/scrollbar-gutter/);
  });
});

describe("G5: the header's search, Upload and account menu", () => {
  // Changed on purpose (UI rebuild, 21 Oct 2026): the top bar's search opens the Search screen (3.9).
  it("sends a search to the Search screen, /search?q=...", () => {
    expect(shell).toContain('<Form action="/search" role="search" className={styles.search}>');
    expect(shell).toMatch(/id="app-search"\s*name="q"/);
  });

  it("focuses the search on Ctrl+K and Cmd+K only", () => {
    const keys = (key: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "altKey" | "shiftKey", boolean>>) => ({
      key,
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      shiftKey: false,
      ...mods,
    });
    expect(isSearchShortcut(keys("k", { ctrlKey: true }))).toBe(true);
    expect(isSearchShortcut(keys("K", { metaKey: true }))).toBe(true);
    expect(isSearchShortcut(keys("k", {}))).toBe(false);
    expect(isSearchShortcut(keys("k", { ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isSearchShortcut(keys("k", { ctrlKey: true, altKey: true }))).toBe(false);
    expect(isSearchShortcut(keys("j", { ctrlKey: true }))).toBe(false);
    expect(shell).toMatch(/if \(!isSearchShortcut\(event\)\) return;\s*event\.preventDefault\(\);\s*searchRef\.current\?\.focus\(\);/);
  });

  it("puts search, then the primary Upload, then the account menu at the header's end", () => {
    const end = shell.slice(shell.indexOf("styles.headerEnd"), shell.indexOf("</header>"));
    expect(end.indexOf('role="search"')).toBeGreaterThan(0);
    // Changed on purpose (Tushar, 16 Oct 2026, one control height / centred icons): Upload also
    // takes styles.upload, a square icon button on a phone.
    const upload = end.indexOf("className={`btn btn-primary ${styles.upload}`}");
    expect(upload).toBeGreaterThan(end.indexOf('role="search"'));
    expect(end.indexOf("<AccountMenu")).toBeGreaterThan(upload);
    // Changed on purpose (UI rebuild, 21 Oct 2026): it searches inside documents first, names too.
    expect(end).toMatch(/<label htmlFor="app-search" className="visually-hidden">\s*Search your documents/);
    expect(end).toContain('placeholder="Search inside documents, or a name"');
    expect(end).toContain('title="Search your documents (Ctrl+K, or Cmd+K on a Mac)"');
    expect(end).toContain('aria-keyshortcuts="Control+K Meta+K"');
    // Upload goes to the documents screen, and opens its picker when already there.
    expect(end).toMatch(/<Link href="\/documents" className=\{`btn btn-primary \$\{styles\.upload\}`\} onClick=\{upload\}>/);
    expect(shell).toContain('const UPLOAD_INPUT_ID = "upload-input";');
  });

  it("names the avatar button, with a tooltip, and draws the initials", () => {
    expect(initials("Asha Patil")).toBe("AP");
    expect(initials("  asha  ")).toBe("A");
    expect(initials("Asha Rani Patil")).toBe("AR");
    expect(initials("")).toBe("?");
    const html = account("system");
    expect(html).toMatch(/<button[^>]*aria-label="Account menu, Asha Patil"[^>]*title="Account menu, Asha Patil"/);
    expect(html).toMatch(/popovertarget="account-menu"/i);
    expect(html).toMatch(/<div id="account-menu" popover="auto"/);
  });

  // Vrushit UAT (25 Oct 2026): the avatar circle is as tall as the Upload button beside it.
  it("draws the avatar circle at the Upload button's height, with no border around it", () => {
    const circle = rule(shellCss, ".avatar");
    expect(circle).toMatch(/width: var\(--control-h\);/);
    expect(circle).toMatch(/height: var\(--control-h\);/);
    expect(circle).toMatch(/border-radius: 50%;/);
    expect(rule(shellCss, ".avatarButton")).toMatch(/border: 0;/);
  });
});

describe("G6 and G12: the account menu", () => {
  const html = account("system");
  const menu = html.slice(html.indexOf('id="account-menu"'), html.indexOf("<dialog"));

  it("holds who is signed in with the workspace labelled, the theme switch and both sign-outs", () => {
    expect(menu).toContain("Role: Admin");
    expect(menu).toContain("Workspace: Sharma Traders");
    expect(menu).toContain("<legend");
    expect(menu.indexOf(">Sign out<")).toBeGreaterThan(menu.indexOf("<legend"));
    expect(menu.indexOf(">Sign out everywhere<")).toBeGreaterThan(menu.indexOf(">Sign out<"));
  });

  // Changed on purpose (issue 373, 27 Oct 2026): Sign out is the menu's main button, and Sign
  // out everywhere a quiet item in the danger colour, no longer louder than Sign out.
  it("styles Sign out as the main button, and Sign out everywhere quiet in the danger colour", () => {
    const button = (words: string) => menu.slice(menu.lastIndexOf("<button", menu.indexOf(`>${words}<`)), menu.indexOf(`>${words}<`));
    expect(button("Sign out everywhere")).toContain("btn btn-quiet");
    expect(button("Sign out everywhere")).toContain("signOutEverywhere");
    expect(button("Sign out everywhere")).not.toContain("btn-destructive");
    expect(button("Sign out")).toContain("btn btn-primary");
    expect(rule(shellCss, ".signOutEverywhere")).toMatch(/--btn-fg: var\(--critical\);/);
  });

  it("asks in a dialog first, which focuses Cancel and not the destructive button", () => {
    expect(html).toContain("Sign out everywhere?");
    const dialog = read("components/ui/ConfirmDialog.tsx");
    expect(dialog).toMatch(/dialog\.showModal\(\);\s*cancelRef\.current\?\.focus\(\);/);
    expect(dialog).not.toMatch(/autoFocus/);
    const markup = renderToStaticMarkup(
      createElement(
        ConfirmDialog,
        { open: false, title: "Delete it?", confirmLabel: "Delete", onConfirm: noop, onCancel: noop } as ConfirmDialogProps,
        "Gone for good.",
      ),
    );
    expect(markup).toMatch(/<dialog[^>]*aria-labelledby="([^"]+)"[\s\S]*<h2 id="\1"[^>]*>Delete it\?<\/h2>/);
    // [Destructive] ... [Cancel]: the destructive one pushed away from Cancel by .actions.
    expect(markup).toMatch(/<div class="actions"><button type="button" class="btn btn-destructive">Delete<\/button><button type="button" class="btn">Cancel<\/button><\/div>/);
  });
});

describe("G7: no 'mode is fixed' line under the theme switch", () => {
  it("drops the line and says what System does in its tooltip", () => {
    const toggle = read("components/ui/ThemeToggle.tsx");
    expect(toggle).not.toMatch(/is fixed|THEME_DESCRIPTIONS/);
    const html = account("dark");
    expect(html).toContain('title="Follows your system"');
    expect(html).not.toMatch(/mode is fixed/i);
  });
});

describe("G8 and G9: the current link and the Review count", () => {
  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's active nav item, a raised pill with an
  // edge, bold words and the accent icon, replaces the tint and 3px bar.
  it("marks the current link with a raised pill, an edge, bold words and the accent icon", () => {
    const current = rule(shellCss, ".current,\n.current:hover");
    expect(current).toMatch(/background: var\(--k-nav-active\);/);
    expect(current).toMatch(/border-color: var\(--k-card-edge\);/);
    expect(current).toMatch(/font-weight: 600;/);
    expect(rule(shellCss, ".current svg")).toMatch(/color: var\(--k-nav-accent\);/);
  });

  // Changed on purpose (Tushar, 16 Oct 2026): the Review count is gone (shell-nav.test.ts).
});

describe("G10: control minimums every screen inherits", () => {
  it("sets 32px, 44px on touch, on buttons and on the labels of checkboxes and radios", () => {
    expect(globals).toMatch(/:root \{[\s\S]*?--control-min: 32px;/);
    expect(globals).toMatch(/@media \(pointer: coarse\) \{\s*:root \{[^}]*--control-min: var\(--h-lg\);/);
    expect(rule(globals, ".btn")).toMatch(/min-height: var\(--control-min\);/);
    const label = rule(globals, ':where(label:has(> input[type="checkbox"], > input[type="radio"]))');
    expect(label).toMatch(/min-height: var\(--control-min\);/);
    expect(label).toMatch(/cursor: pointer;/);
  });
});

describe("G11: an edge under the header once the content scrolled", () => {
  it("toggles data-scrolled on <main> and darkens the header's 1px edge, with no shadow", () => {
    expect(shell).toContain('toggleAttribute("data-scrolled", event.currentTarget.scrollTop > 0)');
    expect(rule(shellCss, ".app:has(> .main[data-scrolled]) > .header")).toMatch(/^\s*border-bottom-color: var\(--border-strong\);\s*$/);
    expect(shellCss).not.toMatch(/box-shadow/);
  });
});

describe("section 8: shared Toast, Skeleton and IconButton", () => {
  it("keeps the toast's polite region in the page, in a fixed corner, with Undo when given", () => {
    const empty = renderToStaticMarkup(createElement(Toast, { message: null, onClose: noop }));
    expect(empty).toBe('<div role="status" aria-live="polite"></div>');
    const html = renderToStaticMarkup(createElement(Toast, { message: "Moved to Trash.", onUndo: noop, onClose: noop }));
    expect(html).toMatch(/^<div role="status" aria-live="polite"><div class="toast"><p>Moved to Trash\.<\/p><button[^>]*>Undo<\/button>/);
    expect(html).toMatch(/aria-label="Dismiss" title="Dismiss"/);
    expect(renderToStaticMarkup(createElement(Toast, { message: "Saved.", onClose: noop }))).not.toContain("Undo");
    const toast = rule(globals, ".toast");
    expect(toast).toMatch(/position: fixed;/);
    expect(toast).toMatch(/z-index: var\(--z-toast\);/);
    expect(toast).toMatch(/inset-block-end: calc\(\s*var\(--statusbar-h\) \+ var\(--bottombar-h\)/);
  });

  it("draws a skeleton hidden from screen readers, sized by the screen", () => {
    expect(renderToStaticMarkup(createElement(Skeleton, { className: "row" }))).toBe('<span class="skeleton row" aria-hidden="true"></span>');
    expect(renderToStaticMarkup(createElement(Skeleton))).toBe('<span class="skeleton" aria-hidden="true"></span>');
  });

  it("gives an icon-only button its name and a tooltip", () => {
    const html = renderToStaticMarkup(createElement(IconButton, { icon: "close", label: "Close menu" }));
    expect(html).toMatch(/aria-label="Close menu"/);
    expect(html).toMatch(/title="Close menu"/);
  });

  it("rings focus in both themes from one rule", () => {
    expect(globals).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--focus\);\s*outline-offset: 2px;\s*\}/);
  });
});
