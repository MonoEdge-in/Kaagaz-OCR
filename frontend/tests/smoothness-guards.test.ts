// Smoothness pass (Tushar, 17 Oct 2026): listeners never block scrolling, inner scrollers
// never chain to the page, no window units that make a sideways scroll, the phone's bars
// keep clear of the home indicator, and a tap on touch leaves no hover colour behind.
// (Transitions and keyframes on layout properties are guarded in motion.test.ts.)

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const files = (dir: string, ending: RegExp): string[] =>
  readdirSync(join(ROOT, dir)).flatMap((name) => {
    const path = `${dir}/${name}`;
    return statSync(join(ROOT, path)).isDirectory() ? files(path, ending) : ending.test(name) ? [path] : [];
  });
const CSS = [...files("app", /\.css$/), ...files("components", /\.css$/)];
const CODE = [...files("app", /\.tsx?$/), ...files("components", /\.tsx?$/), ...files("lib", /\.tsx?$/)];
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
/** Innermost rules: [selector, body]. */
const rules = (text: string) => [...text.matchAll(/([^{}]*)\{([^{}]*)\}/g)].map((m) => [(m[1] ?? "").trim(), m[2] ?? ""] as const);

describe("smoothness guards", () => {
  it("adds every scroll, wheel, touch and resize listener as passive (the viewer's Ctrl+wheel zoom aside)", () => {
    const found: string[] = [];
    for (const file of CODE) {
      for (const [call] of read(file).matchAll(/addEventListener\(\s*"(?:scroll|wheel|touchstart|touchmove|resize)"[^)]*\)/g)) {
        if (file === "components/viewer/PageViewer.tsx" && call.includes('"wheel", listener, { passive: false }')) continue;
        if (!/passive:\s*true/.test(call)) found.push(`${file}: ${call}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("holds every inner scroller in (overscroll-behavior beside its overflow)", () => {
    const found: string[] = [];
    for (const file of CSS) {
      for (const [selector, body] of rules(css(file))) {
        if (/overflow(-[xy])?\s*:\s*(auto|scroll)/.test(body) && !/overscroll-behavior/.test(body)) found.push(`${file}: ${selector}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("never sizes by 100vw, and uses 100vh only as the fallback line before 100dvh", () => {
    const found: string[] = [];
    for (const file of CSS) {
      const text = css(file);
      if (/100vw/.test(text)) found.push(`${file}: 100vw`);
      for (const m of text.matchAll(/([\w-]+):\s*100vh;\s*/g)) {
        if (!text.slice((m.index ?? 0) + m[0].length).startsWith(`${m[1]}: 100dvh;`)) found.push(`${file}: ${m[0].trim()}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("keeps the phone's bottom bar, drawer and sheet clear of the home indicator", () => {
    const bar = rules(css("components/shell/Shell.module.css")).find(([s]) => s === ".bottombar")?.[1] ?? "";
    expect(bar).toMatch(/padding-bottom:\s*env\(safe-area-inset-bottom\)/);
    expect(css("components/ui/Drawer.module.css")).toMatch(/env\(safe-area-inset-bottom\)/);
    expect(css("components/documents/Documents.module.css")).toMatch(/\.trayPanel \{[^}]*env\(safe-area-inset-bottom\)/);
  });

  it("shows the shared hover colours with a mouse only, so a tap leaves none behind", () => {
    for (const [file, selectors] of [
      ["app/globals.css", [".btn:hover", ".btn-primary:hover", ".btn-quiet:hover", ".tab:hover", ".input:hover"]],
      ["components/shell/Shell.module.css", [".link:hover", ".bottomLink:hover"]],
      ["components/documents/Documents.module.css", ["table.docs > tbody > tr:hover"]],
    ] as const) {
      const text = css(file);
      for (const selector of selectors) {
        const escaped = selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&");
        expect(text, `${file}: ${selector}`).toMatch(new RegExp(`@media \\(hover: hover\\) and \\(pointer: fine\\) \\{\\s*${escaped} \\{`));
      }
    }
  });

  it("never lets the document page's actions push <main> sideways at 320px", () => {
    const head = rules(css("app/(app)/documents/[id]/DocumentView.module.css")).find(([s]) => s === ".headActions")?.[1] ?? "";
    expect(head).toMatch(/max-width:\s*100%/);
  });

  it("never breaks the wordmark mid-word in a narrow header", () => {
    const text = rules(css("components/shell/Wordmark.module.css"));
    expect(text.find(([s]) => s === ".wordmark")?.[1]).toMatch(/flex:\s*none/);
    expect(text.find(([s]) => s === ".name")?.[1]).toMatch(/white-space:\s*nowrap/);
  });
});
