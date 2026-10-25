// The colour palette choice: Kaagaz blue or one of five others, for this person only.
//
// It works exactly like the theme (lib/theme.ts): a plain, non-HttpOnly cookie
// holding only a palette id, never anything about the person. The root layout
// reads it on the server and writes data-palette on <html>, so the first paint
// is already right: no inline script, no localStorage, no flash. Kaagaz blue
// writes no attribute. The colours are token overrides in app/styles/tokens.css
// (section 1d), in light and dark; status colours never change.

export type PaletteChoice = "kaagaz" | "ocean" | "teal" | "violet" | "plum" | "graphite";

export const PALETTE_COOKIE = "kaagaz_palette";
export const PALETTE_CHOICES: readonly PaletteChoice[] = ["kaagaz", "ocean", "teal", "violet", "plum", "graphite"];
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

/** Anything that is not a known palette (a stale or tampered cookie) means Kaagaz blue. */
export function parsePalette(value: string | null | undefined): PaletteChoice {
  return PALETTE_CHOICES.find((choice) => choice === value) ?? "kaagaz";
}

/** The data-palette attribute for a choice; none for Kaagaz blue. */
export function paletteAttribute(choice: PaletteChoice): Exclude<PaletteChoice, "kaagaz"> | undefined {
  return choice === "kaagaz" ? undefined : choice;
}

/** The Set-Cookie text the browser stores, for the whole app, for a year. */
export function paletteCookie(choice: PaletteChoice, secure: boolean): string {
  const parts = [`${PALETTE_COOKIE}=${choice}`, "Path=/", `Max-Age=${ONE_YEAR_SECONDS}`, "SameSite=Lax"];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

/** Apply a choice in the open page at once, and remember it for the next visit. */
export function applyPalette(choice: PaletteChoice): void {
  const attribute = paletteAttribute(choice);
  const root = document.documentElement;
  if (attribute) root.setAttribute("data-palette", attribute);
  else root.removeAttribute("data-palette");
  document.cookie = paletteCookie(choice, window.location.protocol === "https:");
}

export const PALETTE_LABELS: Record<PaletteChoice, string> = {
  kaagaz: "Kaagaz blue",
  ocean: "Ocean",
  teal: "Teal",
  violet: "Violet",
  plum: "Plum",
  graphite: "Graphite",
};
