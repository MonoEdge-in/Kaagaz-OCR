import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseTheme, THEME_COOKIE, themeAttribute, themeCookie } from "@/lib/theme";

describe("theme choice", () => {
  it("keeps only the known choices; anything else follows the system", () => {
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("dark")).toBe("dark");
    for (const value of [undefined, null, "", "system", "Dark", "blue", "<script>", "dark; x=1"]) {
      expect(parseTheme(value)).toBe("system");
    }
  });

  it("writes data-theme only for an explicit choice", () => {
    expect(themeAttribute("light")).toBe("light");
    expect(themeAttribute("dark")).toBe("dark");
    expect(themeAttribute("system")).toBeUndefined();
  });

  it("stores the choice app-wide for a year, Secure on https", () => {
    expect(themeCookie("dark", false)).toBe(
      `${THEME_COOKIE}=dark; Path=/; Max-Age=31536000; SameSite=Lax`,
    );
    expect(themeCookie("light", true)).toMatch(/; Secure$/);
  });
});

// Changed on purpose (MonoEdge design system, 16 Oct 2026): the owner's rules
// file replaces the monoedge.in look. Its tokens exactly, the old names kept as
// aliases, every text and edge pair still WCAG 2.2 AA in both themes, JetBrains
// Mono with the display fallback stack, and flat, square, hairline styles.
describe("MonoEdge design system", () => {
  const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
  const css = read("app/globals.css");
  /** The custom properties set in the block that starts with `opening`. */
  const block = (opening: string): Record<string, string> => {
    const start = css.indexOf(opening);
    expect(start, opening).toBeGreaterThanOrEqual(0);
    const body = css.slice(start + opening.length, css.indexOf("}", start));
    return Object.fromEntries(Array.from(body.matchAll(/(--[\w-]+):\s*([^;]+);/g), (m) => [m[1], (m[2] ?? "").trim()]));
  };
  const light = block(":root {");
  const dark = { ...light, ...block(':root[data-theme="dark"] {') };
  /** A token's value with var() aliases followed to the end. */
  const value = (t: Record<string, string>, name: string): string => {
    const raw = t[name];
    expect(raw, name).toBeDefined();
    const alias = /^var\((--[\w-]+)\)$/.exec(raw ?? "");
    return alias ? value(t, alias[1] ?? "") : (raw ?? "");
  };
  const channel = (pair: string) => {
    const v = parseInt(pair, 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (hex: string) =>
    0.2126 * channel(hex.slice(1, 3)) + 0.7152 * channel(hex.slice(3, 5)) + 0.0722 * channel(hex.slice(5, 7));
  const ratio = (t: Record<string, string>, a: string, b: string) => {
    const [x, y] = [luminance(value(t, a)), luminance(value(t, b))];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };

  const LIGHT = {
    "--bg-page": "#fafbfc", "--bg-surface": "#ffffff", "--bg-subdued": "#f4f5f7", "--bg-hover": "#f4f5f7",
    "--bg-selected": "#f4f7fd", "--text-primary": "#101114", "--text-secondary": "#4e545c", "--text-muted": "#697079",
    "--text-onaccent": "#ffffff", "--accent": "#204494", "--accent-hover": "#264e9f", "--accent-subtle": "#e9f0fa",
    "--border": "#e8eaed", "--border-strong": "#d5d8dd", "--focus": "#204494",
    "--success": "#1e7a3c", "--warning": "#b7791f", "--critical": "#b3261e",
  };
  const DARK = {
    "--bg-page": "#08090a", "--bg-surface": "#101114", "--bg-subdued": "#1a1c1f", "--bg-hover": "#24272b",
    "--bg-selected": "#172f65", "--text-primary": "#ffffff", "--text-secondary": "#b3b8c0", "--text-muted": "#8a9099",
    "--text-onaccent": "#000000", "--accent": "#85a9dd", "--accent-hover": "#a3bee7", "--accent-subtle": "#172f65",
    "--border": "#24272b", "--border-strong": "#33383e", "--focus": "#85a9dd",
    "--success": "#4fb570", "--warning": "#e0a93a", "--critical": "#ef6e63",
  };

  it("sets every MonoEdge colour token, in light and in both dark selectors", () => {
    expect(light).toMatchObject(LIGHT);
    expect(dark).toMatchObject(DARK);
    // The system's dark and the picked Dark are the same colours.
    expect(block(':root:not([data-theme="light"]) {')).toEqual(block(':root[data-theme="dark"] {'));
  });

  it("keeps the old token names as aliases of the new ones", () => {
    expect(light).toMatchObject({
      "--bg": "var(--bg-page)",
      "--surface": "var(--bg-surface)",
      "--surface-2": "var(--bg-subdued)",
      "--fg": "var(--text-primary)",
      "--fg-muted": "var(--text-secondary)",
      "--accent-fg": "var(--text-onaccent)",
      "--accent-soft": "var(--accent-subtle)",
      "--verified-solid": "var(--success)",
      "--review-solid": "var(--warning)",
      "--danger-fg": "var(--critical)",
      "--font-sans": "var(--font-mono)",
    });
  });

  it.each([
    ["light", light],
    ["dark", dark],
  ])("keeps text at 4.5:1 and edges at 3:1 in %s", (_, t) => {
    for (const ground of ["--bg", "--surface", "--surface-2"]) {
      for (const text of ["--fg", "--fg-muted", "--text-muted", "--accent"]) {
        expect(ratio(t, text, ground), `${text} on ${ground}`).toBeGreaterThanOrEqual(4.5);
      }
      for (const edge of ["--control-edge", "--focus"]) expect(ratio(t, edge, ground), edge).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t, "--accent-fg", "--accent")).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t, "--accent", "--accent-soft")).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t, "--text-onaccent", "--critical")).toBeGreaterThanOrEqual(4.5);
    for (const label of ["verified", "review", "danger"]) {
      expect(ratio(t, `--${label}-fg`, `--${label}-bg`), label).toBeGreaterThanOrEqual(4.5);
    }
    expect(ratio(t, "--review-solid", "--surface")).toBeGreaterThanOrEqual(3);
  });

  it("uses the 4px spacing scale, three control heights and one 2px radius", () => {
    expect(light).toMatchObject({
      "--space-1": "4px", "--space-2": "8px", "--space-3": "12px", "--space-4": "16px",
      "--space-6": "24px", "--space-8": "32px", "--space-12": "48px", "--space-16": "64px",
      "--h-sm": "28px", "--h-md": "36px", "--h-lg": "44px",
      "--r-sm": "2px", "--r-md": "2px", "--r-full": "2px",
      "--e-1": "none", "--e-2": "none",
    });
    const radii = Array.from(css.matchAll(/border-radius:\s*([^;]+);/g), (m) => m[1]);
    // Square everywhere but the round spinners.
    expect(radii.filter((r) => !["2px", "1px", "50%", "var(--r-sm)", "var(--r-md)"].includes(r ?? ""))).toEqual([]);
  });

  it("sets the grid, the shell's zones and the layer scale (brief C)", () => {
    expect(light).toMatchObject({
      "--grid-columns": "12", "--gutter": "24px", "--margin": "32px", "--content-max": "1200px",
      "--header-h": "56px", "--toolbar-h": "48px", "--statusbar-h": "32px", "--sidebar-w": "240px",
      "--z-base": "0", "--z-sticky": "10", "--z-dropdown": "20", "--z-overlay": "30",
      "--z-modal": "40", "--z-toast": "50", "--z-tooltip": "60",
      "--ease": "cubic-bezier(0.2, 0, 0.2, 1)",
    });
    expect(block("@media (max-width: 767.98px) {\n  :root {")).toMatchObject({
      "--gutter": "16px", "--margin": "16px", "--header-h": "48px", "--bottombar-h": "56px",
    });
  });

  it("has no shadow, glow, gradient or backdrop blur in the global styles", () => {
    const code = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(Array.from(code.matchAll(/box-shadow:\s*([^;]+);/g), (m) => m[1])).toEqual([]);
    expect(code).not.toMatch(/gradient\(|text-shadow|backdrop-filter|filter:\s*drop-shadow/);
  });

  it("styles the focus ring, labels and numbers as the rules say", () => {
    expect(css).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--focus\);\s*outline-offset: 2px;\s*\}/);
    expect(css).toMatch(/\.field > label,\s*\.label \{\s*font-size: var\(--fs-label\);\s*font-weight: 700;\s*letter-spacing: 0\.08em;\s*text-transform: uppercase;/);
    expect(light["--fs-label"]).toBe("0.6875rem");
    expect(css).toMatch(/body \{[^}]*font-variant-numeric: tabular-nums;/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*html \{\s*scroll-behavior: smooth;/);
  });

  it("tells Verified from Needs review by a solid or dashed edge, never a pill", () => {
    expect(css).toMatch(/\.badge-verified \{[^}]*border: 1px solid var\(--verified-solid\);/);
    expect(css).toMatch(/\.badge-review \{[^}]*border: 1px dashed var\(--review-solid\);/);
    const chat = read("components/chat/Chat.module.css");
    expect(chat).toMatch(/\.answerCard\[data-label="needs_review"\] \{\s*border-style: dashed;/);
  });

  it("loads JetBrains Mono only, and uses the display fallback stack, with Devanagari faces after them", () => {
    const layout = read("app/layout.tsx");
    expect(layout).toContain('import { JetBrains_Mono } from "next/font/google";');
    expect(layout).not.toMatch(/\bInter\b|Archivo/);
    expect(layout).toContain('variable: "--font-jetbrains-mono"');
    expect(layout).toContain('display: "swap"');
    expect(light["--font-mono"]?.startsWith('var(--font-jetbrains-mono, "JetBrains Mono"),')).toBe(true);
    expect(light["--font-display"]?.startsWith('"Monument Extended", "Arial Narrow", "Helvetica Neue", Arial,')).toBe(true);
    for (const token of ["--font-mono", "--font-display"]) {
      expect(light[token], token).toMatch(/"Noto Sans Devanagari",\s*"Nirmala UI"/);
    }
    expect(css).not.toMatch(/--font-inter|--font-archivo/);
  });

  it("signs Kaagaz by MonoEdge on the wordmark and the sign-in frame", () => {
    expect(read("components/shell/Wordmark.tsx")).toContain("by MonoEdge</span>");
    const frame = read("app/login/AuthFrame.tsx");
    expect(frame).toContain("by MonoEdge</span>");
    expect(frame).toContain("&copy; 2026 MonoEdge · Pune &amp; Jalna");
  });
});
