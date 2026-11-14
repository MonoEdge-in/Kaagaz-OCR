// The self-grant password step (PRD 7 decision 11; plan section 1). An admin who gives themselves
// access types their own password. Asked first when the client knows it is a self-grant (the
// person is me, or a preview's `gaining` holds me); otherwise the change is sent without one, and
// a 403 password_required asks then and sends it again. Tested in tests/access-password-step.test.ts.

import { ApiError } from "@/lib/api";
import type { Person } from "@/lib/types";
import { WRONG_PASSWORD } from "./words";

/** Opens the step: `attempt` sends with the typed password and throws what the server said; the
 * step resolves with its result, or null when cancelled (then nothing is sent). */
export type AskPassword = <T>(attempt: (password: string) => Promise<T>) => Promise<T | null>;

export const passwordRequired = (caught: unknown) =>
  caught instanceof ApiError && caught.status === 403 && caught.code === "password_required";

/** What the open step says under its box after a refused password, keeping it open; null for any
 * other failure, which closes the step and goes to the caller. */
export function passwordRetryText(caught: unknown): string | null {
  if (!(caught instanceof ApiError) || caught.status !== 400) return null;
  if (caught.code === "wrong_password") return WRONG_PASSWORD;
  if (caught.code === "password_locked") return caught.message; // the server's: how long it lasts
  return null;
}

/** A preview's `gaining` (or the people chosen) holds me: ask for the password first. */
export const includesMe = (people: readonly Person[], myId: string) => people.some((person) => person.id === myId);

export async function withPassword<T>(
  send: (password?: string) => Promise<T>,
  ask: AskPassword,
  first: boolean,
): Promise<T | null> {
  if (!first) {
    try {
      return await send();
    } catch (caught) {
      if (!passwordRequired(caught)) throw caught;
    }
  }
  return ask((password) => send(password));
}
