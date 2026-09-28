// Which links the shell shows for a role. This one list feeds both the desktop
// sidebar and the phone drawer (ShellMenu), so the two can never disagree. The
// server still refuses a page's data to someone without the role; hiding a
// link is only a convenience.

import type { IconName } from "@/components/ui/icons";
import type { Role } from "@/lib/types";

export interface NavLink {
  href: string;
  label: string;
  icon: IconName;
}

const LINKS: { link: NavLink; roles: readonly Role[] }[] = [
  {
    link: { href: "/documents", label: "Documents", icon: "documents" },
    roles: ["member", "reviewer", "admin"],
  },
  { link: { href: "/chat", label: "Ask", icon: "chat" }, roles: ["member", "reviewer", "admin"] },
  { link: { href: "/review", label: "Review", icon: "review" }, roles: ["reviewer", "admin"] },
  { link: { href: "/admin", label: "Admin", icon: "admin" }, roles: ["admin"] },
];

export function navLinks(role: Role): NavLink[] {
  return LINKS.filter((entry) => entry.roles.includes(role)).map((entry) => entry.link);
}

/** A link is current on its own path and on every path below it (/documents/abc). */
export function isCurrent(href: string, pathname: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
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
