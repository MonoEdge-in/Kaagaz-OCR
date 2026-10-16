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

// The MonoEdge look (16 Oct 2026): monoedge.in's background, ink, blue and
// faces, with every text and edge pair still WCAG 2.2 AA in both themes.
describe("MonoEdge look", () => {
  const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
  const css = read("app/globals.css");
  const block = (opening: string) => {
    const start = css.indexOf(opening);
    expect(start, opening).toBeGreaterThanOrEqual(0);
    const body = css.slice(start + opening.length, css.indexOf("}", start));
    return Object.fromEntries(Array.from(body.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6});/g), (m) => [m[1], m[2]]));
  };
  const light = block(":root {");
  const dark = { ...light, ...block(':root[data-theme="dark"] {') };
  const channel = (pair: string) => {
    const value = parseInt(pair, 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (hex: string) =>
    0.2126 * channel(hex.slice(1, 3)) + 0.7152 * channel(hex.slice(3, 5)) + 0.0722 * channel(hex.slice(5, 7));
  const ratio = (a: string, b: string) => {
    const [x, y] = [luminance(a), luminance(b)];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };

  it("uses the MonoEdge background, ink and blue, in light and dark", () => {
    expect(light).toMatchObject({ "--bg": "#f6f5f2", "--fg": "#0a0a0f", "--accent": "#204494", "--focus": "#204494" });
    expect(dark).toMatchObject({ "--bg": "#0a0a0f", "--accent": "#7f9bdd", "--focus": "#7f9bdd" });
    // The system's dark and the picked Dark are the same colours.
    expect(block(':root:not([data-theme="light"]) {')).toEqual(block(':root[data-theme="dark"] {'));
  });

  it.each([
    ["light", light],
    ["dark", dark],
  ])("keeps text at 4.5:1 and edges at 3:1 in %s", (_, t) => {
    for (const ground of [t["--bg"], t["--surface"], t["--surface-2"]]) {
      for (const text of [t["--fg"], t["--fg-muted"], t["--accent"]]) expect(ratio(text, ground)).toBeGreaterThanOrEqual(4.5);
      for (const edge of [t["--border-strong"], t["--focus"]]) expect(ratio(edge, ground)).toBeGreaterThanOrEqual(3);
    }
    expect(ratio(t["--accent-fg"], t["--accent"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(t["--accent"], t["--accent-soft"])).toBeGreaterThanOrEqual(4.5);
    for (const label of ["verified", "review", "danger"]) {
      expect(ratio(t[`--${label}-fg`], t[`--${label}-bg`]), label).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("loads Inter, Archivo and JetBrains Mono, with Devanagari faces after them", () => {
    const layout = read("app/layout.tsx");
    expect(layout).toContain('import { Archivo, Inter, JetBrains_Mono } from "next/font/google";');
    for (const variable of ["--font-inter", "--font-archivo", "--font-jetbrains-mono"]) {
      expect(layout).toContain(`variable: "${variable}"`);
    }
    expect(layout.match(/subsets: \["latin", "latin-ext"\]/g)).toHaveLength(3);
    expect(layout.match(/display: "swap"/g)).toHaveLength(3);
    for (const [token, face] of [
      ["--font-sans", "--font-inter"],
      ["--font-display", "--font-archivo"],
      ["--font-mono", "--font-jetbrains-mono"],
    ]) {
      const value = new RegExp(`${token}: ([^;]+);`).exec(css)?.[1] ?? "";
      expect(value.startsWith(`var(${face},`), token).toBe(true);
      expect(value, token).toMatch(/"Nirmala UI",\s*"Noto Sans Devanagari"/);
    }
  });

  it("signs Kaagaz by MonoEdge on the wordmark and the sign-in frame", () => {
    expect(read("components/shell/Wordmark.tsx")).toContain("by MonoEdge</span>");
    const frame = read("app/login/AuthFrame.tsx");
    expect(frame).toContain("by MonoEdge</span>");
    expect(frame).toContain("&copy; 2026 MonoEdge · Pune &amp; Jalna");
  });
});
