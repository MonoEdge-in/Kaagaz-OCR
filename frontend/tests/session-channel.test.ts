// Critical #5: tabs of one browser share one session cookie. A tab that signs
// out or in says who is signed in now, so the other tabs can leave the
// screens of whoever was signed in before. Node has BroadcastChannel too:
// `other` stands in for another tab.

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { sessionErrorMessage } from "@/lib/session";
import { SESSION_CHANNEL, announceSession, leavePage, watchSession } from "@/lib/sessionChannel";

// QA 17 Oct: a 429 while asking who is signed in said "not answering"; it is only busy.
describe("the words when the session cannot be loaded", () => {
  it("says busy for too many requests, not answering for other server errors", () => {
    expect(sessionErrorMessage(new ApiError(429, "rate_limited", "Slow down"))).toBe(
      "Kaagaz is busy right now. Wait a minute, then try again.",
    );
    expect(sessionErrorMessage(new ApiError(500, "error", "x"))).toBe("Kaagaz is not answering right now. Try again in a minute.");
  });
});

describe("the session channel between tabs", () => {
  const stops: (() => void)[] = [];
  afterEach(() => {
    for (const stop of stops.splice(0)) stop();
  });

  function listen(): (string | null)[] {
    const heard: (string | null)[] = [];
    stops.push(watchSession((userId) => heard.push(userId)));
    return heard;
  }

  function otherTab(): { channel: BroadcastChannel; heard: unknown[] } {
    const channel = new BroadcastChannel(SESSION_CHANNEL);
    const heard: unknown[] = [];
    channel.onmessage = (event: MessageEvent<unknown>) => heard.push(event.data);
    stops.push(() => channel.close());
    return { channel, heard };
  }

  it("is called kaagaz-session", () => {
    expect(SESSION_CHANNEL).toBe("kaagaz-session");
  });

  it("tells the other tabs who is signed in now, or nobody", async () => {
    const other = otherTab();
    announceSession("u2");
    announceSession(null);
    await expect.poll(() => other.heard).toEqual([{ userId: "u2" }, { userId: null }]);
  });

  it("hears the other tabs, and ignores anything else on the channel", async () => {
    const heard = listen();
    const other = otherTab();
    for (const data of ["u2", { userId: 7 }, {}, { userId: "u3" }]) other.channel.postMessage(data);
    await expect.poll(() => heard).toEqual(["u3"]);
  });

  // The tab that signs out goes to its own sign-in page; hearing itself would
  // send it somewhere else as well (the admin sign-in, from /admin).
  it("never hears its own announcement", async () => {
    const heard = listen();
    const other = otherTab();
    announceSession(null);
    other.channel.postMessage({ userId: "u4" });
    await expect.poll(() => heard).toEqual(["u4"]);
    expect(other.heard).toEqual([{ userId: null }]);
  });

  it("hears nothing once stopped", async () => {
    const heard: (string | null)[] = [];
    const stop = watchSession((userId) => heard.push(userId));
    stop();
    const still = listen();
    otherTab().channel.postMessage({ userId: "u5" });
    await expect.poll(() => still).toEqual(["u5"]);
    expect(heard).toEqual([]);
  });
});

// A late reply saying the session has ended must not send a tab that is
// already signing out somewhere else: the first page it leaves for wins.
describe("leavePage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads the first page asked for, and ignores any later one", () => {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { replace } });
    leavePage("/login?signed_out=1");
    leavePage("/login?ended=1");
    expect(replace.mock.calls).toEqual([["/login?signed_out=1"]]);
  });
});
