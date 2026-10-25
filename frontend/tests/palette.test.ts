// Colour palettes (Tushar, 25 Oct 2026): six palettes, a choice for this person only, kept
// like the theme in a plain cookie the root layout reads, so the first paint is right.
// Each palette re-sets the brand family in light and dark and stays WCAG AA; status colours
// never change and no accent is near their hues; no screen hard-codes the accent.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PalettePicker } from "@/components/ui/PalettePicker";
import { PaletteProvider } from "@/components/ui/PaletteProvider";
import {
  applyPalette,
  PALETTE_CHOICES,
  PALETTE_COOKIE,
  PALETTE_LABELS,
  paletteAttribute,
  paletteCookie,
  parsePalette,
} from "@/lib/palette";
import type { PaletteChoice } from "@/lib/palette";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name in cookieJar ? { value: cookieJar[name] } : undefined) }),
}));
vi.mock("next/font/google", () => {
  const face = () => ({ variable: "font" });
  return { Archivo: face, IBM_Plex_Mono: face, IBM_Plex_Sans: face, IBM_Plex_Sans_Devanagari: face };
});

describe("palette choice", () => {
  it("offers six palettes with plain names, Kaagaz blue first", () => {
    expect(PALETTE_CHOICES).toEqual(["kaagaz", "ocean", "teal", "violet", "plum", "graphite"]);
    expect(PALETTE_CHOICES.map((p) => PALETTE_LABELS[p])).toEqual([
      "Kaagaz blue",
      "Ocean",
      "Teal",
      "Violet",
      "Plum",
      "Graphite",
    ]);
  });

  it("keeps only the known palettes; anything else is Kaagaz blue", () => {
    for (const choice of PALETTE_CHOICES) expect(parsePalette(choice)).toBe(choice);
    for (const value of [undefined, null, "", "Teal", "red", "<script>", "teal; x=1", "constructor", "__proto__"]) {
      expect(parsePalette(value)).toBe("kaagaz");
    }
  });

  it("writes data-palette only for a palette other than Kaagaz blue", () => {
    expect(paletteAttribute("kaagaz")).toBeUndefined();
    expect(paletteAttribute("plum")).toBe("plum");
  });

  it("stores only the palette id, app-wide for a year, readable by the page, Secure on https", () => {
    expect(paletteCookie("teal", false)).toBe(`${PALETTE_COOKIE}=teal; Path=/; Max-Age=31536000; SameSite=Lax`);
    expect(paletteCookie("teal", true)).toMatch(/; Secure$/);
    expect(paletteCookie("teal", true)).not.toMatch(/HttpOnly/i);
    expect(PALETTE_COOKIE).toBe("kaagaz_palette");
  });

  afterEach(() => vi.unstubAllGlobals());

  it("applies a choice to <html> at once and writes the cookie", () => {
    const attributes = new Map<string, string>();
    const doc = {
      cookie: "",
      documentElement: {
        setAttribute: (name: string, value: string) => attributes.set(name, value),
        removeAttribute: (name: string) => attributes.delete(name),
      },
    };
    vi.stubGlobal("document", doc);
    vi.stubGlobal("window", { location: { protocol: "https:" } });
    applyPalette("violet");
    expect(attributes.get("data-palette")).toBe("violet");
    expect(doc.cookie).toBe(paletteCookie("violet", true));
    applyPalette("kaagaz");
    expect(attributes.has("data-palette")).toBe(false);
    expect(doc.cookie).toBe(paletteCookie("kaagaz", true));
  });
});

describe("the root layout", () => {
  async function html(cookies: Record<string, string>): Promise<ReactElement<Record<string, unknown>>> {
    cookieJar = cookies;
    const { default: RootLayout } = await import("@/app/layout");
    const element = (await RootLayout({ children: null })) as ReactNode;
    expect(isValidElement(element)).toBe(true);
    return element as ReactElement<Record<string, unknown>>;
  }

  it("writes the cookie's palette as data-palette on <html> for the first paint", async () => {
    expect((await html({ [PALETTE_COOKIE]: "teal" })).props["data-palette"]).toBe("teal");
    expect((await html({ [PALETTE_COOKIE]: "graphite", kaagaz_theme: "dark" })).props).toMatchObject({
      "data-palette": "graphite",
      "data-theme": "dark",
    });
  });

  it("writes no data-palette for Kaagaz blue, no cookie or a tampered one", async () => {
    const jars: Record<string, string>[] = [{}, { [PALETTE_COOKIE]: "kaagaz" }, { [PALETTE_COOKIE]: "hotpink" }];
    for (const cookies of jars) {
      expect((await html(cookies)).props["data-palette"]).toBeUndefined();
    }
  });
});

describe("the Colours picker", () => {
  const render = (initial: PaletteChoice) =>
    renderToStaticMarkup(
      createElement(PaletteProvider, { initial } as Parameters<typeof PaletteProvider>[0], createElement(PalettePicker)),
    );

  it("shows six named radios in one group, the current one checked", () => {
    const markup = render("teal");
    expect(markup).toMatch(/<fieldset[^>]*><legend[^>]*>Colours<\/legend>/);
    const radios = Array.from(markup.matchAll(/<input type="radio"[^>]*>/g), (m) => m[0]);
    expect(radios).toHaveLength(6);
    expect(new Set(radios.map((r) => /name="([^"]+)"/.exec(r)?.[1])).size).toBe(1);
    expect(radios.map((r) => /value="([^"]+)"/.exec(r)?.[1])).toEqual([...PALETTE_CHOICES]);
    expect(radios.filter((r) => /\schecked=""/.test(r)).map((r) => /value="([^"]+)"/.exec(r)?.[1])).toEqual(["teal"]);
    for (const choice of PALETTE_CHOICES) {
      expect(markup).toContain(`<span>${PALETTE_LABELS[choice]}</span>`);
      expect(markup).toContain(`background:var(--k-swatch-${choice})`);
    }
  });

  it("checks Kaagaz blue by default", () => {
    const checked = /<input type="radio"[^>]*checked=""[^>]*>/.exec(render("kaagaz"))?.[0];
    expect(checked).toContain('value="kaagaz"');
  });

  it("sits right after the theme switch in the account menu, for every signed-in person", () => {
    const menu = read("components/shell/AccountMenu.tsx");
    expect(menu).toMatch(/<ThemeToggle \/>\s*(\{\/\*[^]*?\*\/\}\s*)?<PalettePicker \/>/);
    // No role or permission check around it.
    const at = menu.indexOf("<PalettePicker />");
    expect(menu.slice(at - 200, at)).not.toMatch(/role|can_|isAdmin/);
  });

  it("marks the current one by a lift and a ring, shows focus, and hovers only with a mouse", () => {
    const css = read("components/ui/PalettePicker.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).toMatch(/\.option:has\(\.radio:checked\) \{[^}]*box-shadow: var\(--k-shadow-1\);/);
    expect(css).toMatch(/\.option:has\(\.radio:checked\) \.swatch \{[^}]*outline: 2px solid var\(--k-ink\);/);
    expect(css).toMatch(/\.option:has\(\.radio:focus-visible\) \{[^}]*outline: 2px solid var\(--focus\);/);
    expect(css).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{\s*\.option:hover \{/);
    expect(css.match(/:hover/g)).toHaveLength(1);
  });
});

// --- The colours themselves -------------------------------------------------------------

const TOKENS = read("app/styles/tokens.css");
const GLOBALS = read("app/globals.css");

/** The custom properties of the first block in `css` opening with exactly `head {`. */
function block(css: string, head: string): Record<string, string> {
  const start = css.indexOf(`${head} {`);
  expect(start, head).toBeGreaterThanOrEqual(0);
  const body = css.slice(start + head.length + 2, css.indexOf("}", start)).replace(/\/\*[\s\S]*?\*\//g, "");
  return Object.fromEntries(Array.from(body.matchAll(/(--[\w-]+):\s*([^;]+);/g), (m) => [m[1]!, m[2]!.trim()]));
}

type Mode = "light" | "dark";
const MODES: Mode[] = ["light", "dark"];
const LIGHT_BASE = { ...block(GLOBALS, ":root"), ...block(TOKENS, ':root,\n[data-theme="light"]') };
const DARK_BASE = {
  ...LIGHT_BASE,
  ...block(GLOBALS, ':root[data-theme="dark"]'),
  ...block(TOKENS, '[data-theme="dark"]'),
};
const OTHERS = PALETTE_CHOICES.filter((p) => p !== "kaagaz");
const lightOf = (p: PaletteChoice) => block(TOKENS, `:root[data-palette="${p}"]`);
const darkOf = (p: PaletteChoice) => block(TOKENS, `:root[data-palette="${p}"][data-theme="dark"]`);
const systemOf = (p: PaletteChoice) => block(TOKENS, `  :root[data-palette="${p}"]:not([data-theme="light"])`);

/** Every token a palette and mode ends up with: the theme's, then the palette's on top. */
function tokens(palette: PaletteChoice, mode: Mode): Record<string, string> {
  if (palette === "kaagaz") return mode === "light" ? LIGHT_BASE : DARK_BASE;
  return mode === "light" ? { ...LIGHT_BASE, ...lightOf(palette) } : { ...DARK_BASE, ...lightOf(palette), ...darkOf(palette) };
}

function hex(t: Record<string, string>, name: string): string {
  const raw = t[name];
  expect(raw, name).toBeDefined();
  const alias = /^var\((--[\w-]+)\)$/.exec(raw ?? "");
  if (alias) return hex(t, alias[1]!);
  expect(raw, name).toMatch(/^#[0-9a-fA-F]{6}$/);
  return raw!;
}
const channel = (pair: string) => {
  const v = parseInt(pair, 16) / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
const luminance = (h: string) =>
  0.2126 * channel(h.slice(1, 3)) + 0.7152 * channel(h.slice(3, 5)) + 0.0722 * channel(h.slice(5, 7));
const ratio = (t: Record<string, string>, a: string, b: string) => {
  const [x, y] = [luminance(hex(t, a)), luminance(hex(t, b))];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
const hue = (h: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  if (d === 0) return 0;
  const raw = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (raw * 60 + 360) % 360;
};

// Text and button labels 4.5:1; the focus ring, nav icon and chart bar 3:1 (WCAG 1.4.3, 1.4.11).
const TEXT: [string, string][] = [
  ["--accent", "--bg-surface"],
  ["--accent", "--bg-page"],
  ["--accent", "--bg-subdued"],
  ["--accent", "--bg-hover"],
  ["--accent", "--accent-subtle"],
  ["--text-onaccent", "--accent"],
  ["--text-onaccent", "--accent-hover"],
  ["--text-onaccent", "--critical"],
  ["--k-accent", "--k-surface"],
  ["--k-accent", "--k-ground"],
  ["--k-accent", "--k-accent-soft"],
  ["--k-accent-ink", "--k-accent"],
  ["--k-accent-ink", "--k-accent-hover"],
  ["--k-accent-soft-ink", "--k-accent-soft"],
  ["--k-inverse-accent", "--k-inverse-bg"],
  ["--k-ink-faint", "--k-ground"],
  ["--review-on-solid", "--warning"],
  ...["--bg-page", "--bg-subdued", "--bg-hover", "--bg-selected"].flatMap((ground) =>
    ["--text-primary", "--text-secondary", "--text-muted"].map((text): [string, string] => [text, ground]),
  ),
  ...["--bg-surface", "--bg-page", "--bg-subdued"].flatMap((ground) =>
    ["--success", "--warning", "--critical"].map((status): [string, string] => [status, ground]),
  ),
];
const UI: [string, string][] = [
  ...["--bg-surface", "--bg-page", "--bg-subdued", "--accent-subtle"].map((g): [string, string] => ["--focus", g]),
  ["--k-focus", "--k-surface"],
  ["--k-focus", "--k-ground"],
  ["--k-nav-accent", "--k-nav-bg"],
  ["--k-nav-accent", "--k-nav-active"],
  ["--k-chart-1", "--k-chart-track"],
];

describe("palette tokens", () => {
  it("leaves Kaagaz blue exactly as it was: no rule for it", () => {
    expect(TOKENS).not.toContain('data-palette="kaagaz"');
    expect(LIGHT_BASE["--accent"]).toBe("#1e4396");
    expect(DARK_BASE["--accent"]).toBe("#8fa8f0");
  });

  it.each(OTHERS)("sets the same tokens in light, picked dark and system dark for %s", (palette) => {
    const names = Object.keys(lightOf(palette)).sort();
    expect(names.length).toBeGreaterThan(20);
    expect(Object.keys(darkOf(palette)).sort()).toEqual(names);
    expect(systemOf(palette)).toEqual(darkOf(palette));
    expect(TOKENS).toContain(`@media (prefers-color-scheme: dark) {\n  :root[data-palette="${palette}"]:not([data-theme="light"]) {`);
  });

  it.each(OTHERS)("never changes a status or label colour in %s", (palette) => {
    const status = /^--(k-)?(verified|review|danger|success|warning|critical|lab-|label-|diff-|mark-unsure|mark-unreadable|hl-|words-on|mine-on)/;
    for (const name of [...Object.keys(lightOf(palette)), ...Object.keys(darkOf(palette))]) {
      expect(name).not.toMatch(status);
    }
  });

  it("gives every palette's circle its own accent", () => {
    // The swatch blocks come after the theme's own blocks of the same names.
    const swatches = TOKENS.slice(TOKENS.indexOf("/* Each palette's own accent"));
    const light = block(swatches, ":root");
    const dark = block(swatches, '[data-theme="dark"]');
    expect(block(swatches, '  :root:not([data-theme="light"])')).toEqual(dark);
    for (const palette of PALETTE_CHOICES) {
      expect(light[`--k-swatch-${palette}`], palette).toBe(hex(tokens(palette, "light"), "--k-accent"));
      expect(dark[`--k-swatch-${palette}`], palette).toBe(hex(tokens(palette, "dark"), "--k-accent"));
    }
  });

  for (const palette of PALETTE_CHOICES) {
    for (const mode of MODES) {
      it(`${palette} in ${mode}: text 4.5:1, focus ring and UI parts 3:1`, () => {
        const t = tokens(palette, mode);
        for (const [a, b] of TEXT) expect(ratio(t, a, b), `${a} on ${b}`).toBeGreaterThanOrEqual(4.5);
        for (const [a, b] of UI) expect(ratio(t, a, b), `${a} on ${b}`).toBeGreaterThanOrEqual(3);
      });

      it(`${palette} in ${mode}: the accent is never near the status colours' hues`, () => {
        const t = tokens(palette, mode);
        const accent = hue(hex(t, "--accent"));
        for (const status of ["--success", "--warning", "--critical"]) {
          const d = Math.abs(accent - hue(hex(t, status)));
          expect(Math.min(d, 360 - d), status).toBeGreaterThanOrEqual(30);
        }
      });
    }
  }
});

describe("no hard-coded accent", () => {
  // The Kaagaz blue family. Only the token files hold it, and app/global-error.tsx: it replaces the
  // root layout when that fails, so neither the stylesheets nor the cookie reach it.
  const FAMILY = /#(1e4396|17357a|e8ecf7|8fa8f0|b0c2f5|1c2541|0b1430|2a55c0|2a4a9e|9db4f0|9db0dd|3a4e86|b7c6f6)\b|rgba?\(\s*(30,\s*67,\s*150|143,\s*168,\s*240)\b/i;
  const ALLOWED = ["app/globals.css", "app/styles/tokens.css", "app/global-error.tsx"];
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? walk(path) : /\.(css|tsx?)$/.test(name) ? [path] : [];
    });

  it("takes the accent from tokens in every component, so a palette reaches it", () => {
    const offenders = ["app", "components", "lib"]
      .flatMap((dir) => walk(join(ROOT, dir)))
      .map((path) => relative(ROOT, path).replaceAll("\\", "/"))
      .filter((path) => !ALLOWED.includes(path) && FAMILY.test(read(path)));
    expect(offenders).toEqual([]);
  });
});
