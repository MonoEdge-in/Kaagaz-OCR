// PRD 7 PR 11a (#604): the self-grant password step. Asked first when the client knows it is a
// self-grant; asked on 403 password_required otherwise; a refused password keeps it open and sends
// nothing more; Cancel sends nothing.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { includesMe, passwordRetryText, withPassword } from "@/components/access/passwordStep";
import type { AskPassword } from "@/components/access/passwordStep";
import { SelfPasswordDialog } from "@/components/access/SelfPasswordDialog";
import { ApiError } from "@/lib/api";

const REQUIRED = new ApiError(403, "password_required", "Type your password to give yourself access.");
const WRONG = new ApiError(400, "wrong_password", "Your password is not right.");
const LOCKED = new ApiError(400, "password_locked", "Too many wrong passwords were tried on this account. Try again in 15 minutes.");

/** A fake API: the password "right" works, any other is refused; no password: 403 when `needs`. */
function fakeApi(needs: boolean) {
  const sent: (string | undefined)[] = [];
  const send = async (password?: string) => {
    sent.push(password);
    if (password === undefined) {
      if (needs) throw REQUIRED;
      return "done";
    }
    if (password !== "right") throw WRONG;
    return "done";
  };
  return { sent, send };
}

/** A fake dialog: types each password in turn while the step stays open, then cancels. */
function typing(...passwords: string[]): { ask: AskPassword; said: (string | null)[]; opened: () => number } {
  let opens = 0;
  const said: (string | null)[] = [];
  const ask: AskPassword = async (attempt) => {
    opens += 1;
    for (const password of passwords) {
      try {
        return await attempt(password);
      } catch (caught) {
        const text = passwordRetryText(caught);
        if (text === null) throw caught;
        said.push(text);
      }
    }
    return null; // Cancel
  };
  return { ask, said, opened: () => opens };
}

describe("withPassword", () => {
  it("asks first when the target is me, and sends once, with the password", async () => {
    const api = fakeApi(true);
    const step = typing("right");
    expect(await withPassword(api.send, step.ask, true)).toBe("done");
    expect(api.sent).toEqual(["right"]);
  });

  it("sends without asking when it is not a self-grant", async () => {
    const api = fakeApi(false);
    const step = typing("right");
    expect(await withPassword(api.send, step.ask, false)).toBe("done");
    expect(api.sent).toEqual([undefined]);
    expect(step.opened()).toBe(0);
  });

  it("on 403 password_required opens the step and sends again with the password", async () => {
    const api = fakeApi(true);
    const step = typing("right");
    expect(await withPassword(api.send, step.ask, false)).toBe("done");
    expect(api.sent).toEqual([undefined, "right"]);
  });

  it("keeps the step open after a wrong password, with its words, and sends nothing else", async () => {
    const api = fakeApi(true);
    const step = typing("wrong");
    expect(await withPassword(api.send, step.ask, true)).toBeNull();
    expect(api.sent).toEqual(["wrong"]);
    expect(step.said).toEqual(["That password isn't right. Try again."]);
  });

  it("says the server's lock words and stays open", () => {
    expect(passwordRetryText(LOCKED)).toBe(LOCKED.message);
    expect(passwordRetryText(new ApiError(409, "disabled", "x"))).toBeNull();
    expect(passwordRetryText(new TypeError("offline"))).toBeNull();
  });

  it("sends nothing on Cancel", async () => {
    const api = fakeApi(true);
    expect(await withPassword(api.send, typing().ask, true)).toBeNull();
    expect(api.sent).toEqual([]);
  });

  it("gives any other refusal to the caller, without asking", async () => {
    const step = typing("right");
    const refused = async () => {
      throw new ApiError(409, "disabled", "This person is switched off.");
    };
    await expect(withPassword(refused, step.ask, false)).rejects.toMatchObject({ code: "disabled" });
    expect(step.opened()).toBe(0);
  });

  it("asks first when a preview's gaining holds me", () => {
    const gaining = [{ id: "u2", name: "Amit" }, { id: "me", name: "Ravi" }];
    expect(includesMe(gaining, "me")).toBe(true);
    expect(includesMe(gaining, "u9")).toBe(false);
  });
});

describe("the dialog", () => {
  it("says what is given and that the email names no file, with Cancel before the action", () => {
    const html = renderToStaticMarkup(
      createElement(SelfPasswordDialog, { what: "Finance", attempt: async () => undefined, onCancel: () => undefined }),
    );
    expect(html).toContain("Confirm it&#x27;s you");
    expect(html).toContain("You&#x27;re giving yourself access to Finance.");
    expect(html).toContain("The email doesn&#x27;t name any file.");
    expect(html).toContain("Your Kaagaz password");
    expect(html).toContain('autoComplete="current-password"');
    expect(html.indexOf(">Cancel</button>")).toBeLessThan(html.indexOf("Give myself access</button>"));
    expect(html.match(/btn-primary/g)).toHaveLength(1);
  });
});
