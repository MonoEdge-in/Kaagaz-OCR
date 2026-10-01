// Critical #5: tabs of one browser share one session cookie. A tab that signs
// out or in says who is signed in now, so the other tabs can leave the
// screens of whoever was signed in before. Node has BroadcastChannel too.

import { afterEach, describe, expect, it } from "vitest";
import { SESSION_CHANNEL, announceSession, watchSession } from "@/lib/sessionChannel";

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

  it("is called kaagaz-session", () => {
    expect(SESSION_CHANNEL).toBe("kaagaz-session");
  });

  it("tells the other tabs who is signed in now, or nobody", async () => {
    const heard = listen();
    announceSession("u2");
    announceSession(null);
    await expect.poll(() => heard).toEqual(["u2", null]);
  });

  it("ignores anything else on the channel", async () => {
    const heard = listen();
    const other = new BroadcastChannel(SESSION_CHANNEL);
    other.postMessage("u2");
    other.postMessage({ userId: 7 });
    other.postMessage({});
    other.close();
    announceSession("u3");
    await expect.poll(() => heard).toEqual(["u3"]);
    announceSession("u4");
    await expect.poll(() => heard).toEqual(["u3", "u4"]);
  });

  it("hears nothing once stopped", async () => {
    const heard: (string | null)[] = [];
    const stop = watchSession((userId) => heard.push(userId));
    stop();
    const still = listen();
    announceSession("u5");
    await expect.poll(() => still).toEqual(["u5"]);
    expect(heard).toEqual([]);
  });
});
