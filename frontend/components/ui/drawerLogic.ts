// The close rules behind Drawer, as pure functions so vitest can prove them.
// Esc, focus trapping and the inert background come from the native <dialog>
// (showModal); these cover the rest.

/** At this width the sidebar shows, so an open drawer is no longer needed. */
export const DESKTOP_QUERY = "(min-width: 1024px)";

/**
 * A click on the backdrop. The dialog has no padding and its panel fills it,
 * so the dialog itself is the target only outside the panel. The press must
 * start there too: a text selection dragged out of the panel is not a close.
 */
export function isBackdropClick(pressTarget: unknown, clickTarget: unknown, dialog: unknown): boolean {
  return dialog != null && pressTarget === dialog && clickTarget === dialog;
}

/** An open drawer closes when the route changes (a nav link inside it was followed). */
export function closesOnRoute(previousPath: string, nextPath: string, open: boolean): boolean {
  return open && previousPath !== nextPath;
}

/** An open drawer closes once the viewport is wide enough for the sidebar. */
export function closesOnViewport(desktopMatches: boolean, open: boolean): boolean {
  return open && desktopMatches;
}

/**
 * Whether to send focus back to the opener after closing: only when focus is
 * lost (on the body or nothing) or still inside the closed drawer, never when
 * the person has already moved it somewhere else.
 */
export function shouldReturnFocus<T>(
  active: T | null,
  body: T | null,
  dialog: { contains(node: T | null): boolean },
): boolean {
  return active === null || active === body || dialog.contains(active);
}

/**
 * Whether a close the person asked for (Esc, the backdrop, the close button)
 * may go ahead: always, unless there are unsaved changes, and then only once
 * they say yes to `ask` (a confirm naming what would be lost).
 */
export function closeAllowed(unsaved: boolean, ask: () => boolean): boolean {
  return !unsaved || ask();
}
