// Moving the operator's two-step to a new device (16 Oct 2026): the checks before
// sending, when the dialog asks before closing, what each refusal says, calm words,
// and the first step rendered in node: labelled fields, [Cancel] then the primary.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MoveTwoStepDialog } from "@/components/operator/MoveTwoStepDialog";
import {
  CONFIRM_REFUSED,
  MOVE_TEXT,
  PASSWORD_MISSING,
  START_REFUSED,
  closeAsked,
  hasUnsaved,
  leaveQuestion,
  moveErrorMessage,
  passwordError,
} from "@/components/operator/twoStepMove";
import { ApiError } from "@/lib/api";

const refused = new ApiError(403, "move_refused", "server words");

describe("moving two-step to a new device", () => {
  it("needs the password exactly as typed", () => {
    expect(passwordError("")).toBe(PASSWORD_MISSING);
    expect(passwordError(" ")).toBeNull();
  });

  it("asks before closing when something typed or a shown key would be lost", () => {
    const empty = { password: "", code: "" };
    expect(hasUnsaved("check", empty)).toBe(false);
    expect(hasUnsaved("check", { password: "x", code: "" })).toBe(true);
    expect(hasUnsaved("check", { password: "", code: "1" })).toBe(true);
    expect(hasUnsaved("scan", empty)).toBe(true); // the new key is on screen
    expect(hasUnsaved("done", { password: "x", code: "1" })).toBe(false);
    expect(closeAsked({ busy: true, unsaved: false })).toBe("stay");
    expect(closeAsked({ busy: true, unsaved: true })).toBe("stay");
    expect(closeAsked({ busy: false, unsaved: true })).toBe("ask");
    expect(closeAsked({ busy: false, unsaved: false })).toBe("close");
    expect(leaveQuestion("check")).toContain("Two-step stays on your current device");
    expect(leaveQuestion("scan")).toContain("Two-step stays on your current device");
  });

  it("says each refusal in its own step's words, anything else as the console does", () => {
    expect(moveErrorMessage(refused, "check")).toBe(START_REFUSED);
    expect(moveErrorMessage(refused, "scan")).toBe(CONFIRM_REFUSED);
    expect(CONFIRM_REFUSED).toContain("nothing changed");
    expect(moveErrorMessage(new ApiError(401, "not_signed_in", ""), "scan")).toBe("Your session ended. Sign in again.");
    expect(moveErrorMessage(new TypeError("fetch failed"), "check")).toMatch(/internet connection/);
    expect(moveErrorMessage(new ApiError(429, "rate_limited", ""), "check")).toMatch(/Wait/);
  });

  it("uses calm words and the agreed success sentence", () => {
    expect(MOVE_TEXT.done).toBe(
      "Two-step moved. The new device is now the only one that works. Other browsers were signed out.",
    );
    expect(MOVE_TEXT.newCode).toBe("Enter the 6-digit code from the new device");
    const words = [...Object.values(MOVE_TEXT).flat(), START_REFUSED, CONFIRM_REFUSED, PASSWORD_MISSING, leaveQuestion("check"), leaveQuestion("scan")];
    for (const text of words) expect(text).not.toMatch(/can't|cannot|sorry|oops|turn off|disable/i);
  });

  it("starts with the password and the current code, labelled, then [Cancel] [Show the new key]", () => {
    const html = renderToStaticMarkup(createElement(MoveTwoStepDialog, { onClose: () => undefined }));
    expect(html).toContain(MOVE_TEXT.title);
    expect(html).toMatch(/<label for="move-password">Your password<\/label>/);
    expect(html).toMatch(/<input id="move-password"[^>]*type="password"/);
    expect(html).toMatch(/<label for="move-current-code">6-digit code from your current device<\/label>/);
    expect(html).toContain(`autoComplete="current-password"`);
    const cancel = html.indexOf(">Cancel</button>");
    const primary = html.indexOf(`${MOVE_TEXT.showKey}</button>`);
    expect(cancel).toBeGreaterThan(-1);
    expect(primary).toBeGreaterThan(cancel); // the primary last
    expect(html).toMatch(/<button type="submit" class="btn btn-primary[^"]*"[^>]*>[^<]*Show the new key/);
    expect(html).not.toContain("otpauth"); // no key before the password and code
  });
});
