// A failed request in plain words, with the fix (#468), by the reply's status. The server's
// own sentence is kept where it is the backend's specific message for its code (the routes'
// ApiError texts and backend/kaagaz/api/errors.py MESSAGES are written for customers: "That
// e-mail is already in use.", "Turn on two-step sign-in in Account to do this."). Its generic
// refusals, every 404 and 5xx, and a reply with no code of the API's (lib/api.ts errorFrom
// gives such a reply, a proxy's error page, the code "error") get the sentences here.

import type { ApiError } from "./api";
import { NETWORK_FAILURE } from "./emailLinks";

export const FORBIDDEN = "You don't have permission to do this. Ask your admin.";
export const OPERATOR_FORBIDDEN = "Your operator account can't do this.";
export const NOT_FOUND = "This was not found. It may have been deleted.";
export const TOO_MANY_REQUESTS = "Too many requests. Wait a minute and try again.";
export const SERVER_PROBLEM = "Kaagaz had a problem. Try again in a minute.";

/** What to say for a failed request. ``who``: an operator is told a different fix for a 403. */
export function errorWords(error: ApiError, who: "customer" | "operator" = "customer"): string {
  const fromApi = error.code !== "error"; // the reply carried the backend's own code and words
  if (error.status === 0) return error.message || NETWORK_FAILURE;
  if (error.status >= 500) return SERVER_PROBLEM;
  switch (error.status) {
    case 403:
      if (fromApi && error.code !== "forbidden") return error.message;
      return who === "operator" ? OPERATOR_FORBIDDEN : FORBIDDEN;
    case 404:
      return NOT_FOUND;
    case 409:
      return fromApi ? error.message : SERVER_PROBLEM;
    case 429:
      return fromApi ? error.message : TOO_MANY_REQUESTS;
    default:
      // 400, 401 (the session's end is handled in lib/session.tsx), 413...: the backend's own
      // words; a reply without its code (a proxy's page) has none worth showing.
      return fromApi ? error.message : SERVER_PROBLEM;
  }
}
