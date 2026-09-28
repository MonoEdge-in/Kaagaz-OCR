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
