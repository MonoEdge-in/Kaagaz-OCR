// PRD 7 PR 11a (#604): the self-grant password step. Asked first when the client knows it is a
// self-grant; asked on 403 password_required otherwise; a refused password keeps it open and sends
// nothing more; Cancel sends nothing.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { asksFirst, includesMe, passwordRetryText, stepAttempt, withPassword } from "@/components/access/passwordStep";
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

describe("stepAttempt, the open step's send", () => {
  function step(outcome: () => Promise<string>) {
    const seen: string[] = [];
    const send = stepAttempt(outcome, {
      close: () => seen.push("close"),
      resolve: (reply) => seen.push(`resolve ${reply}`),
      reject: (caught) => seen.push(`reject ${(caught as ApiError).code}`),
    });
    return { send, seen };
  }

  it("stays open after a wrong password: it throws for the dialog to say, and closes nothing", async () => {
    const { send, seen } = step(async () => {
      throw WRONG;
    });
    await expect(send("wrong")).rejects.toBe(WRONG);
    expect(seen).toEqual([]);
    expect(passwordRetryText(WRONG)).toBe("That password isn't right. Try again.");
  });

  it("stays open on the sign-in lock, with the server's lock words", async () => {
    const { send, seen } = step(async () => {
      throw LOCKED;
    });
    await expect(send("x")).rejects.toBe(LOCKED);
    expect(seen).toEqual([]);
    expect(passwordRetryText(LOCKED)).toBe("Too many wrong passwords were tried on this account. Try again in 15 minutes.");
  });

  it("closes and rejects the caller's wait on any other failure", async () => {
    const { send, seen } = step(async () => {
      throw new ApiError(409, "disabled", "This person is switched off.");
    });
    await expect(send("right")).resolves.toBeUndefined();
    expect(seen).toEqual(["close", "reject disabled"]);
  });

  it("closes and resolves with the reply on success", async () => {
    const { send, seen } = step(async () => "done");
    await send("right");
    expect(seen).toEqual(["close", "resolve done"]);
  });
});

describe("asksFirst", () => {
  it("asks first for a give or a tick turned on for me, never for someone else or a tick taken away", () => {
    expect(asksFirst({ myId: "me", targetId: "me" })).toBe(true); // a department, folder or file
    expect(asksFirst({ myId: "me", targetId: "me", on: true })).toBe(true); // Head or Management on
    expect(asksFirst({ myId: "me", targetId: "me", on: false })).toBe(false);
    expect(asksFirst({ myId: "me", targetId: "u2" })).toBe(false);
    expect(asksFirst({ myId: "me", targetId: "u2", on: true })).toBe(false);
  });

  it("asks first when a preview's gaining holds me", () => {
    expect(asksFirst({ myId: "me", gaining: [{ id: "u2", name: "Amit" }, { id: "me", name: "Ravi" }] })).toBe(true);
    expect(asksFirst({ myId: "me", gaining: [{ id: "u2", name: "Amit" }] })).toBe(false);
    expect(asksFirst({ myId: "me", gaining: [] })).toBe(false);
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
