// Admin, Settings, WhatsApp (#603), kept pure so vitest can prove it: the one dialog's words (PRD 7
// section 15, "Who controls it"), the roles in their order with the extra ticks, whether the form
// changed, and the Users tab's WhatsApp cell.

import { formatDate } from "@/lib/format";
import type { Role, WhatsAppAdmin, WhatsAppPerson } from "@/lib/types";

export type WhatsAppForm = Pick<WhatsAppAdmin, "enabled" | "roles" | "management" | "voice" | "uploads">;

export const turnOnTitle = (organisation: string) => `Turn on WhatsApp for ${organisation}?`;
export const TURN_ON_TEXT =
  "People can ask about their files and get links to them from WhatsApp. Files never leave Kaagaz: each link needs a Kaagaz sign-in.";
export const SENT_STAYS = "Answers already sent stay on the person's phone, even after their access is removed.";
export const VOICE_LABEL = "Allow voice notes";
/** Every organisation's Admin confirms it before turning WhatsApp on (lead's decision, 15 Nov 2026). */
export const RULES_LABEL = "I confirm our organisation's data rules allow WhatsApp.";
export const RULES_MISSING = "Tick the box to confirm your organisation's data rules allow WhatsApp.";
export const UPLOADS_LABEL = "Allow saving files from WhatsApp";
export const TURN_OFF_TEXT = "Everyone's WhatsApp is disconnected at once. You can turn it on again later.";
export const NOT_AVAILABLE = "WhatsApp isn't available for your organisation yet.";

/** The roles that may connect, in order; Admins and Management need the extra tick (decision 4). */
export const ROLE_TICKS: readonly { role: Role; label: string; hint?: string }[] = [
  { role: "member", label: "Members" },
  { role: "reviewer", label: "Reviewers" },
  { role: "admin", label: "Admins", hint: "An extra tick: a phone has no two-step code." },
];
export const MANAGEMENT_LABEL = "Management";
export const MANAGEMENT_HINT = "An extra tick: Management sees every file.";

export function formOf(saved: WhatsAppAdmin): WhatsAppForm {
  const { enabled, roles, management, voice, uploads } = saved;
  return { enabled, roles: [...roles], management, voice, uploads };
}

/** ``role`` ticked or not, the roles kept in ROLE_TICKS order. */
export function withRole(form: WhatsAppForm, role: Role, on: boolean): WhatsAppForm {
  const roles = ROLE_TICKS.map((tick) => tick.role).filter((r) => (r === role ? on : form.roles.includes(r)));
  return { ...form, roles };
}

/** Ticking Admins or Management (not there before) asks for the Admin's password, as turning it on. */
export function tickNeedsPassword(saved: WhatsAppAdmin, form: WhatsAppForm): boolean {
  return (form.roles.includes("admin") && !saved.roles.includes("admin")) || (form.management && !saved.management);
}

export function formChanged(saved: WhatsAppAdmin, form: WhatsAppForm): boolean {
  const before = formOf(saved);
  return (Object.keys(before) as (keyof WhatsAppForm)[]).some((key) =>
    key === "roles" ? before.roles.join() !== form.roles.join() : before[key] !== form[key],
  );
}

/** The Users tab's WhatsApp column: the connections while they load, could not load, or loaded. */
export type WhatsAppLoad = WhatsAppPerson[] | "loading" | "failed";
export type WhatsAppCellState =
  | { kind: "loading" }
  | { kind: "failed" } // "Couldn't load", with Try again: never "Not connected"
  | { kind: "none" }
  | { kind: "connected"; text: string };

/** One person's WhatsApp cell: "••••1234 since 14 Nov 2026" once loaded and connected. */
export function whatsappCellState(load: WhatsAppLoad, userId: string): WhatsAppCellState {
  if (load === "loading" || load === "failed") return { kind: load };
  const person = load.find((row) => row.user_id === userId);
  return person ? { kind: "connected", text: `••••${person.last4} since ${formatDate(person.linked_at)}` } : { kind: "none" };
}
