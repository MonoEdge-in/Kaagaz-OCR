// The theme choice: Light, Dark, or follow the system.
//
// It lives in a plain, non-HttpOnly cookie holding only "light", "dark" or
// "system", never anything about the person. The root layout reads it on the
// server and writes data-theme on <html>, so the first paint is already right:
// no inline script, no localStorage, no flash (council 2026-09-27). "System"
// writes no attribute; app/globals.css then follows prefers-color-scheme.

export type ThemeChoice = "light" | "dark" | "system";

export const THEME_COOKIE = "kaagaz_theme";
export const THEME_CHOICES: readonly ThemeChoice[] = ["light", "dark", "system"];
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

/** Anything that is not a known choice (a stale or tampered cookie) means "system". */
export function parseTheme(value: string | null | undefined): ThemeChoice {
  return value === "light" || value === "dark" ? value : "system";
}

/** The data-theme attribute for a choice; none for "system". */
export function themeAttribute(choice: ThemeChoice): "light" | "dark" | undefined {
  return choice === "system" ? undefined : choice;
}

/** The Set-Cookie text the browser stores, for the whole app, for a year. */
export function themeCookie(choice: ThemeChoice, secure: boolean): string {
  const parts = [
    `${THEME_COOKIE}=${choice}`,
    "Path=/",
    `Max-Age=${ONE_YEAR_SECONDS}`,
    "SameSite=Lax",
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

/** Apply a choice in the open page at once, and remember it for the next visit. */
export function applyTheme(choice: ThemeChoice): void {
  const attribute = themeAttribute(choice);
  const root = document.documentElement;
  if (attribute) root.setAttribute("data-theme", attribute);
  else root.removeAttribute("data-theme");
  document.cookie = themeCookie(choice, window.location.protocol === "https:");
}

export const THEME_LABELS: Record<ThemeChoice, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};
