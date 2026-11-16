// PRD 7 PR 11c (#604, lead decision 7): "No files here yet. Ask your admin for access." on All
// documents when it is empty for someone the walls keep files from: not an Admin, not Management,
// in no live department, and only once the organisation has kept something to some people (PR 10c's
// note: on day one nobody is in a department, so every member of a new organisation would see it).
// An older server, without these flags, keeps the usual words.

import type { Me } from "@/lib/types";

export const ASK_FOR_ACCESS = { title: "No files here yet", text: "Ask your admin for access." };

export function asksForAccess(me: Me): boolean {
  const access = me.access;
  return (
    me.user.role !== "admin" && access?.walled === true && access.in_department === false && !access.management
  );
}
