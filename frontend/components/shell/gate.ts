// What the signed-in person's session allows the app to show, read from Me in
// one place. Both reads deny by default: only scope "admin" is an admin
// session (anything else gets no Admin link), and only true asks for a new
// password. The server checks the same things on every request; this only
// picks the screen.

import type { Me, SessionScope } from "@/lib/types";

export function sessionScope(me: Me): SessionScope {
  return me.session.scope === "admin" ? "admin" : "app";
}

export function mustChangePassword(me: Me): boolean {
  return me.user.must_change_password === true;
}

/** "set_password": show only the set-new-password screen (PRD A5), in either scope. */
export type AppGate = "set_password" | "app";

export function appGate(me: Me): AppGate {
  return mustChangePassword(me) ? "set_password" : "app";
}
