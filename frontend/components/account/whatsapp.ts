// Account, WhatsApp (#603), kept pure so vitest can prove the words and the rules: the number as
// typed (+91 filled in), masked to its last 4 digits, the Connect screen's privacy text, what the
// page says for each state.

import { formatDate } from "@/lib/format";
import type { WhatsAppMe } from "@/lib/types";

export const TITLE = "WhatsApp";
export const PREFIX = "+91 ";
/** PRD 7 section 15, Privacy: the Connect screen says it, word for word. */
export const PRIVACY_TEXT =
  "Your questions, voice notes and answers pass through WhatsApp (Meta) and stay in your chat, on your phone, " +
  "its backups and WhatsApp Web. Files are never sent on WhatsApp; you open them in Kaagaz with your password. " +
  "Kaagaz cannot delete them later. Anyone who can open WhatsApp on this phone can ask about your files. " +
  "Keep your phone locked.";
export const CONSENT_TEXT = "I agree that my questions, voice notes and answers will pass through WhatsApp (Meta).";
export const NOT_AGREED = "Tick the box to agree before you continue.";
export const BAD_NUMBER = "Enter your WhatsApp number with its country code, like +91 98765 43210.";

/** The number as the server takes it: "+" and the digits, spaces and dashes left out. */
export function normalizeNumber(typed: string): string {
  const digits = typed.replace(/\D/g, "");
  return digits ? `+${digits}` : "";
}

/** Why the number can't be sent yet, or null: 8 to 15 digits, the first not 0 (E.164). */
export function numberProblem(typed: string): string | null {
  return /^\+[1-9]\d{7,14}$/.test(normalizeNumber(typed)) ? null : BAD_NUMBER;
}

/** "+91 ••••1234" for an Indian number, "••••1234" for any other. Never more than the last 4 digits. */
export function maskedNumber(typed: string): string {
  const number = normalizeNumber(typed);
  const masked = `••••${number.slice(-4)}`;
  return number.startsWith("+91") ? `+91 ${masked}` : masked;
}

/** What the page says above its button for each state. */
export function stateText(me: WhatsAppMe): string {
  if (me.state === "off") return "WhatsApp isn't turned on for your organisation. Ask your admin.";
  if (me.state === "not_allowed") return "Your organisation doesn't use WhatsApp for your role.";
  if (me.state === "none") return "Ask about your files and get links to them from WhatsApp. Files never leave Kaagaz.";
  const used = me.last_used_at ? ` Last used ${formatDate(me.last_used_at)}.` : "";
  return `Connected: ••••${me.last4 ?? ""}, since ${formatDate(me.linked_at ?? "")}.${used}`;
}
