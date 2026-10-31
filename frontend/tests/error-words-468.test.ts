// #468 B1: a failed request in plain words, with the fix (lib/errorWords.ts), and the screens
// that now use it: the admin panels, the operator's 403, the document that can't open, and the
// Activity tab's "Find a file", whose failure is not "no file".

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { retryErrorText, settingsErrorText } from "@/components/admin/logic";
import { operatorErrorMessage } from "@/components/operator/logic";
import { loadFailure, reprocessErrorText } from "@/components/viewer/reprocess";
import { ApiError } from "@/lib/api";
import { NETWORK_FAILURE } from "@/lib/emailLinks";
import {
  FORBIDDEN,
  NOT_FOUND,
  OPERATOR_FORBIDDEN,
  SERVER_PROBLEM,
  TOO_MANY_REQUESTS,
  errorWords,
} from "@/lib/errorWords";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
// lib/api.ts errorFrom: a reply with no JSON error (a proxy's page) has the code "error".
const noCode = (status: number) => new ApiError(status, "error", "Something went wrong.");

describe("errorWords: status to a plain sentence with the fix", () => {
  it("says the sentences the issue asks for", () => {
    expect(FORBIDDEN).toBe("You don't have permission to do this. Ask your admin.");
    expect(OPERATOR_FORBIDDEN).toBe("Your operator account can't do this.");
    expect(NOT_FOUND).toBe("This was not found. It may have been deleted.");
    expect(TOO_MANY_REQUESTS).toBe("Too many requests. Wait a minute and try again.");
    expect(SERVER_PROBLEM).toBe("Kaagaz had a problem. Try again in a minute.");
  });

  it("0: keeps the network words the request gave, else the shared network sentence", () => {
    expect(errorWords(new ApiError(0, "network", "Could not reach the server."))).toBe("Could not reach the server.");
    expect(errorWords(new ApiError(0, "network", ""))).toBe(NETWORK_FAILURE);
  });

  it("401: the session ending is handled elsewhere, so the words pass through", () => {
    expect(errorWords(new ApiError(401, "not_signed_in", "Please sign in."))).toBe("Please sign in.");
  });

  it("403: the generic refusal in plain words; a customer and an operator are told different fixes", () => {
    const generic = new ApiError(403, "forbidden", "You don't have permission to do this.");
    expect(errorWords(generic)).toBe(FORBIDDEN);
    expect(errorWords(generic, "operator")).toBe(OPERATOR_FORBIDDEN);
    expect(errorWords(noCode(403))).toBe(FORBIDDEN);
  });

  it("403: keeps the server's specific sentence for its own code (errors.py, the routes)", () => {
    const twoStep = new ApiError(403, "two_step_required", "Turn on two-step sign-in in Account to do this.");
    expect(errorWords(twoStep)).toBe("Turn on two-step sign-in in Account to do this.");
    const adminOnly = new ApiError(403, "admin_session_required", "Only admins can use this. Ask your admin if you need it.");
    expect(errorWords(adminOnly)).toBe(adminOnly.message);
    expect(errorWords(new ApiError(403, "feature_off", "This is turned off for now."))).toBe("This is turned off for now.");
  });

  it("404: always the plain not-found sentence", () => {
    expect(errorWords(new ApiError(404, "not_found", "We couldn't find that."))).toBe(NOT_FOUND);
    expect(errorWords(noCode(404))).toBe(NOT_FOUND);
  });

  it("409 and 429: the server's own words for a known code; otherwise a plain sentence", () => {
    expect(errorWords(new ApiError(409, "conflict", "That e-mail is already in use."))).toBe("That e-mail is already in use.");
    expect(errorWords(new ApiError(409, "in_trash", "This document is in the Trash. Restore it first."))).toBe(
      "This document is in the Trash. Restore it first.",
    );
    expect(errorWords(new ApiError(429, "rate_limited", "Too many questions. Wait a minute."))).toBe(
      "Too many questions. Wait a minute.",
    );
    expect(errorWords(noCode(429))).toBe(TOO_MANY_REQUESTS);
    // A 409 with no code is not "too many requests": it gets the general sentence.
    expect(errorWords(noCode(409))).toBe(SERVER_PROBLEM);
  });

  it("5xx: always the plain sentence, never 'Something went wrong.' or the server's busy words", () => {
    expect(errorWords(noCode(502))).toBe(SERVER_PROBLEM);
    expect(errorWords(new ApiError(500, "internal_error", "Something went wrong on our side."))).toBe(SERVER_PROBLEM);
    expect(errorWords(new ApiError(503, "backend_unavailable", "The service is busy right now."))).toBe(SERVER_PROBLEM);
  });

  it("any other status keeps the server's words (a 400's 'The request is not valid.', a 413)", () => {
    expect(errorWords(new ApiError(400, "bad_request", "The request is not valid."))).toBe("The request is not valid.");
    expect(errorWords(new ApiError(413, "too_large", "This file is too large."))).toBe("This file is too large.");
  });
});

describe("where the plain words are used", () => {
  it("admin: Settings, Retry and the load errors say errorWords, never the raw reply", () => {
    expect(settingsErrorText(noCode(502))).toBe(SERVER_PROBLEM);
    expect(settingsErrorText(new ApiError(403, "forbidden", "x"))).toBe(FORBIDDEN);
    expect(retryErrorText(noCode(500))).toBe(SERVER_PROBLEM);
    // The 409 and 429 refusals Retry explains keep their own words.
    expect(retryErrorText(new ApiError(409, "conflict", "Someone already retried it."))).toBe("Someone already retried it.");
    for (const file of [
      "components/admin/ActivityPanel.tsx",
      "components/admin/AiSettingsPanel.tsx",
      "components/admin/DepartmentsPanel.tsx",
      "components/admin/MembersTable.tsx",
      "components/admin/NumbersPanel.tsx",
      "components/admin/SettingsPanel.tsx",
      "components/admin/UsagePanel.tsx",
      "components/admin/UsersPanel.tsx",
      "components/admin/logic.ts",
    ]) {
      const source = read(file);
      expect(source, file).not.toMatch(/instanceof ApiError\s*\?\s*caught\.message/);
      expect(source, file).toContain("errorWords(caught)");
    }
  });

  it("operator: a 403 is the operator's own refusal, or the server's specific words; the rest as before", () => {
    expect(operatorErrorMessage(new ApiError(403, "forbidden", "x"))).toBe(OPERATOR_FORBIDDEN);
    expect(operatorErrorMessage(new ApiError(403, "move_refused", "That move was not accepted."))).toBe(
      "That move was not accepted.",
    );
    // A known refusal still wins (REFUSALS).
    expect(operatorErrorMessage(new ApiError(403, "step_up_failed", "x"))).toMatch(/That code was not accepted/);
  });

  it("a document that can't open: the plain not-found sentence, no Try again; 5xx in plain words", () => {
    // Changed on purpose (#468): the raw "We couldn't find that." becomes the plain sentence.
    expect(loadFailure(new ApiError(404, "not_found", "We couldn't find that."))).toEqual({ message: NOT_FOUND, retry: false });
    expect(loadFailure(noCode(502))).toEqual({ message: SERVER_PROBLEM, retry: true });
    expect(loadFailure(new ApiError(403, "forbidden", "x"))).toEqual({ message: FORBIDDEN, retry: true });
    expect(reprocessErrorText(noCode(500))).toBe(SERVER_PROBLEM);
  });

  it("Activity's Find a file: a failure is said with Try again, never as 'no file has that name'", () => {
    const panel = read("components/admin/ActivityPanel.tsx");
    expect(panel).not.toMatch(/\(\) => setFound\(\[\]\)/);
    expect(panel).toMatch(/Could not search the file names\./);
    expect(panel).toMatch(/findFailed[\s\S]{0,400}Try again/);
  });
});
