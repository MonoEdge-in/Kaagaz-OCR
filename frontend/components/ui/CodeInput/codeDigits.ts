// The digits of a sign-in code, kept as one string with no gaps. Pure, so it is
// unit tested (tests/kit-inputs.test.ts).

/** The digits in `text`, at most `length` of them: a pasted "482 913" gives "482913". */
export function digitsOf(text: string, length: number): string {
  return text.replace(/\D/g, "").slice(0, length);
}

/**
 * Digits typed or pasted into box `index`: written from there on, over what
 * was there. A box past the digits so far writes after them, so there is never
 * a gap. Returns the new value and the box to focus next.
 */
export function typeAt(value: string, index: number, typed: string, length: number): { value: string; focus: number } {
  const at = Math.min(index, value.length);
  const digits = digitsOf(typed, length - at);
  if (!digits) return { value, focus: at };
  const next = (value.slice(0, at) + digits + value.slice(at + digits.length)).slice(0, length);
  return { value: next, focus: Math.min(at + digits.length, length - 1) };
}

/** What was typed into a box that held `old`: the box's new text without the old digit. */
export function typedInto(old: string, text: string): string {
  if (old && text.startsWith(old)) return text.slice(old.length);
  if (old && text.endsWith(old)) return text.slice(0, -old.length);
  return text;
}

/** Backspace in box `index`: clears its digit, or the one before it when it is empty. */
export function eraseAt(value: string, index: number): { value: string; focus: number } {
  const at = index < value.length ? index : Math.max(0, Math.min(index, value.length) - 1);
  return { value: value.slice(0, at) + value.slice(at + 1), focus: at };
}
