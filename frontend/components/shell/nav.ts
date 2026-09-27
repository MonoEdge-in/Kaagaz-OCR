// Which links the top bar shows for a role. The server still refuses a page's
// data to someone without the role; hiding a link is only a convenience.

import type { Role } from "@/lib/types";

export interface NavLink {
  href: string;
  label: string;
}

const LINKS: { link: NavLink; roles: readonly Role[] }[] = [
  { link: { href: "/documents", label: "Documents" }, roles: ["member", "reviewer", "admin"] },
  { link: { href: "/chat", label: "Ask" }, roles: ["member", "reviewer", "admin"] },
  { link: { href: "/review", label: "Review" }, roles: ["reviewer", "admin"] },
  { link: { href: "/admin", label: "Admin" }, roles: ["admin"] },
];

export function navLinks(role: Role): NavLink[] {
  return LINKS.filter((entry) => entry.roles.includes(role)).map((entry) => entry.link);
}

/** A link is current on its own path and on every path below it (/documents/abc). */
export function isCurrent(href: string, pathname: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

const ROLE_NAMES: Record<Role, string> = {
  member: "Member",
  reviewer: "Reviewer",
  admin: "Admin",
};

export function roleName(role: Role): string {
  return ROLE_NAMES[role];
}
