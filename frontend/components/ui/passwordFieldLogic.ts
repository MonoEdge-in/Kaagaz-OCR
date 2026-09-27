// The pure rules behind PasswordField, kept here so vitest can prove them in
// node: what the Show/Hide control says, where the caret goes after a toggle,
// when Caps Lock is on, and when a visible password must be hidden again.

export type PasswordInputType = "password" | "text";

/** The input's type: dots while masked, the typed characters while shown. */
export function inputType(masked: boolean): PasswordInputType {
  return masked ? "password" : "text";
}

/** The visible word on the toggle; a visually hidden " password" follows it. */
export function toggleText(masked: boolean): "Show" | "Hide" {
  return masked ? "Show" : "Hide";
}

/** What the polite live region says after the person toggles. */
export function visibilityMessage(masked: boolean): string {
  return masked ? "Password is hidden" : "Password is visible";
}

export const CAPS_LOCK_MESSAGE = "Caps Lock is on.";

/**
 * Whether Caps Lock is on, from a key event. Some events that look like key
 * events (a browser's autofill, for one) have no getModifierState; then the
 * answer is unknown (null) and the field keeps what it last knew.
 */
export function capsLockFrom(event: { getModifierState?: unknown }): boolean | null {
  const read = event.getModifierState;
  if (typeof read !== "function") return null;
  const on: unknown = read.call(event, "CapsLock");
  return typeof on === "boolean" ? on : null;
}

export interface Caret {
  start: number;
  end: number;
  direction: "forward" | "backward" | "none";
}

/**
 * The selection to put back after the input's type changes (some browsers move
 * the caret to the end when password becomes text). Out-of-range or missing
 * positions fall back to the end of the value.
 */
export function caretToKeep(
  selectionStart: number | null,
  selectionEnd: number | null,
  length: number,
  direction: string | null = "none",
): Caret {
  const clamp = (n: number | null) => (n === null || n < 0 || n > length ? length : n);
  let start = clamp(selectionStart);
  let end = clamp(selectionEnd);
  if (start > end) [start, end] = [end, start];
  const dir = direction === "forward" || direction === "backward" ? direction : "none";
  return { start, end, direction: dir };
}

/** A visible password is hidden again when the page is left or put in the background. */
export function remaskOn(eventType: string, visibilityState: string): boolean {
  if (eventType === "pagehide") return true;
  return eventType === "visibilitychange" && visibilityState === "hidden";
}

/** The ids of the parts around the input, so aria-describedby can point at them. */
export function passwordFieldIds(id: string) {
  return {
    toggle: `${id}-toggle`,
    error: `${id}-error`,
    caps: `${id}-caps`,
    hint: `${id}-hint`,
    status: `${id}-status`,
  };
}

/** aria-describedby: the error first, then the Caps Lock warning, then the hint. */
export function describedBy(
  ids: ReturnType<typeof passwordFieldIds>,
  parts: { error: boolean; capsLock: boolean; hint: boolean },
): string | undefined {
  const list: string[] = [];
  if (parts.error) list.push(ids.error);
  if (parts.capsLock) list.push(ids.caps);
  if (parts.hint) list.push(ids.hint);
  return list.length > 0 ? list.join(" ") : undefined;
}
