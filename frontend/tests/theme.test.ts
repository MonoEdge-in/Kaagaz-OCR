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

// Changed on purpose (UI rebuild, 21 Oct 2026): the handoff (design/kaagaz-ui-handoff)
// replaces the flat, square MonoEdge rules file: its colours, faces, radii and soft
// shadows. Every pair is still measured for WCAG 2.2 AA in both themes.
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

  // The handoff's colours (styles/kaagaz.css, sections 1 and 1b).
  const LIGHT = {
    "--bg-page": "#f6f5f1", "--bg-surface": "#ffffff", "--bg-subdued": "#efede6", "--bg-hover": "#efede6",
    "--bg-selected": "#e8ecf7", "--text-primary": "#0e0e10", "--text-secondary": "#5b5b60", "--text-muted": "#66666b",
    "--text-onaccent": "#ffffff", "--accent": "#1e4396", "--accent-hover": "#17357a", "--accent-subtle": "#e8ecf7",
    "--border": "#e6e3da", "--border-strong": "#cfcbc0", "--focus": "#1e4396",
    "--success": "#1b7a4b", "--warning": "#8a5800", "--critical": "#b42318",
  };
  const DARK = {
    "--bg-page": "#111113", "--bg-surface": "#18181b", "--bg-subdued": "#1f1f23", "--bg-hover": "#232327",
    // A step lighter than the handoff's #8c8b87 (4.43:1), so muted text is 4.5:1 on a selected row.
    "--bg-selected": "#1c2541", "--text-primary": "#f2f1ec", "--text-secondary": "#a6a5a0", "--text-muted": "#93928d",
    "--text-onaccent": "#0b1430", "--accent": "#8fa8f0", "--accent-hover": "#b0c2f5", "--accent-subtle": "#1c2541",
    "--border": "#2a2a2e", "--border-strong": "#3a3a40", "--focus": "#b0c2f5",
    "--success": "#6fcf97", "--warning": "#edb02b", "--critical": "#f4917b",
  };

  it("sets every colour token, in light and in both dark selectors", () => {
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

  // UI audit, 16 Oct 2026 (section 8): muted text was near 3:1 on tinted rows in dark.
  it.each([
    ["light", light],
    ["dark", dark],
  ])("keeps muted text at 4.5:1 on hovered, selected and tinted rows too in %s", (_, t) => {
    for (const ground of ["--bg-hover", "--bg-selected", "--accent-subtle"]) {
      for (const text of ["--fg", "--fg-muted", "--text-muted"]) {
        expect(ratio(t, text, ground), `${text} on ${ground}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  // UI audit G8 and G9, 16 Oct 2026: the current nav link's bar and the Review count.
  it.each([
    ["light", light],
    ["dark", dark],
  ])("shows the current nav link's bar and the filled Review count clearly in %s", (_, t) => {
    for (const ground of ["--accent-subtle", "--surface-2"]) {
      expect(ratio(t, "--accent", ground), `bar on ${ground}`).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t, "--fg", "--accent-subtle")).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t, "--review-on-solid", "--review-solid")).toBeGreaterThanOrEqual(4.5);
  });

  it("uses the 4px spacing scale, three control heights and the handoff's radii and shadows", () => {
    expect(light).toMatchObject({
      "--space-1": "4px", "--space-2": "8px", "--space-3": "12px", "--space-4": "16px",
      "--space-6": "24px", "--space-8": "32px", "--space-12": "48px", "--space-16": "64px",
      "--h-sm": "32px", "--h-md": "40px", "--h-lg": "44px",
      "--r-sm": "var(--k-r-2)", "--r-md": "var(--k-r-4)", "--r-full": "var(--k-r-pill)",
      "--e-1": "var(--k-shadow-1)", "--e-2": "var(--k-shadow-2)",
    });
    const radii = Array.from(css.matchAll(/border-radius:\s*([^;]+);/g), (m) => m[1]);
    // Every corner from a token, but the round marks.
    expect(radii.filter((r) => !/^(var\(--(r-sm|r-md|r-full|k-r-\d)\)|50%|1px)$/.test(r ?? ""))).toEqual([]);
  });

  it("sets the grid, the shell's zones and the layer scale (brief C)", () => {
    expect(light).toMatchObject({
      "--grid-columns": "12", "--gutter": "24px", "--margin": "32px", "--content-max": "1200px",
      "--header-h": "var(--k-top-h)", "--toolbar-h": "48px", "--statusbar-h": "32px", "--sidebar-w": "var(--k-side-w)",
      "--z-base": "0", "--z-sticky": "10", "--z-dropdown": "20", "--z-overlay": "30",
      "--z-modal": "40", "--z-toast": "50", "--z-tooltip": "60",
      "--ease": "cubic-bezier(0.2, 0, 0.2, 1)",
    });
    expect(block("@media (max-width: 767.98px) {\n  :root {")).toMatchObject({
      "--gutter": "16px", "--margin": "16px", "--header-h": "60px", "--bottombar-h": "56px",
    });
  });

  it("takes shadows only from the shadow tokens, with no glow, gradient or backdrop blur", () => {
    const code = css.replace(/\/\*[\s\S]*?\*\//g, "");
    const shadows = Array.from(code.matchAll(/box-shadow:\s*([^;]+);/g), (m) => m[1]);
    expect(shadows.filter((s) => !/^(var\(--e-[12]\)|0 0 0 4px var\(--accent-subtle\))$/.test(s ?? ""))).toEqual([]);
    expect(code).not.toMatch(/gradient\(|text-shadow|backdrop-filter|filter:\s*drop-shadow/);
  });

  it("styles the focus ring, labels and numbers as the rules say", () => {
    expect(css).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--focus\);\s*outline-offset: 2px;\s*\}/);
    // The handoff's field label: 13px, semibold, in ink.
    expect(css).toMatch(/\.field > label,\s*\.label \{\s*font-size: var\(--fs-sm\);\s*font-weight: 600;\s*color: var\(--text-primary\);/);
    expect(light["--fs-label"]).toBe("0.6875rem");
    expect(css).toMatch(/body \{[^}]*font-variant-numeric: tabular-nums;/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*html \{\s*scroll-behavior: smooth;/);
  });

  // Changed on purpose (UI rebuild, 21 Oct 2026): pills now, as the handoff's status badges.
  it("tells Verified from Needs review by a solid or dashed edge", () => {
    expect(css).toMatch(/\.badge-verified \{[^}]*border: 1px solid var\(--verified-solid\);/);
    expect(css).toMatch(/\.badge-review \{[^}]*border: 1px dashed var\(--review-solid\);/);
    // Changed on purpose (Tushar, 16 Oct 2026: no Needs review in Ask): no edge on an answer card.
    expect(read("components/chat/Chat.module.css")).not.toContain("data-label");
  });

  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's faces replace Inter and JetBrains
  // Mono: Archivo set wide for headings, IBM Plex Sans for text, Plex Sans Devanagari for Hindi and
  // Marathi, Plex Mono for data and labels. No Monument Extended (no licence here) and no Poppins.
  it("loads the handoff's four faces and points the screens' font tokens at them", () => {
    const layout = read("app/layout.tsx");
    expect(layout).toContain('import { Archivo, IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Sans_Devanagari } from "next/font/google";');
    for (const variable of ["--font-archivo", "--font-plex-sans", "--font-plex-devanagari", "--font-plex-mono"]) {
      expect(layout).toContain(`variable: "${variable}"`);
    }
    expect(layout.match(/display: "swap"/g)).toHaveLength(4);
    expect(layout).not.toMatch(/Inter|JetBrains/);
    expect(light).toMatchObject({
      "--font-sans": "var(--k-font-body)",
      "--font-mono": "var(--k-font-mono)",
      "--font-display": "var(--k-font-display)",
    });
    for (const file of ["app/layout.tsx", "app/globals.css", "components/shell/Wordmark.module.css"]) {
      expect(read(file), file).not.toMatch(/Monument|Poppins/);
    }
  });

  it("sets text in Plex Sans and headings in Archivo, and keeps mono for data through .mono", () => {
    expect(css).toMatch(/body \{[^}]*font-family: var\(--font-sans\);/);
    expect(css).toMatch(/h1,\s*h2,\s*h3 \{\s*font-family: var\(--font-display\);/);
    expect(css).toMatch(/\.mono,\s*td\.num \{\s*font-family: var\(--font-mono\);/);
    expect(css).toMatch(/\.readText \{\s*font-family: var\(--font-mono\);/);
    expect(read("components/shell/Wordmark.module.css")).not.toMatch(/--font-mono/);
  });

  it("signs Kaagaz by MonoEdge on the wordmark and the sign-in frame", () => {
    expect(read("components/shell/Wordmark.tsx")).toContain("by MonoEdge</span>");
    const frame = read("app/login/AuthFrame.tsx");
    expect(frame).toContain("by MonoEdge</span>");
    // Changed on purpose (Tushar, 16 Oct 2026): the sign-in footer line is removed.
    expect(frame).not.toContain("Pune &amp; Jalna");
  });
});
