// Which links the shell shows for a role and session. This one list feeds both
// the desktop sidebar and the phone drawer (ShellMenu), so the two can never
// disagree. The server still refuses a page's data to someone without the role
// or the admin session; hiding a link is only a convenience.

import type { IconName } from "@/components/ui/icons";
import type { Role, SessionScope } from "@/lib/types";

export interface NavLink {
  href: string;
  label: string;
  icon: IconName;
  /** "review": the link shows the values waiting in Review (D-7). */
  count?: "review";
  /** The phone tab bar's shorter words, when the label is too long for it. */
  short?: string;
}

const LINKS: { link: NavLink; roles: readonly Role[]; scope?: SessionScope; management?: true }[] = [
  // File management D1: the dashboard is the home screen, first after sign-in.
  { link: { href: "/dashboard", label: "Home", icon: "home" }, roles: ["member", "reviewer", "admin"] },
  {
    link: { href: "/documents", label: "Documents", icon: "documents" },
    roles: ["member", "reviewer", "admin"],
  },
  { link: { href: "/chat", label: "Ask", icon: "chat" }, roles: ["member", "reviewer", "admin"] },
  // My words: each user's own, so every role has it.
  { link: { href: "/words", label: "My words", icon: "words" }, roles: ["member", "reviewer", "admin"] },
  // #707: each person's own access; an Admin in an admin session has the Admin tab's Access.
  { link: { href: "/my-access", label: "My access", icon: "eye" }, roles: ["member", "reviewer", "admin"], scope: "app" },
  { link: { href: "/review", label: "Review", icon: "review", count: "review" }, roles: ["reviewer", "admin"] },
  // PRD 7 (#601): Management's, a flag not a role. An Admin reads the same in the Admin tab.
  // #706: "Activity" on the phone tab bar, so it never reads as the admins' Access below.
  {
    link: { href: "/access-activity", label: "Access activity", icon: "history", short: "Activity" },
    roles: ["member", "reviewer", "admin"],
    management: true,
  },
  // #706: every person's access (/access), for exactly who sees Admin.
  { link: { href: "/access", label: "Access", icon: "key" }, roles: ["admin"], scope: "admin" },
  // PRD A1: an admin signed in at /login gets no Admin menu.
  { link: { href: "/admin", label: "Admin", icon: "admin" }, roles: ["admin"], scope: "admin" },
];

export function navLinks(role: Role, scope: SessionScope, management = false): NavLink[] {
  // An Admin has it all in the Admin tab's Activity, Management or not.
  const access = management && role !== "admin";
  return LINKS.filter(
    (entry) =>
      entry.roles.includes(role) &&
      (entry.scope === undefined || entry.scope === scope) &&
      (entry.management === undefined || access),
  ).map((entry) => entry.link);
}

/** A link is current on its own path and on every path below it (/documents/abc). */
export function isCurrent(href: string, pathname: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The values waiting in Review, as the link shows them: "23", and "99+" past 99; none at 0 (D-7). */
export function reviewCountText(total: number | null): string | null {
  if (total === null || total <= 0) return null;
  return total > 99 ? "99+" : String(total);
}

/** The name of the page the person is on, for the phone top bar; null if none matches. */
export function currentPageName(links: readonly NavLink[], pathname: string): string | null {
  return links.find((link) => isCurrent(link.href, pathname))?.label ?? null;
}

const ROLE_NAMES: Record<Role, string> = {
  member: "Member",
  reviewer: "Reviewer",
  admin: "Admin",
};

export function roleName(role: Role): string {
  return ROLE_NAMES[role];
}

type ShortcutKeys = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">;

/** Ctrl+K, or Cmd+K on a Mac: focus the header's search. */
export function isSearchShortcut(event: ShortcutKeys): boolean {
  return (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k";
}

/** The avatar's letters: the first letter of the name's first two words ("Asha Patil" is "AP"). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean).slice(0, 2);
  return words.map((word) => Array.from(word)[0] ?? "").join("").toUpperCase() || "?";
}
