// Layout rules for the tables (council 2026-10-04, responsive screens). Each
// guard reads the CSS source, so a rule that brings an old bug back fails here:
// text that cannot wrap ("29 minutes agoTushar"), words broken anywhere
// ("Unknow|n"), and columns that need more room than the table has.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { STICKY_VIEWER_QUERY } from "@/components/viewer/viewer";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

interface Rule {
  /** The at-rule preludes around the rule, outermost first ("" at the top level). */
  at: string;
  selector: string;
  body: string;
}

/** The style rules of a CSS file. Enough for this repo: no nesting, no braces in strings. */
function rules(css: string): Rule[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  const stack: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "{") {
      const prelude = text.slice(start, i).trim();
      if (prelude.startsWith("@")) {
        stack.push(prelude);
        start = i + 1;
        continue;
      }
      const end = text.indexOf("}", i);
      out.push({ at: stack.join(" "), selector: prelude, body: text.slice(i + 1, end) });
      i = end;
      start = end + 1;
    } else if (text[i] === "}") {
      stack.pop();
      start = i + 1;
    }
  }
  return out;
}

const selectors = (rule: Rule) => rule.selector.split(",").map((part) => part.trim());

// Cells, headers and the text inside them must be able to wrap.
const WRAPPING = /\b(td|th)\b|Cell\b|Message\b|\.meta\b|\.name\b|\.statusContent\b/;
const NOWRAP = /white-space:\s*nowrap/;
const CELL = /\b(td|th)\b/;

/** Rules in these files whose cells or cell text are kept on one line. */
function nowrapCells(files: string[]): string[] {
  return files.flatMap((file) =>
    rules(read(file))
      .filter((rule) => NOWRAP.test(rule.body))
      // Changed on purpose (UI quality council, 15 Oct 2026): headers wrap between words too; a
      // one-line header spilled into its neighbour in tables with many columns.
      .flatMap((rule) => selectors(rule).filter((s) => WRAPPING.test(s)))
      .map((s) => `${file}: ${s}`),
  );
}

/** Where a container query applies, in rem: [min, max]. null when it is not that container. */
function containerRange(at: string, container: string): [number, number] | null {
  const query = new RegExp(`@container ${container}\\b([^@]*)`).exec(at)?.[1];
  if (query === undefined) return null;
  const min = /min-width:\s*([\d.]+)rem/.exec(query)?.[1];
  const max = /max-width:\s*([\d.]+)rem/.exec(query)?.[1];
  return [min ? Number(min) : 0, max ? Number(max) : Infinity];
}

/** The widths (rem) of the columns shown when the container is `width` rem wide. */
function columnsAt(css: string, container: string, width: number): Map<string, number> {
  const widths = new Map<string, number>();
  const hidden = new Set<string>();
  for (const rule of rules(css)) {
    const range = containerRange(rule.at, container);
    if (!range || width < range[0] || width > range[1]) continue;
    for (const column of selectors(rule).map((s) => /\.(\w+Column)$/.exec(s)?.[1])) {
      if (!column) continue;
      if (/display:\s*none/.test(rule.body)) hidden.add(column);
      const rem = /(?<![-\w])width:\s*([\d.]+)rem/.exec(rule.body)?.[1];
      if (rem) widths.set(column, Number(rem));
    }
  }
  for (const column of hidden) widths.delete(column);
  return widths;
}

/** The least room Name and Status share in the documents table (11rem each). */
const NAME_AND_STATUS_REM = 22;

/** The least room the admin Users table's User column (name and e-mail) keeps. */
const USER_REM = 11;

/** Each width a table's container queries start at, with the columns shown there. */
function budgets(file: string, container: string, keep: number) {
  const css = read(file);
  const starts = new Set(
    rules(css)
      .map((rule) => containerRange(rule.at, container)?.[0])
      .filter((min): min is number => min !== undefined && min > 0),
  );
  return [...starts]
    .sort((a, b) => a - b)
    .map((start) => {
      const columns = [...columnsAt(css, container, start).values()];
      return { start, needs: columns.reduce((sum, rem) => sum + rem, keep) };
    });
}

const DOCS_CSS = "components/documents/Documents.module.css";
const ADMIN_CSS = "components/admin/Admin.module.css";
const OPERATOR_CSS = "components/operator/Operator.module.css";
const PLATFORM_CSS = "components/operator/Platform.module.css";
const TABLE_FILES = [DOCS_CSS, "components/documents/Table.module.css", ADMIN_CSS, OPERATOR_CSS, PLATFORM_CSS];

/** Every stylesheet the app ships, as paths from the frontend folder. */
const CSS_FILES = ["app", "components"].flatMap((dir) =>
  readdirSync(join(ROOT, dir), { encoding: "utf8", recursive: true })
    .filter((file) => file.endsWith(".css"))
    .map((file) => `${dir}/${file.replaceAll("\\", "/")}`),
);

/** Each rule in every stylesheet whose body matches, named "file: selector". */
const everyRule = (body: RegExp) =>
  CSS_FILES.flatMap((file) =>
    rules(read(file))
      .filter((rule) => body.test(rule.body))
      .map((rule) => ({ file, rule, name: `${file}: ${rule.selector.replace(/\s+/g, " ")}` })),
  );

/** The rules for exactly this selector (one of a list counts), in a CSS file. */
const rulesFor = (file: string, selector: string) =>
  rules(read(file)).filter((rule) => selectors(rule).includes(selector));

describe("layout guards", () => {
  it("lets every table cell, header and its text wrap", () => {
    expect(nowrapCells(TABLE_FILES)).toEqual([]);
  });

  it("never breaks words anywhere in a table cell, only where they are too long to fit", () => {
    const offenders = TABLE_FILES.flatMap((file) =>
      rules(read(file))
        .filter((rule) => /overflow-wrap:\s*anywhere/.test(rule.body))
        .flatMap(selectors)
        .filter((s) => CELL.test(s))
        .map((s) => `${file}: ${s}`),
    );
    expect(offenders).toEqual([]);
  });

  it("keeps the tables free of one-line cells in their markup", () => {
    const markup = [
      "components/documents/DocumentsTable.tsx",
      "components/admin/UsersPanel.tsx",
      "components/admin/ActivityPanel.tsx",
      "components/admin/FailedPanel.tsx",
      "components/operator/OrganisationsView.tsx",
      "components/operator/ConfirmDialog.tsx",
      "components/operator/CreateDialog.tsx",
      "components/operator/AdminsDialog.tsx",
      "components/operator/ActivityView.tsx",
      "components/operator/HealthView.tsx",
    ];
    expect(markup.filter((file) => /styles\.nowrap/.test(read(file)))).toEqual([]);
  });

  it("asks container queries in rem, so the switch follows the text size", () => {
    const px = TABLE_FILES.flatMap((file) => rules(read(file)).map((rule) => rule.at))
      .filter((at) => at.includes("@container") && /\d+(\.\d+)?px/.test(at));
    expect(px).toEqual([]);
  });

  it("gives the tables' short columns rem widths, never percentages", () => {
    const percent = TABLE_FILES.flatMap((file) => rules(read(file)))
      .filter((rule) => selectors(rule).some((s) => /Column$/.test(s)) && /(?<![-\w])width:\s*[\d.]+%/.test(rule.body))
      .map((rule) => rule.selector);
    expect(percent).toEqual([]);
  });

  it("leaves Name and Status room at each width the documents table starts at", () => {
    const found = budgets(DOCS_CSS, "docs-list", NAME_AND_STATUS_REM);
    expect(found.map((b) => b.start)).toEqual([48, 64]);
    for (const { start, needs } of found) expect(needs, `at ${start}rem`).toBeLessThanOrEqual(start);
  });

  it("lets a badge wrap, so a long label never pushes the page sideways", () => {
    const badge = rulesFor("app/globals.css", ".badge");
    expect(badge.length).toBeGreaterThan(0);
    expect(badge.filter((rule) => NOWRAP.test(rule.body))).toEqual([]);
  });

  it("stretches the sidebar down the page; only its inner part sticks", () => {
    const shell = "components/shell/Shell.module.css";
    expect(rulesFor(shell, ".sidebar").filter((rule) => /position:\s*sticky/.test(rule.body))).toEqual([]);
    expect(rulesFor(shell, ".sidebarInner").some((rule) => /position:\s*sticky/.test(rule.body))).toBe(true);
  });

  it("gives the page frame no height limit of its own (no second scrollbar)", () => {
    const frame = rulesFor("components/viewer/PageViewer.module.css", ".frame");
    expect(frame.length).toBeGreaterThan(0);
    expect(frame.filter((rule) => /max-height/.test(rule.body))).toEqual([]);
  });

  it("rings the highlight in paper then dark ink, in both themes (1.4.11)", () => {
    const box = rulesFor("components/viewer/PageViewer.module.css", ".box");
    expect(box.map((rule) => /box-shadow:([^;]*)/.exec(rule.body)?.[1]?.trim())).toEqual([
      "0 0 0 2px var(--paper), 0 0 0 4px var(--hl-halo)",
    ]);
    // Set once, so the dark theme keeps it; a dark ink, never transparent.
    const halo = read("app/globals.css").match(/--hl-halo:\s*([^;]+);/g) ?? [];
    expect(halo).toEqual(["--hl-halo: #1f1a14;"]);
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("1f") + 0.7152 * channel("1a") + 0.0722 * channel("14");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(3); // against the white paper ring
  });

  it("pulses a new spot only where reduced motion can stop it", () => {
    const css = read("components/viewer/PageViewer.module.css");
    expect(rulesFor("components/viewer/PageViewer.module.css", ".pulse")[0]?.body).toMatch(/animation:\s*spotPulse [\d.]+s ease-out 2;/);
    expect(css).toContain("@keyframes spotPulse");
    const reduced = rules(read("app/globals.css")).filter((rule) => rule.at === "@media (prefers-reduced-motion: reduce)");
    expect(reduced.some((rule) => /animation-iteration-count:\s*1 !important/.test(rule.body))).toBe(true);
  });

  it("sticks the page column under the same media query the page fit uses", () => {
    const sticky = rules(read("app/(app)/documents/[id]/DocumentView.module.css")).filter(
      (rule) => selectors(rule).includes(".viewer") && /position:\s*sticky/.test(rule.body),
    );
    expect(sticky.map((rule) => rule.at)).toEqual([`@media ${STICKY_VIEWER_QUERY}`]);
  });

  it("leaves the admin Users table's User column room where it becomes a table", () => {
    const found = budgets(ADMIN_CSS, "reflow", USER_REM);
    // Changed on purpose (UI touch-up council, 15 Oct 2026): from 64rem the actions sit side by side.
    expect(found.map((b) => b.start)).toEqual([48, 64]);
    for (const { start, needs } of found) expect(needs, `at ${start}rem`).toBeLessThanOrEqual(start);
  });

  it("leaves the organisations table's Name column room where it is a table", () => {
    const css = read(OPERATOR_CSS);
    for (const width of [48, 64]) {
      const needs = [...columnsAt(css, "reflow", width).values()].reduce((sum, rem) => sum + rem, USER_REM);
      expect(needs, `at ${width}rem`).toBeLessThanOrEqual(width);
    }
    expect(columnsAt(css, "reflow", 48).has("pagesColumn")).toBe(false);
    expect(columnsAt(css, "reflow", 64).has("pagesColumn")).toBe(true);
  });

  it("leaves the activity and health tables' words columns 11rem each where they are tables", () => {
    const widths = columnsAt(read(PLATFORM_CSS), "reflow", 48);
    const sum = (columns: string[]) => columns.reduce((total, column) => total + (widths.get(column) ?? 0), 0);
    // Activity: Organisation and Action share the rest; Health: Name, Documents and Failed.
    expect(sum(["whenColumn", "targetColumn", "byColumn"]) + 2 * USER_REM).toBeLessThanOrEqual(48);
    expect(sum(["statusColumn", "waitColumn"]) + 3 * USER_REM).toBeLessThanOrEqual(48);
  });

  it("lets a status chip wrap inside its cell", () => {
    const chip = rules(read("app/globals.css")).filter((rule) => selectors(rule).includes(".status-chip"));
    expect(chip.length).toBeGreaterThan(0);
    expect(chip.filter((rule) => NOWRAP.test(rule.body))).toEqual([]);
  });

  // UI quality council, 15 Oct 2026: the guards below hold the layout, scroll and motion fixes.

  it("lets the chat screen fill the content area beside the sidebar", () => {
    const screen = rulesFor("components/chat/Chat.module.css", ".screen").find((rule) => rule.at === "");
    expect(screen?.body).toMatch(/flex:\s*1 1 auto;/);
    expect(screen?.body).toMatch(/min-width:\s*0;/);
  });

  it("keeps the sticky page column steady and never squeezes the page to nothing", () => {
    const sticky = rules(read("app/(app)/documents/[id]/DocumentView.module.css")).filter(
      (rule) => selectors(rule).includes(".viewer") && /position:\s*sticky/.test(rule.body),
    );
    expect(sticky.length).toBe(1);
    expect(sticky[0]?.body ?? "").not.toMatch(/dvh/);
    expect(sticky[0]?.body).toMatch(/100svh/);
    const fit = rulesFor("components/viewer/PageViewer.module.css", ".fitPage").find((rule) => rule.at === "");
    expect(Number(/min-height:\s*([\d.]+)rem/.exec(fit?.body ?? "")?.[1] ?? 0)).toBeGreaterThan(0);
  });

  it("lets a button's label wrap inside it, everywhere", () => {
    const btn = rulesFor("app/globals.css", ".btn");
    expect(btn.some((rule) => /white-space:\s*normal/.test(rule.body) && /max-width:\s*100%/.test(rule.body))).toBe(true);
    const nowrap = everyRule(NOWRAP).filter(({ rule }) => /\.btn\b/.test(rule.selector));
    expect(nowrap.map((found) => found.name)).toEqual([]);
  });

  it("traps scrolling only in drawers, dialogs and the chat's own panes", () => {
    const allowed = [
      "components/ui/Drawer.module.css: .body: overscroll-behavior",
      "components/documents/ActionDialog.module.css: .body: overscroll-behavior",
      "components/chat/Chat.module.css: .pane: overscroll-behavior",
      "components/chat/Chat.module.css: .thread: overscroll-behavior",
      "components/documents/Library.module.css: .treePane: overscroll-behavior",
      // Sideways only: a wide page scrolls without a swipe going back a screen.
      "components/viewer/PageViewer.module.css: .frame: overscroll-behavior-x",
      "components/viewer/PageViewer.module.css: .sheetFrame: overscroll-behavior-x",
    ];
    const found = everyRule(/overscroll-behavior/).flatMap(({ rule, name }) =>
      [...rule.body.matchAll(/(overscroll-behavior[-\w]*):/g)].map((match) => `${name}: ${match[1]}`),
    );
    expect(found.filter((entry) => !allowed.includes(entry))).toEqual([]);
  });

  it("sizes by the window with dvh or svh, never bare vh (it ignores the phone's toolbars)", () => {
    const found = CSS_FILES.filter((file) => /(?<![\w.-])[\d.]+vh\b/.test(read(file).replace(/\/\*[\s\S]*?\*\//g, "")));
    expect(found).toEqual([]);
  });

  it("wraps the admin and operator tabs at every width, with no hidden sideways scroll", () => {
    const tabs = rulesFor(ADMIN_CSS, ".tabs").find((rule) => rule.at === "");
    expect(tabs?.body).toMatch(/flex-wrap:\s*wrap/);
    const scrolling = [ADMIN_CSS, "components/operator/Insights.module.css"].flatMap((file) =>
      rules(read(file))
        .filter((rule) => /tab/i.test(rule.selector) && /overflow-x|scroll-snap|scrollbar-width/.test(rule.body))
        .map((rule) => `${file}: ${rule.selector}`),
    );
    expect(scrolling).toEqual([]);
  });

  it("keeps each side of a page clear of its own notch, at every width", () => {
    const page = rulesFor("app/globals.css", ".page").filter((rule) => /padding/.test(rule.body));
    expect(page.map((rule) => rule.at)).toEqual(["", "@media (min-width: 640px)"]);
    for (const { body } of page) {
      expect(body).toMatch(/padding-left:[^;]*env\(safe-area-inset-left\)/);
      expect(body).toMatch(/padding-right:[^;]*env\(safe-area-inset-right\)/);
      // Nothing after them puts the plain padding back.
      expect(body.slice(body.indexOf("padding-left"))).not.toMatch(/padding(-inline)?:/);
    }
    expect(rulesFor("app/globals.css", ".page.page-wide")[0]?.body).toMatch(/max-width:\s*120rem/);
  });

  it("animates opacity and transform, not background-position (a repaint every frame)", () => {
    // The saving bar's moving stripe (PRD 5 U10).
    // Changed on purpose (sign-in redesign, 15 Oct 2026): the sign-in title's sweep is gone.
    const allowed = ["components/documents/Documents.module.css: @keyframes saving"];
    const found = everyRule(/background-position/)
      .filter(({ rule }) => rule.at.startsWith("@keyframes"))
      .map(({ file, rule }) => `${file}: ${rule.at}`);
    expect([...new Set(found)].filter((entry) => !allowed.includes(entry))).toEqual([]);
  });

  // Board 11 of the UI mockups: one calm frame for all three sign-ins, and its
  // only motion is the citation fading in once (no robot, pages or title sweep).
  it("frames every sign-in with AuthFrame, whose one motion is a single fade", () => {
    const pages = ["app/login/page.tsx", "app/admin/login/page.tsx", "app/operator/login/page.tsx", "app/signup/page.tsx"];
    for (const page of pages) {
      expect(read(page), page).toMatch(/<AuthFrame\b/);
    }
    const css = "app/login/login.module.css";
    const all = rules(read(css));
    expect([...new Set(all.map((rule) => rule.at).filter((at) => at.startsWith("@keyframes")))]).toEqual([
      "@keyframes auth-cite",
    ]);
    const animated = all.filter((rule) => /(^|;)\s*animation(-[\w-]+)?:/.test(rule.body));
    for (const { at, body } of animated) {
      if (at.includes("prefers-reduced-motion")) expect(body).toMatch(/animation:\s*none/);
      else expect(body).toMatch(/animation:\s*auth-cite 600ms var\(--ease\) 400ms both;/);
    }
    expect(animated.map((rule) => rule.at)).toContain("@media (prefers-reduced-motion: reduce)");
    expect(read(css)).not.toMatch(/\binfinite\b/);
  });

  it("loops an animation forever only to show work in progress (WCAG 2.2.2)", () => {
    const allowed = [
      'app/globals.css: .status-chip[data-tone="working"]::before',
      "app/globals.css: .spinner",
      "app/globals.css: .skeleton",
      "components/documents/Documents.module.css: .readMeter:indeterminate",
    ];
    const found = everyRule(/animation(-iteration-count)?:[^;]*\binfinite\b/).map(({ name }) => name);
    expect(found.filter((entry) => !allowed.includes(entry))).toEqual([]);
  });

  it("gives the chat's small links a 44px tap area on touch, without moving the text", () => {
    const coarse = rules(read("components/chat/Chat.module.css")).filter((rule) => rule.at === "@media (pointer: coarse)");
    const body = (selector: string) => coarse.find((rule) => selectors(rule).includes(selector))?.body ?? "";
    expect(body(".docChip")).toMatch(/min-height:\s*44px/);
    expect(body(".cite")).toMatch(/position:\s*relative/);
    expect(body(".cite::after")).toMatch(/inset:\s*-9px/); // the 26px chip plus 9px each side
  });

  it("shows a whole dialog, its foot included (no 2px clip)", () => {
    const css = "components/documents/ActionDialog.module.css";
    expect(rulesFor(css, ".panel").filter((rule) => /max-height/.test(rule.body))).toEqual([]);
    expect(rulesFor(css, ".dialog[open]")[0]?.body).toMatch(/display:\s*flex;[\s\S]*flex-direction:\s*column/);
  });

  it("moves an upload bar and card without animating width or shadows", () => {
    expect(rulesFor(DOCS_CSS, ".readMeter::-webkit-progress-value").filter((rule) => /transition/.test(rule.body))).toEqual([]);
    expect(rulesFor(DOCS_CSS, ".upload")[0]?.body).not.toMatch(/transition:[^;]*box-shadow/);
  });

  // UI touch-up council, 15 Oct 2026: the guards below hold the final touch-up fixes.

  it("positions every scroll box, so hidden screen-reader text inside it cannot stretch the page", () => {
    const allowed = [
      // Inside a modal <dialog> or the fixed drawer, which already hold their hidden text.
      "components/documents/ActionDialog.module.css: .body",
      "components/ui/Drawer.module.css: .body",
      // A <textarea>: it scrolls its own text and holds no elements.
      "components/chat/Chat.module.css: .composerInput",
    ];
    const modules = CSS_FILES.filter((file) => file.endsWith(".module.css"));
    const unpositioned = modules.flatMap((file) => {
      const all = rules(read(file));
      return all
        .filter((rule) => /overflow(-x|-y)?:\s*(auto|scroll)/.test(rule.body))
        .flatMap(selectors)
        .filter((s) => !all.some((rule) => selectors(rule).includes(s) && /(^|;)\s*position:/.test(rule.body)))
        .map((s) => `${file}: ${s}`);
    });
    expect(unpositioned.filter((entry) => !allowed.includes(entry))).toEqual([]);
  });

  it("gives My words' fields a flex basis only in a row of fields, never in a dialog's column", () => {
    const css = "components/words/Words.module.css";
    expect(rulesFor(css, ".field").filter((rule) => /flex(-basis)?:/.test(rule.body))).toEqual([]);
    expect(rulesFor(css, ".fields > .field")[0]?.body).toMatch(/flex:\s*1 1 14rem/);
  });

  it("lets a scanned line and its Add meaning button wrap, so the text is never squeezed", () => {
    const row = rulesFor("components/viewer/ScannedText.module.css", ".lineRow");
    expect(row.some((rule) => /flex-wrap:\s*wrap/.test(rule.body))).toBe(true);
  });

  it("stops the page scrolling behind an open dialog", () => {
    expect(read("app/globals.css")).toMatch(/html:has\(dialog:modal\)\s*\{\s*overflow:\s*hidden;/);
  });

  it("lets the Usage tiles share the row, so a lone tile is never stuck narrow", () => {
    expect(rulesFor(ADMIN_CSS, ".tiles")[0]?.body).toMatch(/repeat\(auto-fit,/);
  });

  it("never breaks words anywhere in the document page's tables, and never makes them scroll sideways", () => {
    const css = rules(read("app/(app)/documents/[id]/DocumentView.module.css"));
    const anywhere = css.filter((rule) => CELL.test(rule.selector) && /overflow-wrap:\s*anywhere/.test(rule.body));
    expect(anywhere.map((rule) => rule.selector)).toEqual([]);
    const wide = css.filter((rule) => /table|Table/.test(rule.selector) && /min-width:\s*[\d.]+rem/.test(rule.body));
    expect(wide.map((rule) => rule.selector)).toEqual([]);
  });

  it("gives a 24px control 44px on touch screens, unless it is a word inside a line of text", () => {
    const allowed = [
      // A scanned line: the whole row is the button, as tall as its text and padding (over 30px),
      // one under another; 44px each would make a long page's list three times as long.
      "components/viewer/ScannedText.module.css: .lineButton",
    ];
    const named = (found: { file: string; rule: Rule }[]) =>
      found.flatMap(({ file, rule }) => selectors(rule).map((s) => `${file}: ${s}`));
    const coarse = new Set(named(everyRule(/min-height/).filter(({ rule }) => rule.at.includes("pointer: coarse"))));
    const small = named(everyRule(/min-height:\s*24px/).filter(({ rule }) => !rule.at.includes("pointer: coarse")));
    expect(small.filter((entry) => !coarse.has(entry) && !allowed.includes(entry))).toEqual([]);
  });

  it("keeps a focused control in a table clear of the sticky header row (2.4.11)", () => {
    const stickyHead = (file: string) =>
      rules(read(file)).some(
        (rule) => /\bthead\b[^,]*\bth\b/.test(rule.selector) && /position:\s*sticky;\s*top:\s*var\(--topbar-space\)/.test(rule.body),
      );
    const sticky = CSS_FILES.filter(stickyHead);
    expect(sticky.length).toBeGreaterThan(0);
    const clear = (file: string) =>
      rules(read(file)).some((rule) => /\btbody\b/.test(rule.selector) && /scroll-margin-top/.test(rule.body));
    expect(sticky.filter((file) => !clear(file))).toEqual([]);
  });

  it("gives the operator's wide tables' name column room, and the console a wider page", () => {
    expect(rulesFor(OPERATOR_CSS, ".nameWide th:first-child")[0]?.body).toMatch(/width:\s*\d+rem/);
    for (const view of ["ModelBoard", "QualityView", "ModelsView"]) {
      expect(read(`components/operator/${view}.tsx`)).toContain("${table.reflow} ${styles.nameWide}");
    }
    // Changed on purpose (UI quality final review, 15 Oct 2026): `.page.console` so it beats the
    // global .page by specificity, not by stylesheet order.
    expect(rulesFor("components/operator/Insights.module.css", ":global(.page).console")[0]?.body).toMatch(
      /max-width:\s*100rem/,
    );
    expect(read("components/operator/OperatorConsole.tsx")).toContain("`page ${css.console}`");
  });

  it("keeps the chat's loading line in a page, one Upload button on an empty list, and old answers still", () => {
    expect(read("app/(app)/chat/page.tsx")).toMatch(/fallback=\{\s*<div className="page">/);
    expect(read("components/documents/DocumentsView.tsx")).toContain('{view !== "empty" && uploadButton}');
    expect(read("components/chat/AnswerView.tsx")).toContain("export const AnswerView = memo(function AnswerView(");
  });

  it("hatches the edge of a Needs review value row, as a table and as a card, and keeps it when selected", () => {
    const css = "app/(app)/documents/[id]/DocumentView.module.css";
    const HATCH = /background-image:\s*repeating-linear-gradient\(45deg, var\(--review-solid\) 0 4px, transparent 4px 8px\);/;
    const table = rulesFor(css, '.valuesTable tr[data-label="needs_review"] td:first-child').filter((rule) => HATCH.test(rule.body));
    expect(table.map((rule) => rule.at)).toEqual(["@container (min-width: 36rem)"]);
    const card = rulesFor(css, '.valuesTable tr[data-label="needs_review"]').filter((rule) => HATCH.test(rule.body));
    expect(card.map((rule) => rule.at)).toEqual(["@container (max-width: 35.99rem)"]);
    // Selection sets only the colour, so the hatch stays.
    const selected = [...rulesFor(css, ".activeRow td"), ...rulesFor(css, ".valuesTable tbody tr.activeRow")];
    expect(selected.filter((rule) => /background-color:\s*var\(--accent-soft\)/.test(rule.body))).toHaveLength(2);
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toContain("data-label={value.label}");
  });

  it("on a phone, hides a button's extra words the way visually-hidden does, so its name stays whole", () => {
    const hidden = rulesFor("app/globals.css", ".visually-hidden")[0]?.body.trim();
    const narrow = rulesFor("app/globals.css", ".narrow-hide");
    expect(narrow.map((rule) => rule.at)).toEqual(["@media (max-width: 639.98px)"]);
    const declarations = (body = "") => body.split(";").map((part) => part.trim()).filter(Boolean);
    expect(declarations(narrow[0]?.body)).toEqual(declarations(hidden));
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain('<Icon name="download" />\n              <span className="narrow-hide">Download original</span>');
    expect(view).toContain('Scanned<span className="narrow-hide"> text</span>');
    expect(read("components/viewer/PageViewer.tsx")).toContain('Fit<span className="narrow-hide">{fit === "page" ? " page" : " width"}</span>');
    const phone = rules(read("app/(app)/documents/[id]/DocumentView.module.css")).filter((rule) => rule.at === "@media (max-width: 639.98px)");
    expect(phone.find((rule) => rule.selector === ".download")?.body).toMatch(/margin-inline-start:\s*0;/);
    expect(phone.find((rule) => rule.selector === ".ask")?.body).toMatch(/flex:\s*1 1 auto;/);
  });

  it("puts the document's status, folder, tags and versions on one meta line under the title (board 12 P4)", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    const between = (from: string, to: string) => view.slice(view.indexOf(from), view.indexOf(to, view.indexOf(from)));
    // The title row: the title, then Download original and Ask about this document, the one primary.
    const title = between("<div className={styles.titleRow}>", "<div className={styles.status}>");
    expect(title).toContain("<h1 className={styles.title}>");
    expect(title).toContain("Download original");
    expect(title).toMatch(/btn btn-primary \$\{styles\.ask\}[\s\S]*Ask about this document/);
    // The meta line holds everything else, in reading order; no second "place" row.
    const meta = between("<div className={styles.status}>", "{note && ");
    const order = ["<StatusChip", "pagesText", "<ReadAgain", 'aria-label="Folder"', 'id="document-tags-label"', 'aria-label="Versions"'];
    const at = order.map((part) => meta.indexOf(part));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(view).not.toContain("styles.place}");
    const css = "app/(app)/documents/[id]/DocumentView.module.css";
    expect(rulesFor(css, ".status")[0]?.body).toMatch(/flex-wrap:\s*wrap;/);
    // Read again no longer pushes the rest of the line away; its question and error still take their own row.
    expect(rulesFor(css, ".readAgain")).toEqual([]);
    expect(rulesFor(css, ".readAgainConfirm")[0]?.body).toMatch(/flex:\s*1 1 100%;/);
    expect(rulesFor(css, ".readAgainLine")[0]?.body).toMatch(/flex:\s*1 1 100%;/);
    // On a phone the title has a row of its own, so Ask never squeezes it.
    const phone = rules(read(css)).filter((rule) => rule.at === "@media (max-width: 639.98px)");
    expect(phone.find((rule) => rule.selector === ".title")?.body).toMatch(/flex-basis:\s*100%;/);
  });

  it("paints an older chat answer only near view, never the newest four, and never on the card itself", () => {
    const CHAT_CSS = "components/chat/Chat.module.css";
    const lazy = rules(read(CHAT_CSS)).filter((rule) => /content-visibility/.test(rule.body));
    // On the wrapper (the card's ::before edge stays unclipped), the newest four answers left out.
    expect(lazy.map((rule) => `${rule.at}|${rule.selector}`)).toEqual([
      "|.log > .answerWrap:not(:nth-last-child(-n + 4 of .answerWrap))",
    ]);
    expect(lazy[0]?.body).toMatch(/content-visibility:\s*auto;/);
    // "auto": an answer seen once keeps its real height while off screen.
    expect(lazy[0]?.body).toMatch(/contain-intrinsic-block-size:\s*auto \d+px;/);
    const chat = read("components/chat/ChatScreen.tsx");
    expect(chat).toMatch(/<div className=\{styles\.answerWrap\}>\s*<AnswerView /);
    // Opening a conversation pins its end again after the first paint, when real heights are in.
    expect(chat).toContain("frame = requestAnimationFrame(pin);");
    // A chip pointed at re-renders two source cards, and typing a search never re-renders the list.
    expect(read("components/chat/SourceCard.tsx")).toContain("export const SourceCard = memo(function SourceCard(");
    const docs = read("components/documents/DocumentsView.tsx");
    expect(docs).toContain("defaultValue={q}");
    expect(docs).not.toMatch(/value=\{draft\}/);
  });

  it("lines money columns up at the end, after the table's own alignment so it wins, and lets them wrap", () => {
    const all = rules(read("app/globals.css"));
    const num = all.findIndex((rule) => selectors(rule).includes("td.num") && selectors(rule).includes("th.num"));
    const table = all.findIndex((rule) => selectors(rule).includes(".table td") && /text-align/.test(rule.body));
    expect(num).toBeGreaterThan(table);
    expect(table).toBeGreaterThanOrEqual(0);
    expect(all[num]?.body).toMatch(/text-align:\s*end;[\s\S]*font-variant-numeric:\s*tabular-nums;/);
    expect(all[num]?.body).not.toMatch(NOWRAP);
    // The number columns, header and cells. Changed on purpose (UI quality council, 15 Oct 2026):
    // the org admin's Usage tables count tokens, not cost (Tushar, 17:40).
    const usage = read("components/admin/UsagePanel.tsx");
    expect(usage.match(/<th scope="col" role="columnheader" className="num">\s*Tokens/g)).toHaveLength(2);
    expect(usage.match(/data-label="Tokens" className=\{?[`"]num\b/g)).toHaveLength(2);
    const spend = read("components/operator/SpendView.tsx");
    expect(spend).toContain('<th scope="col" role="columnheader" className="num">Cost</th>');
    expect(spend).toContain('data-label="Cost" className="num"');
    const overview = read("components/operator/OverviewView.tsx");
    expect(overview).toContain('className={column.id === "spend" ? "num" : undefined}');
    expect(overview).toContain('data-label="AI spend this month" className="num"');
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx").match(/className=\{MONEY_LINE_FIELDS\.has\(field\) \? "num" : undefined\}/g)).toHaveLength(2);
  });
});
